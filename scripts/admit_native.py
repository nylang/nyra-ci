"""Admit exact SDK bytes with complete existing gates; execute no compiler suite."""
import json
import os
from pathlib import Path
import re
from build_sdk import HOSTS, cache_root
from release_common import ROOT, check_sdk, safe_name, sha, spec, write
from retained_targets import verify_native


def complete_units(value, expected, builder):
    rows = value.get('suites', [])
    if (value.get('status') != 'complete' or value.get('compiler_sha256') != builder
            or len(rows) != len(expected) or {r.get('package') for r in rows} != set(expected)
            or value.get('passed') != sum(expected.values())):
        raise ValueError('incomplete retained unit inventory or builder')
    for row in rows:
        summary = row.get('summary', {})
        count = expected[row['package']]
        if (summary.get('status') != 'ok' or summary.get('passed') != count
                or summary.get('ran') != count
                or any(summary.get(key) != 0 for key in ('failed', 'skipped', 'exit_code'))):
            raise ValueError('failed, partial or empty retained unit suite')


def verify_complete(sdk, manifest, plan, host, expected):
    """Check immutable payload and full gate receipts, including both other tools."""
    check_sdk(manifest, plan, host)
    index = {}
    for row in manifest['files']:
        name = str(safe_name(row['path']))
        if name == 'manifest.json' or name in index:
            raise ValueError('duplicate/self-referential SDK file inventory')
        file = sdk / name
        if (not file.is_file() or file.is_symlink() or not file.resolve().is_relative_to(sdk.resolve())
                or file.stat().st_size != row['bytes'] or sha(file) != row['sha256']):
            raise ValueError('retained SDK file identity mismatch: ' + name)
        index[name] = file
    actual = {p.relative_to(sdk).as_posix() for p in sdk.rglob('*') if p.is_file()}
    if actual != set(index) | {'manifest.json'}:
        raise ValueError('retained SDK inventory is incomplete')
    def read(name):
        if name not in index:
            raise ValueError('missing retained gate receipt: ' + name)
        return json.loads(index[name].read_text())
    evidence = read(manifest['build_evidence'])
    fixed = evidence.get('fixed_point', {})
    compiler = manifest['tools']['nyrac']['sha256']
    seed = evidence.get('seed_compiler_sha256', '')
    if (evidence.get('source_revision') != manifest['source_revision']
            or evidence.get('validation_policy') != 'g1-g2-v1'
            or any(evidence.get(key) is not True for key in (
                'source_worktree_clean', 'product_source_matches_generations', 'full_regression', 'full_unit'))
            or fixed.get('identical') is not True or fixed.get('G1_sha256') != compiler
            or fixed.get('G2_sha256') != compiler or not re.fullmatch('[0-9a-f]{64}', seed)):
        raise ValueError('retained compiler fixed point or frozen inputs incomplete')
    for name, row in manifest['tools'].items():
        if row['path'] not in index or sha(index[row['path']]) != row['sha256']:
            raise ValueError('accepted tool bytes were substituted: ' + name)
    regression = read('share/nyra/validation/compiler-regression.json')
    count = expected['regression']
    if (regression.get('compiler_sha256') != compiler or regression.get('total') != count
            or regression.get('passed') != count or regression.get('failed') != 0
            or not re.fullmatch('[0-9a-f]{64}', regression.get('suite_inventory_sha256', ''))):
        raise ValueError('complete retained compiler regression gate missing')
    units = read('share/nyra/validation/units.json')
    complete_units(units, expected['compiler_units'], compiler)
    tools = {}
    for name, package in (('nyra-analyzer', 'analyzer'), ('nyra', 'package_manager')):
        receipt = read('share/nyra/validation/tools/' + name + '.json')
        if (receipt.get('status') != 'complete' or receipt.get('builder_sha256') != seed
                or receipt.get('product_sha256') != manifest['tools'][name]['sha256']
                or not re.fullmatch('[0-9a-f]{64}', receipt.get('test_source_sha256', ''))):
            raise ValueError('retained tool product/builder receipt mismatch: ' + name)
        complete_units(receipt.get('units', {}), {package: expected['tool_units'][name]}, seed)
        tools[name] = expected['tool_units'][name]
    return dict(compiler_sha256=compiler, compiler_regressions=count,
                compiler_units=units['passed'], tool_units=tools,
                compiler_rebuilt=False, complete_suites_repeated=False)


def main():
    host = os.environ['NYRA_CI_HOST']
    if host not in HOSTS:
        raise ValueError('unsupported native admission host')
    plan = spec(os.environ['NYRA_CI_RELEASE_TAG'])
    config = json.loads((ROOT / 'qualified-inputs' / (plan['version'] + '.json')).read_text())
    if config['source_commit'] != plan['source_commit']:
        raise ValueError('admission source differs from frozen release')
    pin = config['managed_imports'][host]
    directory = cache_root() / 'nyra-prequalified' / plan['version'] / host
    archive, receipt = verify_native(directory, host, plan, pin)
    sdk = directory / 'sdk'
    if sha(sdk / 'manifest.json') != pin['sdk_manifest_sha256']:
        raise ValueError('managed SDK differs from pinned public manifest')
    result = verify_complete(sdk, json.loads((sdk / 'manifest.json').read_text()),
                             plan, host, config['expected_native_inventory'][host])
    result.update(status='admitted-existing-complete-gates', host=host, source_commit=plan['source_commit'],
        native_receipt_sha256=pin['receipt_sha256'], archive_sha256=pin['archive_sha256'],
        sdk_manifest_sha256=pin['sdk_manifest_sha256'], source_qualification_origin=receipt,
        admission_control_commit=os.environ['GITHUB_SHA'], published=False)
    write(ROOT / 'native-admission.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'source_qualification_origin'}))


if __name__ == '__main__':
    main()
