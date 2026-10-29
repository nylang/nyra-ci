"""Probe the actual CI publication credential using a disposable private draft."""
import json
import os
from pathlib import Path
import tempfile
from publish_release import Github, sha
from release_common import policy, write

token = os.environ.get('SDK_RELEASE_TOKEN', '')
if not token:
    raise ValueError('SDK_RELEASE_TOKEN is missing')
github = Github(policy()['public_repository'], token)
repo = github.api('')
if repo['private'] or repo['full_name'] != policy()['public_repository']:
    raise ValueError('unexpected publication destination')
# Probe Contents-write against an already published commit, independently of
# permissions required to introduce workflow files at a new branch head.
latest = github.api('/releases/latest')
commit = github.api('/commits/' + latest['tag_name'])['sha']
release = None
try:
    tag = 'ci-publication-probe-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    release = github.api('/releases', dict(tag_name=tag, target_commitish=commit,
                         name=tag, body='Disposable CI permission probe; no compiler assets.', draft=True, prerelease=True))
    with tempfile.TemporaryDirectory() as temp:
        file = Path(temp) / 'probe.json'
        write(file, dict(probe='publication-permissions', run=os.environ['GITHUB_RUN_ID']))
        asset = github.upload(release, file)
        if asset.get('state') != 'uploaded' or asset.get('digest') != 'sha256:' + sha(file):
            raise ValueError('CI publication upload probe failed')
finally:
    if release:
        github.api('/releases/' + str(release['id']), method='DELETE')
write('publication-preflight.json', dict(success=True, create_upload_digest_delete=True,
                                      released=False, complete_suites_repeated=False))
print('CI publication credential can create, upload, verify and delete its draft')
