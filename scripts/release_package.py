"""Merge validated private runtime catalogs and export a source-free SDK."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from release_common import ROOT, spec, policy, check_sdk, catalog_check, write, state_path, sha

host = os.environ['NYRA_CI_HOST']
plan = spec(os.environ['GITHUB_REF_NAME'])
configuration = policy()
state = json.loads(state_path(host).read_text())
expected = {'host': host, 'source_commit': plan['source_commit'], 'version': plan['version'],
    'control_commit': os.environ['GITHUB_SHA'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
    'runner': os.environ['RUNNER_NAME']}
if any(state.get(k) != v for k, v in expected.items()):
    raise ValueError('retained build does not belong to this runner, tag and workflow attempt; rerun all jobs')
sdk = Path(state['sdk'])
sys.path.insert(0, state['source'])
from tools.build.sdk_manifest import validate_sdk, inventory
from tools.build.cross_sdk import import_runtime_catalog, sync_runtime_lock
from tools.build.distribution import export_public
manifest = validate_sdk(sdk)
check_sdk(manifest, plan, host)
for producer in configuration['hosts']:
    directory = ROOT / 'catalog-input' / ('runtime-catalog-' + producer)
    catalog_check(directory, plan, producer)
    manifest.setdefault('runtime_targets', []).extend(import_runtime_catalog(sdk, directory, manifest))
    framework = directory / 'frameworks'
    if framework.exists() and not (sdk / 'frameworks').exists():
        shutil.copytree(framework, sdk / 'frameworks')
profiles = manifest['targets'] + manifest.get('runtime_targets', [])
if len(profiles) != len(configuration['targets']) or {row['key'] for row in profiles} != set(configuration['targets']):
    raise ValueError('release must include every required target profile, without duplicates')
if host.startswith('darwin-'):
    # Inspect actual deployment versions; never patch binary load commands or
    # lower a metadata field to claim compatibility that the binary lacks.
    maximum = tuple(map(int, (configuration['macos_minimum'] + '.0.0').split('.')[:3]))
    paths = [sdk / row['path'] for row in manifest['tools'].values()]
    paths += [p for p in (sdk / 'hosts' / host / 'llvm/bin').iterdir() if p.is_file()]
    paths += list((sdk / 'hosts' / host / 'llvm/lib').glob('*.dylib'))
    for binary in paths:
        file_type = subprocess.check_output(['/usr/bin/file', '-b', str(binary)], text=True)
        if 'Mach-O' not in file_type:
            continue
        output = subprocess.check_output(['xcrun', 'vtool', '-show-build', str(binary)], text=True)
        versions = [line.split()[1] for line in output.splitlines() if line.strip().startswith('minos ')]
        if not versions or any(tuple(map(int, (value + '.0.0').split('.')[:3])) > maximum for value in versions):
            raise ValueError('binary exceeds macOS deployment policy: ' + str(binary))
    manifest['minimum_os_version'] = configuration['macos_minimum']
manifest['kind'] = 'release'
sync_runtime_lock(sdk, manifest)
manifest['files'] = inventory(sdk)
write(sdk / 'manifest.json', manifest)
check_sdk(validate_sdk(sdk), plan, host)
export_dir = sdk.parent / 'public-release'
result = export_public(sdk, export_dir, candidate=False, plan_only=False)
output = ROOT / 'public-output'
if output.exists():
    shutil.rmtree(output)
output.mkdir()
archive = Path(result['archive'])
shutil.copy2(archive, output / archive.name)
receipt = json.loads((export_dir / 'release.json').read_text())
receipt.update(source_commit=plan['source_commit'], execution_mode=state['execution_mode'],
    targets=configuration['targets'], acceptance={k: manifest['acceptance'][k] for k in manifest['acceptance']})
write(output / ('release-' + host + '.json'), receipt)
print('Source-free stable SDK export complete: ' + archive.name)
