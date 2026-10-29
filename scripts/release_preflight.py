"""Exercise CI release inputs without rebuilding or repeating qualified suites."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from build_sdk import seed_sdk, run
from runner_inputs import verify_providers, verify_build_tools
from release_common import ROOT, checkout, sha, write

host = os.environ['NYRA_CI_HOST']
report = dict(host=host, success=False, complete_suites_repeated=False, compiler_rebuilt=False)
try:
    report.update(verify_providers(host))
    sdk = seed_sdk(host)
    env = verify_build_tools(host, sdk)
    manifest = json.loads((sdk / 'manifest.json').read_text())
    report['seed_version'] = manifest['version']
    report['seed_manifest_sha256'] = sha(sdk / 'manifest.json')
    for name, row in manifest['tools'].items():
        path = sdk / row['path']
        if sha(path) != row['sha256']:
            raise ValueError('pinned seed tool bytes changed')
        result = subprocess.run([str(path), '--version'], capture_output=True, text=True, check=True, timeout=45, env=env)
        report.setdefault('tools', {})[name] = dict(sha256=row['sha256'], version=result.stdout.strip())
    with tempfile.TemporaryDirectory(prefix='nyra-ci-preflight-') as temp:
        source = Path(temp) / 'source'
        commit = checkout(os.environ['NYRA_CI_SOURCE_REF'], source)
        modules = subprocess.check_output(['git', 'config', '--file', '.gitmodules', '--get-regexp', r'submodule\..*\.path'], cwd=source, text=True)
        paths = [line.split(None, 1)[1] for line in modules.splitlines() if line.split(None, 1)[1].startswith('third_party/')]
        run(['git', 'submodule', 'update', '--init', '--recursive', '--', *paths], source)
        sys.path.insert(0, str(source))
        from tools.build.distribution import validate_public
        validate_public(sdk)
        # The public SDK selection/parser is exercised by the normal dry run.
        suffix = '.exe' if host.startswith('windows-') else ''
        command = [sys.executable, '-B', 'build.py', 'bootstrap', '--bootstrap-sdk', str(sdk),
                   '--seed', str(sdk / 'hosts' / host / 'bin' / ('nyrac' + suffix)),
                   '--out-dir', str(Path(temp) / 'planned-bootstrap'), '--regression', '--plan']
        run(command, source, env)
        report['source_commit'] = commit
    report['success'] = True
finally:
    write(ROOT / 'release-preflight.json', report)
