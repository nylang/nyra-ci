"""Qualify one frozen native SDK; export the exact accepted products once.

The compiler uses G1/G2 and one G2 suite pass. Tools are built/tested once by
bootstrap. Publication never calls this module's build entry point.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from source_access import SOURCE_URL, source_environment
from release_common import ROOT, sha, write

HOSTS = ('darwin-arm64', 'darwin-x86_64', 'linux-x86_64', 'windows-x86_64')


def cache_root():
    return Path(os.environ.get('NYRA_CI_CACHE_DIRECTORY') or os.environ['RUNNER_TOOL_CACHE'])


def run(command, cwd=None, env=None):
    print('BEGIN ' + ' '.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), cwd=cwd, env=env, check=True)


def seed_sdk(host):
    config = json.loads((ROOT / 'sdk-seeds.json').read_text())
    asset = config['hosts'][host]
    cache = cache_root() / 'nyra-sdk' / config['version'] / host
    sdk = cache / 'sdk'
    marker = cache / 'verified-archive.json'
    if sdk.is_dir() and marker.is_file() and json.loads(marker.read_text()) == asset:
        return sdk
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / asset['file']
    if not archive.is_file() or sha(archive) != asset['sha256']:
        partial = archive.with_suffix(archive.suffix + '.partial')
        request = urllib.request.Request(asset['url'], headers={'User-Agent': 'Nyra-private-CI'})
        with urllib.request.urlopen(request, timeout=300) as source, partial.open('wb') as target:
            shutil.copyfileobj(source, target)
        if sha(partial) != asset['sha256']:
            raise ValueError('pinned SDK seed checksum mismatch')
        partial.replace(archive)
    stage = cache / 'extract'
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir()
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as source:
            source.extractall(stage)
    else:
        with tarfile.open(archive) as source:
            source.extractall(stage, filter='data')
    roots = [p.parent for p in stage.glob('*/manifest.json')]
    if len(roots) != 1:
        raise ValueError('ambiguous SDK archive layout')
    shutil.rmtree(sdk, ignore_errors=True)
    for attempt in range(4):
        try:
            roots[0].rename(sdk)
            break
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(attempt + 1)
    shutil.rmtree(stage)
    write(marker, asset)
    return sdk


def native_environment(sdk, host):
    environment = {k: v for k, v in os.environ.items() if not k.startswith('NYRA')}
    llvm = sdk / 'hosts' / host / 'llvm'
    provider = cache_root() / 'nyra-providers' / 'openssl-3.5.9'
    if not (provider / 'include/openssl/ssl.h').is_file():
        raise ValueError('missing managed static OpenSSL provider: ' + str(provider))
    environment.update(LLVM_PREFIX=str(llvm), NYRA_GIT_OPENSSL_PREFIX=str(provider),
                       PATH=str(llvm / 'bin') + os.pathsep + environment.get('PATH', ''))
    if host.startswith('darwin-'):
        environment['MACOSX_DEPLOYMENT_TARGET'] = '14.0'
    if host == 'windows-x86_64':
        cmake = Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'Microsoft Visual Studio/2022/BuildTools/Common7/IDE/CommonExtensions/Microsoft/CMake'
        environment['PATH'] = os.pathsep.join([str(cmake / 'CMake/bin'), str(cmake / 'Ninja'), environment['PATH']])
    if host.startswith(('darwin-', 'linux-')):
        environment['NYRA_REGRESSION_MIN_TIMEOUT'] = '1800' if host.startswith('linux-') else '600'
    return environment


def build():
    host = os.environ['NYRA_CI_HOST']
    if host not in HOSTS:
        raise ValueError('unknown native host')
    ref = os.environ.get('NYRA_CI_SOURCE_REF', 'main')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', ref):
        raise ValueError('invalid source reference')
    work = cache_root() / 'nyra-release-work' / (os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + host)
    work.mkdir(parents=True, exist_ok=False)
    receipt = dict(host=host, source_ref=ref, workspace=str(work), success=False,
                   execution_mode=os.environ.get('NYRA_CI_EXECUTION_MODE', 'native'), published=False)
    try:
        sdk = seed_sdk(host)
        source = work / 'source'
        run(['git', 'init', source])
        run(['git', 'remote', 'add', 'origin', SOURCE_URL], source)
        run(['git', 'fetch', '--depth', '1', 'origin', ref], source, source_environment())
        run(['git', 'checkout', '--detach', 'FETCH_HEAD'], source)
        # Initialize all declared public dependencies, including ngit and TLS.
        # The tracked bootstrap SDK is supplied by the pinned release instead.
        modules = subprocess.check_output(['git', 'config', '--file', '.gitmodules', '--get-regexp', r'submodule\..*\.path'], cwd=source, text=True)
        paths = [line.split(None, 1)[1] for line in modules.splitlines() if line.split(None, 1)[1].startswith('third_party/')]
        run(['git', 'submodule', 'update', '--init', '--recursive', '--', *paths], source)
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
        receipt['source_commit'] = commit
        tag = os.environ.get('NYRA_CI_RELEASE_TAG')
        if tag:
            from release_common import spec, check_source
            check_source(source, spec(tag))
        from runner_inputs import verify_providers, verify_build_tools
        receipt.update(verify_providers(host))
        environment = verify_build_tools(host, sdk)
        suffix = '.exe' if host.startswith('windows-') else ''
        compiler = sdk / 'hosts' / host / 'bin' / ('nyrac' + suffix)
        bootstrap = work / 'bootstrap'
        jobs = '1' if host.startswith('darwin-') else '2' if host.startswith('linux-') else '4'
        run([sys.executable, '-B', 'build.py', 'bootstrap', '--bootstrap-sdk', sdk,
             '--out-dir', bootstrap, '-j', jobs,
             '--timeout', '7200', '--regression'], source, environment)
        candidate = work / 'sdk'
        run([sys.executable, '-B', 'build.py', 'sdk', '--input-dir', bootstrap,
             '--out-dir', candidate], source, environment)
        sys.path.insert(0, str(source))
        from tools.build.sdk_manifest import validate_sdk
        from tools.build.distribution import export_public
        manifest = validate_sdk(candidate)
        from release_common import check_sdk
        check_sdk(manifest, {'version': manifest['version'], 'source_commit': commit}, host)
        public = export_public(candidate, work / 'public-release', candidate=False, plan_only=False)
        archive = Path(public['archive'])
        output = ROOT / 'public-output'
        shutil.rmtree(output, ignore_errors=True)
        output.mkdir()
        shutil.copy2(archive, output / archive.name)
        receipt.update(success=True, sdk=str(candidate), source=str(source), bootstrap_report=str(bootstrap / 'bootstrap-report.json'),
                       validation_policy='g1-g2-v1', archive=archive.name, archive_sha256=sha(archive),
                       version=manifest['version'], tools=manifest['tools'])
        write(output / ('release-' + host + '.json'), {k: receipt[k] for k in
              ('host', 'source_commit', 'version', 'validation_policy', 'tools', 'execution_mode', 'success')} | {
                  'archive': {'path': archive.name, 'sha256': sha(archive), 'bytes': archive.stat().st_size},
                  'sdk_manifest_sha256': sha(Path(public['sdk']) / 'manifest.json')})
    finally:
        evidence = ROOT / 'qualification-evidence'
        shutil.rmtree(evidence, ignore_errors=True)
        evidence.mkdir()
        bootstrap = work / 'bootstrap'
        if bootstrap.is_dir():
            for path in bootstrap.rglob('*'):
                relative = path.relative_to(bootstrap)
                if path.is_file() and path.suffix in ('.json', '.log') and 'snapshot' not in relative.parts:
                    target = evidence / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
        write(ROOT / 'build-receipt.json', receipt)


if __name__ == '__main__':
    build()
