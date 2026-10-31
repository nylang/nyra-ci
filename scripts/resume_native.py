"""Continue approved, hash-identical native stages; never rerun G1/G2/regression."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from build_sdk import cache_root, run, seed_sdk
from release_common import ROOT, checkout, sha, write
from runner_inputs import verify_build_tools, verify_providers

TEST_PATH = 'source/nyra/private/tests/lowering_selftest.ny'


def corrected_test(original, corrected):
    start = original.index('@test(tag = "core-lowering") func test_production_heap_binding_excludes_unselected_test_provider()')
    expected = original[:start] + original[start:].replace(
        '  var active: Program = Program.Empty();',
        '  var active: Program = Program.Empty();\n  CheckedUnit.BeginAt(&mut active);', 1).replace(
        '  var test_only: Program = Program.Empty();',
        '  var test_only: Program = Program.Empty();\n  CheckedUnit.BeginAt(&mut test_only);', 1)
    if corrected != expected:
        raise ValueError('continuation changes more than the reviewed test-body initialization')
    header = original.index(' {\n', start) + 3
    return hashlib.sha256((original[:header] + '<production-ignored test body>\n}\n').encode()).hexdigest()


def validate_original(report, regression, config, host, compiler, platform):
    if (report.get('source_revision') != config['actual_product_build_source_commit']
            or report.get('source_worktree_clean') is not True
            or report.get('validation_policy') != 'g1-g2-v1'
            or report.get('fixed_point') != dict(identical=True,
                G1_sha256=config['hosts'][host], G2_sha256=config['hosts'][host])
            or sha(compiler) != config['hosts'][host]
            or sha(platform) != report['native_inputs']['platform_sha256']):
        raise ValueError('original native fixed-point/input identity mismatch')
    if (regression.get('compiler_sha256') != config['hosts'][host]
            or regression.get('total') != 536 or regression.get('passed') != 536
            or regression.get('failed') != 0 or len(regression.get('cases', [])) != 536
            or any(row.get('ok') is not True for row in regression['cases'])):
        raise ValueError('original complete regression gate did not pass')


def retain_git_backend(build, cache, host):
    metadata = build / 'ngit-build.json'
    backend = json.loads(metadata.read_text())
    root = cache / 'nyra-providers/ngit-0.2.0-libgit2-1.9.7'
    root.mkdir(parents=True, exist_ok=True)
    libraries = {}
    for name, digest in backend['native_libraries'].items():
        source = Path(name)
        if sha(source) != digest:
            raise ValueError('Git provider changed before retention')
        if source.name in ('libngit.a', 'libgit2.a', 'git2.lib'):
            target = root / source.name
            if not target.exists():
                shutil.copy2(source, target)
            if sha(target) != digest:
                raise ValueError('managed Git provider differs from accepted backend')
        else:
            target = source
        libraries[str(target.resolve())] = digest
    if sum(Path(name).parent == root for name in libraries) != 2:
        raise ValueError('incomplete independent Git provider retention')
    keys = ('package', 'version', 'libgit2', 'bridge_source_sha256', 'windows_paths_source_sha256',
            'openssl_version', 'source_patches', 'system_libraries')
    write(root / 'provider.json', {k: backend[k] for k in keys} | dict(
        host=host, native_libraries=libraries, origin_sha256=sha(metadata),
        cleanup='Keep one managed native provider while matching independent ngit consumers need it.'))


def resume():
    version = os.environ['NYRA_CI_RELEASE_TAG'].removeprefix('v')
    config = json.loads((ROOT / 'scope-continuations' / (version + '.json')).read_text())
    host = os.environ['NYRA_CI_HOST']
    if config['schema_version'] != 1 or host not in config['hosts']:
        raise ValueError('unsupported approved native continuation')
    cache = cache_root()
    original = ROOT / 'continuation-input'
    original_receipt = json.loads((original / 'receipt/build-receipt.json').read_text())
    if (original_receipt.get('host'), original_receipt.get('source_commit'), original_receipt.get('success')) != (
            host, config['actual_product_build_source_commit'], False):
        raise ValueError('original workflow receipt differs from reviewed continuation')
    evidence = original / 'evidence'
    report = json.loads((evidence / 'bootstrap-report.json').read_text())
    regression_file = evidence / 'native-generations/stage-2/regression/report.json'
    regression = json.loads(regression_file.read_text())
    checkpoint = cache / 'nyra-release-checkpoints' / version / (config['original_run'] + '-1-' + host)
    suffix = '.exe' if host.startswith('windows-') else ''
    compiler = checkpoint / ('nyrac' + suffix)
    platform = checkpoint / 'libnyra_platform.a'
    validate_original(report, regression, config, host, compiler, platform)
    work = cache / 'nyra-release-work' / (os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'] + '-' + host)
    work.mkdir(parents=True, exist_ok=False)
    state = dict(host=host, source_commit=config['release_source_commit'], actual_product_build_source_commit=report['source_revision'],
                 workspace=str(work), execution_mode=original_receipt['execution_mode'], success=False, published=False,
                 original_qualification_run=config['original_run'], compiler_rebuilt=False, complete_regression_repeated=False)
    try:
        state.update(verify_providers(host))
        sdk = seed_sdk(host)
        env = verify_build_tools(host, sdk)
        source = work / 'source'
        checkout(report['source_revision'], source)
        from source_access import source_environment
        run(['git', 'fetch', '--depth', '1', 'origin', config['release_source_commit']], source, source_environment())
        names = subprocess.check_output(['git', 'diff', '--name-only', report['source_revision'], config['release_source_commit']], cwd=source, text=True).splitlines()
        if TEST_PATH not in names or any(name != TEST_PATH and not name.startswith('data/') for name in names):
            raise ValueError('unreviewed source changes prevent stage reuse')
        paths = subprocess.check_output(['git', 'config', '--file', '.gitmodules', '--get-regexp', r'submodule\..*\.path'], cwd=source, text=True)
        run(['git', 'submodule', 'update', '--init', '--recursive', '--',
            *[row.split(None, 1)[1] for row in paths.splitlines() if row.split(None, 1)[1].startswith('third_party/')]], source)
        sys.path.insert(0, str(source))
        from tools.build.bootstrap import _collect_release_artifacts, _native_compiler_environment, _prepare_release_kit, _run_unit_suites
        from tools.build.generation_evidence import tree_digest
        from tools.build.validation import COMPILER_UNITS, check_units
        from tools.dev.workspace import create_snapshot
        from tools.ci.runner_storage import check_space
        check_space(work)
        bootstrap = work / 'bootstrap'
        native = bootstrap / 'native-generations'
        native.mkdir(parents=True)
        platform_root = bootstrap / 'platform-libs'
        (platform_root / host).mkdir(parents=True)
        shutil.copy2(platform, platform_root / host / platform.name)
        snapshot = native / 'snapshot'
        create_snapshot(source, snapshot, platform_root, regression=True, bootstrap_provenance=report['bootstrap_sdk'])
        for name, digest in report['source_inputs'].items():
            if sha(snapshot / name) != digest:
                raise ValueError('reconstructed original source input differs: ' + name)
        env.update(_native_compiler_environment(Path(env['LLVM_PREFIX']), env))
        env.update(NYRA_LIB_ROOT=str(platform_root))
        env = _prepare_release_kit(bootstrap, env, 7200)
        actual = dict(CC_sha256=sha(Path(env['CC'])), AR_sha256=sha(Path(env['AR'])),
                      LLVM_sha256=sha(Path(env['NYRA_LLVM_STATIC_LIB'])), platform_sha256=sha(platform))
        if actual != report['native_inputs']:
            raise ValueError('reconstructed native inputs differ')
        builder = Path(report['seed'])
        if sha(builder) != report['seed_compiler_sha256']:
            raise ValueError('selected original G0 changed')
        view = bootstrap / 'corrected-nyra-inputs'
        shutil.copytree(snapshot, view, symlinks=True)
        corrected = subprocess.check_output(['git', 'show', config['release_source_commit'] + ':' + TEST_PATH], cwd=source)
        projected_test = corrected_test((snapshot / TEST_PATH).read_text(), corrected.decode())
        (view / TEST_PATH).write_bytes(corrected)
        suites = []
        for name in COMPILER_UNITS:
            prior = evidence / 'units' / name / 'report.json'
            if name != 'nyra' and prior.exists():
                row = dict(package=name, summary=json.loads(prior.read_text()))
                check_units(dict(status='complete', compiler_sha256=sha(compiler), suites=[row], passed=row['summary']['passed']), {name}, sha(compiler))
                row['provenance'] = dict(mode='retained complete suite', original_report_sha256=sha(prior))
            else:
                result = _run_unit_suites(compiler, view if name == 'nyra' else snapshot,
                    bootstrap / 'continued-units' / name, env, 7200, packages=(name,))
                row = result['suites'][0]
                row['provenance'] = dict(mode='complete suite execution')
            suites.append(row)
            write(bootstrap / 'scope-progress.json', dict(suites=suites))
        units = dict(status='complete', compiler_sha256=sha(compiler), suites=suites,
            passed=sum(row['summary']['passed'] for row in suites), ignored=sum(row['summary']['ignored'] for row in suites))
        check_units(units, set(COMPILER_UNITS), sha(compiler))
        write(bootstrap / 'unit-report.json', units)
        write(bootstrap / 'artifacts/share/nyra/validation/compiler-regression.json', dict(
            compiler_sha256=sha(compiler), total=536, passed=536, failed=0,
            suite_inventory_sha256=tree_digest(snapshot / 'tests/regression')))
        report.update(unit_acceptance=units, full_unit=True)
        write(bootstrap / 'bootstrap-report.json', report)
        report['artifacts'] = _collect_release_artifacts(compiler, snapshot, bootstrap,
            platform_root / host / platform.name, env, 7200, builder=builder, qualified=True)
        write(bootstrap / 'bootstrap-report.json', report)
        retain_git_backend(bootstrap / 'package-manager', cache, host)
        from tools.build.sdk import assemble
        from tools.build.distribution import export_public
        candidate = work / 'sdk'
        candidate.mkdir()
        manifest = assemble(bootstrap, candidate, work / 'sdk.validation', candidate=False)
        public = export_public(candidate, work / 'public-release', candidate=False, plan_only=False)
        payload = Path(public['sdk'])
        meta = json.loads((payload / 'manifest.json').read_text())
        projected = report['source_inputs'].copy()
        projected[TEST_PATH] = projected_test
        proof = dict(kind='approved-platform-effective-input-reuse-v1', status='complete', host=host,
            approval=config['approval'], actual_product_build_source_commit=report['source_revision'],
            release_source_commit=config['release_source_commit'], original_run=config['original_run'],
            original_report_sha256=sha(evidence / 'bootstrap-report.json'), original_regression_report_sha256=sha(regression_file),
            native_inputs=actual, compiler_effective_inputs_sha256=hashlib.sha256(json.dumps(projected, sort_keys=True).encode()).hexdigest(),
            body_exclusion_authority=dict(path='source/frontend/private/semantic/check.ny', sha256=sha(snapshot / 'source/frontend/private/semantic/check.ny')),
            tools={name: row['sha256'] for name, row in meta['tools'].items()},
            frontend_gate=next(row['summary'] for row in suites if row['package'] == 'frontend'),
            nyra_gate=next(row['summary'] for row in suites if row['package'] == 'nyra'),
            compiler_unit_count=units['passed'],
            tool_unit_counts={name: row['units']['passed'] for name, row in report['artifacts']['tool_acceptance'].items()},
            compiler_rebuilt=False, complete_regression_repeated=False, published=False)
        archive = Path(public['archive'])
        output = ROOT / 'public-output'
        output.mkdir(exist_ok=False)
        shutil.copy2(archive, output / archive.name)
        release = dict(host=host, source_commit=config['release_source_commit'], actual_product_build_source_commit=meta['source_revision'],
            version=version, validation_policy='g1-g2-v1', tools=meta['tools'], execution_mode=state['execution_mode'], success=True,
            archive=dict(path=archive.name, sha256=sha(archive), bytes=archive.stat().st_size),
            sdk_manifest_sha256=sha(payload / 'manifest.json'), effective_input_equivalence=proof, published=False)
        write(output / ('release-' + host + '.json'), release)
        write(bootstrap / 'effective-input-equivalence.json', proof)
        state.update(success=True, source=str(source), sdk=str(candidate), version=version,
            tools=meta['tools'], archive=archive.name, archive_sha256=sha(archive), sdk_manifest_sha256=sha(payload / 'manifest.json'),
            validation_policy='g1-g2-v1', bootstrap_report=str(bootstrap / 'bootstrap-report.json'))
    finally:
        logs = ROOT / 'qualification-evidence'
        logs.mkdir(exist_ok=True)
        remaining = 16 * 1024**2
        for path in sorted((work / 'bootstrap').rglob('*')):
            relative = path.relative_to(work / 'bootstrap')
            if path.is_file() and path.suffix in ('.json', '.log') and 'snapshot' not in relative.parts and 'corrected-nyra-inputs' not in relative.parts and remaining:
                data = path.read_bytes()[-min(remaining, 1024**2):]
                target = logs / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                remaining -= len(data)
        write(ROOT / 'build-receipt.json', state)


if __name__ == '__main__':
    resume()
