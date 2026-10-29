"""Retain small assembly/provenance inputs after successful artifact uploads."""
import json
import os
from pathlib import Path
import shutil
from build_sdk import cache_root
from release_common import ROOT, state_path, write

host = os.environ['NYRA_CI_HOST']
state = json.loads(state_path(host).read_text())
work = Path(state['workspace']).resolve()
allowed = (cache_root() / 'nyra-release-work').resolve()
if work.parent != allowed or not state['success']:
    raise ValueError('refusing cleanup of work outside the completed CI build')
retained = cache_root() / 'nyra-release-assembly' / os.environ['GITHUB_RUN_ID'] / host
retained.mkdir(parents=True, exist_ok=True)
if host == 'darwin-arm64':
    source = Path(state['source'])
    shutil.copytree(source / 'tools', retained / 'source/tools', dirs_exist_ok=True)
    shutil.copytree(source / 'licenses', retained / 'source/licenses', dirs_exist_ok=True)
    shutil.copy2(Path(state['sdk']) / 'share/nyra/source-package-lock.json', retained / 'source-package-lock.json')
    state.update(workspace=str(retained), source=str(retained / 'source'), assembly_lock=str(retained / 'source-package-lock.json'))
    write(state_path(host), state)
# Raw private receipts were uploaded before this step. Published product bytes
# are held by CI artifacts, not reconstructed from this compact retained tree.
shutil.rmtree(work)
print('Released completed compiler build scratch after artifact retention: ' + host)
