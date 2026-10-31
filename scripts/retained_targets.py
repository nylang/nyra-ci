"""Qualify target runtimes using exact retained native SDKs, without rebuilding tools."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from build_sdk import HOSTS, cache_root, run
from release_common import ROOT, check_sdk, checkout, safe_name, sha, spec, write
from runner_inputs import verify_build_tools, verify_providers


def verify_native(directory, host, plan, pin=None):
    """Admit only the native archive already qualified for this frozen source."""
    if host not in HOSTS:
        raise ValueError('unsupported retained native host')
    receipt_file = directory / ('release-' + host + '.json')
    receipt = json.loads(receipt_file.read_text())
    if type(receipt.get('success')) is not bool or (receipt.get('host'), receipt.get('version'), receipt.get('source_commit'),
            receipt.get('success'), receipt.get('validation_policy')) != (
            host, plan['version'], plan['source_commit'], True, 'g1-g2-v1'):
        raise ValueError('retained native qualification identity mismatch')
    name = 'nyra-sdk-' + plan['version'] + '-' + host + ('.zip' if host.startswith('windows-') else '.tar.gz')
    path = directory / name
    if path.is_symlink() or not path.is_file() or receipt['archive'] != dict(
            path=name, sha256=sha(path), bytes=path.stat().st_size):
        raise ValueError('retained native archive changed')
    if pin is not None and (sha(path), sha(receipt_file), receipt['sdk_manifest_sha256']) != (
            pin['archive_sha256'], pin['receipt_sha256'], pin['sdk_manifest_sha256']):
        raise ValueError('managed native import differs from the reviewed pin')
    return path, receipt


def retained_matrix(config, run_id):
    labels = {
        'darwin-arm64': ['self-hosted', 'macOS', 'ARM64', 'nyra-sdk', 'nyra-macos-arm64'],
        'darwin-x86_64': ['self-hosted', 'macOS', 'X64', 'nyra-sdk', 'nyra-macos-x86_64'],
        'linux-x86_64': ['self-hosted', 'Linux', 'X64', 'nyra-sdk', 'nyra-linux-x64'],
        'windows-x86_64': ['self-hosted', 'Windows', 'X64', 'nyra-sdk', 'nyra-windows-x64']}
    managed = config.get('managed_imports', {})
    if set(managed) - set(HOSTS):
        raise ValueError('unknown managed native import host')
    return {'include': [dict(host=host, labels=labels[host],
        native_run='' if host in managed else run_id) for host in HOSTS]}


def stage_git_headers(sdk, bootstrap):
    """Compile-only Git bridge work needs headers, not another libgit2 build."""
    metadata = json.loads((sdk / 'share/nyra/package-manager.json').read_text())
    version = metadata['libgit2']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('invalid libgit2 corresponding source version')
    archive = (sdk / safe_name(metadata['corresponding_source']['path'])).resolve()
    if not archive.is_relative_to(sdk.resolve()) or sha(archive) != metadata['corresponding_source']['sha256']:
        raise ValueError('retained libgit2 corresponding source changed')
    destination = bootstrap / 'package-manager' / ('libgit2-' + version) / 'include'
    with tarfile.open(archive) as bundle:
        public = [row.name for row in bundle.getmembers() if row.isfile() and row.name.endswith('/include/git2.h')]
        if len(public) != 1:
            raise ValueError('ambiguous libgit2 public include root')
        prefix = public[0][:-len('git2.h')]
        members = [row for row in bundle.getmembers()
                   if row.isfile() and row.name.startswith(prefix) and row.name.endswith('.h')]
        for row in members:
            name = row.name[len(prefix):]
            target = destination / safe_name(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(row) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
    if not (destination / 'git2.h').is_file():
        raise ValueError('libgit2 corresponding source has no public headers')


def produce():
    host = os.environ['NYRA_CI_HOST']
    plan = spec(os.environ['NYRA_CI_RELEASE_TAG'])
    catalog = json.loads((ROOT / 'qualified-inputs' / (plan['version'] + '.json')).read_text())
    if catalog['source_commit'] != plan['source_commit']:
        raise ValueError('retained input plan differs from the frozen source')
    imported = catalog.get('managed_imports', {}).get(host)
    native = cache_root() / 'nyra-prequalified' / plan['version'] / host if imported else ROOT / 'native-input'
    archive, original = verify_native(native, host, plan, imported)
    work = cache_root() / 'nyra-release-work' / (
        os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + host)
    work.mkdir(parents=True, exist_ok=False)
    state = dict(original, workspace=str(work), success=False,
                 native_qualification_origin='managed-local-import' if imported else 'github-actions',
                 native_qualification_run=None if imported else catalog['native_qualification_run'],
                 native_qualification_control=catalog['native_control_commit'], published=False,
                 compiler_rebuilt=False, complete_native_suites_repeated=False)
    write(work / 'owner.json', dict(owner='retained ' + plan['version'] + ' target qualification',
          reserve_bytes=80 * 1024**3, expected_peak_bytes=12 * 1024**3,
          cleanup='retain_assembly after artifact upload; bounded failed diagnostics'))
    try:
        source = work / 'source'
        checkout(plan['source_commit'], source)
        modules = subprocess.check_output(['git', 'config', '--file', '.gitmodules',
            '--get-regexp', r'submodule\..*\.path'], cwd=source, text=True)
        paths = [line.split(None, 1)[1] for line in modules.splitlines()
                 if line.split(None, 1)[1].startswith('third_party/')]
        run(['git', 'submodule', 'update', '--init', '--recursive', '--', *paths], source)
        sys.path.insert(0, str(source))
        from tools.build.bootstrap_seed import _extract
        from tools.build.distribution import validate_public
        from tools.dev.workspace import create_snapshot
        from tools.ci.runner_storage import check_space
        check_space(work)
        extraction = work / 'native-sdk'
        extraction.mkdir()
        sdk = _extract(archive, extraction, 'zip' if host.startswith('windows-') else 'tar.gz')
        if sha(sdk / 'manifest.json') != original['sdk_manifest_sha256']:
            raise ValueError('retained SDK manifest changed')
        manifest = validate_public(sdk)
        check_sdk(manifest, plan, host)
        state.update(source=str(source), sdk=str(sdk))
        state.update(verify_providers(host))
        environment = verify_build_tools(host, sdk)
        os.environ.update(environment)
        bootstrap = work / 'bootstrap'
        (bootstrap / 'native-generations').mkdir(parents=True)
        create_snapshot(source, bootstrap / 'native-generations/snapshot', bootstrap / 'platform-libs', regression=False)
        stage_git_headers(sdk, bootstrap)
        # runtime_targets requires a successfully admitted native SDK; that scope
        # is separate from completion of this job's new target runtime work.
        write(ROOT / 'build-receipt.json', dict(state, success=True))
        from runtime_targets import produce as targets
        targets()
        check_space(work)
        public = ROOT / 'public-output'
        shutil.rmtree(public, ignore_errors=True)
        public.mkdir()
        shutil.copy2(archive, public / archive.name)
        shutil.copy2(native / ('release-' + host + '.json'), public)
        state['success'] = True
    finally:
        evidence = ROOT / 'qualification-evidence'
        shutil.rmtree(evidence, ignore_errors=True)
        evidence.mkdir()
        remaining = 16 * 1024**2
        for path in sorted((work / 'runtimes/consumers').rglob('*')):
            if path.is_file() and path.suffix in ('.json', '.log') and remaining:
                data = path.read_bytes()[-min(remaining, 1024**2):]
                target = evidence / path.relative_to(work / 'runtimes')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                remaining -= len(data)
        write(ROOT / 'build-receipt.json', state)
        if state['success']:
            from release_common import state_path
            write(state_path(host), state)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    if args.plan:
        plan = spec(os.environ['NYRA_CI_RELEASE_TAG'])
        config = json.loads((ROOT / 'qualified-inputs' / (plan['version'] + '.json')).read_text())
        if config['source_commit'] != plan['source_commit']:
            raise ValueError('retained input source mismatch')
        from publish_release import Github
        repository = os.environ['GITHUB_REPOSITORY']
        run_id = str(config['native_qualification_run'])
        if not run_id.isdigit():
            raise ValueError('invalid native qualification run')
        native = Github(repository, os.environ['GITHUB_TOKEN']).api('/actions/runs/' + run_id)
        workflow = config.get('native_qualification_workflow', '.github/workflows/sdk-build.yml')
        if workflow not in ('.github/workflows/sdk-build.yml', '.github/workflows/admit-native.yml'):
            raise ValueError('unreviewed native qualification workflow')
        if (native['path'], native['head_sha'], native['conclusion'], native['head_repository']['full_name']) != (
                workflow, config['native_control_commit'], 'success', repository):
            raise ValueError('native qualification workflow identity or completion mismatch')
        matrix = retained_matrix(config, run_id)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write('matrix=' + json.dumps(matrix) + '\n')
    else:
        produce()
