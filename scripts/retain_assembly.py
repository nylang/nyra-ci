"""Retain small assembly/provenance inputs after successful artifact uploads."""
import json
import os
from pathlib import Path
import shutil
from build_sdk import cache_root
from owned_cleanup import remove_tree
from release_common import ROOT, state_path, write

host = os.environ['NYRA_CI_HOST']
release_state = state_path(host)
state = json.loads((release_state if release_state.is_file() else ROOT / 'build-receipt.json').read_text())
work = Path(state['workspace']).resolve()
allowed = (cache_root() / 'nyra-release-work').resolve()
if work.parent != allowed or not state['success']:
    raise ValueError('refusing cleanup of work outside the completed CI build')
exported = None
if state.get('public_export_workspace'):
    exported = Path(state['public_export_workspace']).resolve()
    export_root = (cache_root() / 'nyra-release-export').resolve()
    expected = os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    if exported.parent != export_root or exported.name != expected:
        raise ValueError('refusing cleanup outside the completed public export')
retained = cache_root() / 'nyra-release-assembly' / os.environ['GITHUB_RUN_ID'] / host
retained.mkdir(parents=True, exist_ok=True)
if host == 'darwin-arm64' and release_state.is_file():
    source = Path(state['source'])
    shutil.copytree(source / 'tools', retained / 'source/tools', dirs_exist_ok=True)
    shutil.copytree(source / 'licenses', retained / 'source/licenses', dirs_exist_ok=True)
    shutil.copy2(Path(state['sdk']) / 'share/nyra/source-package-lock.json', retained / 'source-package-lock.json')
    state.update(workspace=str(retained), source=str(retained / 'source'), assembly_lock=str(retained / 'source-package-lock.json'))
    write(state_path(host), state)
# Raw private receipts were uploaded before this step. Published product bytes
# are held by CI artifacts, not reconstructed from this compact retained tree.
remove_tree(work)
if exported is not None:
    remove_tree(exported)
if release_state.is_file():
    state['build_scratch_removed_after_upload'] = True
    write(release_state, state)
print('Released completed compiler build scratch after artifact retention: ' + host)
