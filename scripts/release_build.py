"""Build one frozen host SDK and its assigned cross-runtime catalog privately."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from release_common import ROOT, spec, policy, check_sdk, sha, write, state_path

host = os.environ['NYRA_CI_HOST']
plan = spec(os.environ['GITHUB_REF_NAME'])
configuration = policy()
environment = os.environ | {'NYRA_CI_RELEASE': 'true', 'NYRA_CI_SOURCE_REF': plan['source_commit'],
                            'NYRA_CI_FULL_REGRESSION': 'true'}
if host.startswith('darwin-'):
    environment['MACOSX_DEPLOYMENT_TARGET'] = configuration['macos_minimum']
subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/build_sdk.py')], env=environment, check=True)
build = json.loads((ROOT / 'build-receipt.json').read_text())
if not build['success'] or build['source_commit'] != plan['source_commit']:
    raise ValueError('native SDK build did not complete for the pinned source')
work = Path(build['workspace'])
sdk = Path(build['sdk'])
source = work / 'source'
sys.path.insert(0, str(source))
from tools.build.sdk_manifest import validate_sdk
from tools.build.cross_sdk import add_targets
manifest = validate_sdk(sdk)
check_sdk(manifest, plan, host)
configs = []
for target in configuration['cross_producers'][host]:
    record = {'key': target}
    if target.startswith('android-'):
        ndk = os.environ.get('ANDROID_NDK_HOME')
        if not ndk:
            ndk = str(Path(os.environ['ANDROID_HOME']) / 'ndk' / configuration['ndk_revision'])
        properties = (Path(ndk) / 'source.properties').read_text()
        if ('Pkg.Revision = ' + configuration['ndk_revision']) not in properties:
            raise ValueError('Android NDK differs from the pinned release toolchain')
        record.update(ndk=ndk, api=configuration['android_api'])
    elif target.startswith('ios-'):
        record['minimum'] = configuration['ios_minimum']
    else:
        record['toolchain'] = str(sdk / 'hosts' / host / 'llvm')
        if target == 'linux-riscv64':
            record['sysroot'] = os.environ.get('NYRA_CI_RISCV_SYSROOT', '/usr/riscv64-linux-gnu')
    path = source / 'cache/ci/target-configs' / (target + '.json')
    write(path, record)
    configs.append(path)
if configs:
    # Cross construction is staged as a candidate; native acceptance remains
    # the actual bootstrap evidence and is revalidated before stable export.
    manifest['kind'] = 'candidate'
    write(sdk / 'manifest.json', manifest)
    add_targets(sdk, configs, source / 'cache/ci/cross', timeout=7200)
    manifest = validate_sdk(sdk)
    check_sdk(manifest, plan, host)
catalog = ROOT / 'catalog-output'
if catalog.exists():
    shutil.rmtree(catalog)
catalog.mkdir()
files = []
for row in manifest['files']:
    name = row['path']
    if name.startswith(('targets/', 'share/nyra/', 'frameworks/')):
        target = catalog / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sdk / name, target)
        if sha(target) != row['sha256']:
            raise ValueError('SDK changed while copying runtime catalog')
        files.append(name)
shutil.copy2(sdk / 'manifest.json', catalog / 'manifest.json')
write(catalog / 'catalog.json', {'host': host, 'source_commit': plan['source_commit'],
    'manifest_sha256': sha(catalog / 'manifest.json'), 'files': files})
write(state_path(host), {'host': host, 'source_commit': plan['source_commit'], 'version': plan['version'],
    'control_commit': os.environ['GITHUB_SHA'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
    'runner': os.environ['RUNNER_NAME'], 'source': str(source), 'sdk': str(sdk),
    'execution_mode': build['execution_mode']})
print('Native acceptance and assigned cross libraries complete: ' + host, flush=True)
