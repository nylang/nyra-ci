"""Rebind an authenticated completed target catalog without rebuilding or rerunning it."""
import json
import os
from pathlib import Path
import shutil
from release_common import ROOT, policy, safe_name, sha, write, state_path


def verify_catalog(directory, host, plan, pin):
    file = directory / 'catalog.json'
    if file.is_symlink() or sha(file) != pin['catalog_sha256']:
        raise ValueError('completed target catalog changed')
    value = json.loads(file.read_text())
    if (value['host'], value['version'], value['source_commit'], value['control_commit']) != (
            host, plan['version'], plan['source_commit'], pin['control_commit']):
        raise ValueError('completed target catalog identity mismatch')
    actual = {p.relative_to(directory).as_posix(): p for p in (directory / 'targets').rglob('*') if p.is_file()}
    if set(actual) != set(value['files']):
        raise ValueError('completed target inventory differs')
    for name, digest in value['files'].items():
        safe_name(name)
        if actual[name].is_symlink() or sha(actual[name]) != digest:
            raise ValueError('completed target bytes changed')
    rows = value['targets']
    if len(rows) != len(policy()['runtime_producers'][host]) or {r['key'] for r in rows} != set(policy()['runtime_producers'][host]):
        raise ValueError('completed target coverage differs')
    for row in rows:
        if row.get('status') != 'passed' or row['acceptance_level'] != policy()['execution_required'].get(row['key'], 'compile-only'):
            raise ValueError('incomplete target gate cannot be reused')
    expected=set(policy()['compile_only_hosts'].get(host, []))
    worksets=value.get('host_compilation', [])
    if len(worksets)!=len(expected) or {r['key'] for r in worksets}!=expected:
        raise ValueError('retained compile-only host inventory differs')
    for row in worksets:
        if row['acceptance_level']!='compile-only' or row['executable_package_ready'] or {r['name'] for r in row['tools']}!={'nyrac','nyra-analyzer','nyra'} or len(row['bridges'])!=2:
            raise ValueError('retained host compile-only receipt incomplete')
        if any(r['object_units']<=0 or len(r['objects'])!=r['object_units'] for r in row['tools']):
            raise ValueError('empty retained host object evidence')
    return value


def stage(directory, host, plan, pin, work, state, archive, receipt_file):
    value = verify_catalog(directory, host, plan, pin)
    public = ROOT / 'public-output'
    shutil.rmtree(public, ignore_errors=True)
    public.mkdir()
    shutil.copy2(archive, public / archive.name)
    shutil.copy2(receipt_file, public / receipt_file.name)
    output = ROOT / 'runtime-output'
    shutil.rmtree(output, ignore_errors=True)
    shutil.copytree(directory / 'targets', output / 'targets')
    value.update(retained_target_origin=dict(pin), target_build_control_commit=value['control_commit'],
                 control_commit=os.environ['GITHUB_SHA'], targets_rebuilt=False, target_suites_repeated=False)
    write(output / 'catalog.json', value)
    if host == 'darwin-arm64':
        shutil.copytree(directory / 'assembly/source', work / 'source')
        shutil.copy2(directory / 'assembly/source-package-lock.json', work / 'source-package-lock.json')
        state.update(source=str(work / 'source'), assembly_lock=str(work / 'source-package-lock.json'))
    state.update(success=True, retained_target_origin=dict(pin), targets_rebuilt=False, target_suites_repeated=False)
    write(ROOT / 'build-receipt.json', state)
    write(state_path(host), state)
