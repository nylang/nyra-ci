"""Replay retained Core units under the real Windows service token and PATH."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from release_common import spec, write

if os.name != 'nt':
    raise RuntimeError('Windows service preflight only')
run_id = os.environ['NYRA_RETAINED_RUN']
attempt = os.environ['NYRA_RETAINED_ATTEMPT']
if not re.fullmatch(r'[0-9]+', run_id) or not re.fullmatch(r'[0-9]+', attempt):
    raise ValueError('invalid retained run identity')
cache = Path(os.environ['NYRA_CI_CACHE_DIRECTORY'])
source = cache / 'nyra-release-work' / ('nyra-sdk-' + run_id + '-' + attempt) / 'source'
bootstrap = source / 'cache/ci/bootstrap'
plan = spec('v1.0.2')
if subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip() != plan['source_commit']:
    raise ValueError('retained source differs from the release plan')
record = json.loads((bootstrap / 'bootstrap-report.json').read_text())
if record['source_revision'] != plan['source_commit'] or not record['fixed_point']['identical']:
    raise ValueError('retained bootstrap identity or fixed point differs')
sys.path.insert(0, str(source))
from tools.dev.workspace import prepare_process_test_tools
out = cache / 'nyra-service-preflight' / (os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT'])
out.mkdir(parents=True, exist_ok=False)
prefix = cache / 'nyra-sdk/1.0.1/windows-x86_64/sdk/hosts/windows-x86_64/llvm'
compiler = bootstrap / 'native-generations/stage-3/nyrac.exe'
environment = os.environ | {'NYRA': str(compiler), 'LLVM_PREFIX': str(prefix),
    'NYRA_LLVM_STATIC_LIB': str(prefix / 'lib/libLLVM.a'), 'CC': str(prefix / 'bin/clang.exe'),
    'AR': str(prefix / 'bin/llvm-ar.exe'), 'NYRA_LIB_ROOT': str(bootstrap / 'platform-libs')}
# Keep the service PATH; replacing it here would hide the original defect.
environment = prepare_process_test_tools(bootstrap / 'native-generations/snapshot', out / 'test-tools', environment)
names = [line[6:] for line in (bootstrap / 'units/nyra/run.log').read_text().splitlines() if line.startswith('BEGIN ')]
if len(names) != 74 or len(set(names)) != 74:
    raise ValueError('unexpected retained unit inventory')
results = []
for index, name in enumerate(names):
    result = subprocess.run([str(bootstrap / 'units/nyra/tests.exe'), str(index)],
        cwd=bootstrap / 'native-generations/snapshot', env=environment,
        capture_output=True, text=True, timeout=120)
    results.append({'name': name, 'exit_code': result.returncode, 'output': result.stdout + result.stderr})
    print(name, 'PASS' if result.returncode == 0 else 'FAIL', flush=True)
report = {'scope': 'Retained candidate Core unit replay under the runner service account; not release acceptance',
    'source_commit': plan['source_commit'], 'retained_run': run_id, 'retained_attempt': attempt,
    'passed': sum(row['exit_code'] == 0 for row in results), 'failed': sum(row['exit_code'] != 0 for row in results),
    'results': results}
write(out / 'report.json', report)
write(Path('service-preflight.json'), report)
if report['failed']:
    raise SystemExit(1)
