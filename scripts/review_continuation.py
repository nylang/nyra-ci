"""Authenticate the original CI receipts before a narrowly approved continuation."""
import json
import os
import re
from publish_release import Github
from release_common import ROOT

version = os.environ['NYRA_CI_RELEASE_TAG'].removeprefix('v')
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
    raise ValueError('invalid continuation version')
config = json.loads((ROOT / 'scope-continuations' / (version + '.json')).read_text())
repository = os.environ['GITHUB_REPOSITORY']
github = Github(repository, os.environ['GITHUB_TOKEN'])
original = github.api('/actions/runs/' + config['original_run'])
if (original['path'], original['head_sha'], original['status'], original['conclusion'], original['head_repository']['full_name']) != (
        '.github/workflows/sdk-build.yml', config['original_control_commit'], 'completed', 'failure', repository):
    raise ValueError('original workflow identity or completed failure differs from reviewed scope')
artifacts = github.api('/actions/runs/' + config['original_run'] + '/artifacts?per_page=100')['artifacts']
for host in config['hosts']:
    for name in ('build-receipt-' + host, 'private-qualification-' + host):
        rows = [row for row in artifacts if row['name'] == name and not row['expired']]
        if len(rows) != 1:
            raise ValueError('missing original qualification artifact: ' + name)
with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
    output.write('original_run=' + config['original_run'] + '\n')
