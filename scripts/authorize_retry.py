"""Authorize reuse of an existing successful assembly, including failed uploads."""
import json
import os
import re
import urllib.request


def get(path):
    req = urllib.request.Request('https://api.github.com/repos/' + os.environ['GITHUB_REPOSITORY'] + path,
        headers={'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json', 'User-Agent': 'Nyra-CI'})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


run = os.environ['NYRA_CI_QUALIFICATION_RUN']
if not re.fullmatch(r'[1-9][0-9]*', run):
    raise ValueError('qualification run ID must be numeric')
identity = get('/actions/runs/' + run)
if identity['path'] != '.github/workflows/release-sdk.yml' or identity['event'] not in ('push', 'workflow_dispatch') or identity['head_repository']['full_name'] != os.environ['GITHUB_REPOSITORY']:
    raise ValueError('original run is not a trusted release qualification workflow')
jobs = []
page = 1
while True:
    batch = get('/actions/runs/' + run + '/jobs?filter=latest&per_page=100&page=' + str(page))['jobs']
    jobs += batch
    if len(batch) < 100:
        break
    page += 1
if not any(j['name'] == 'Assemble sealed publication bundle' and j['conclusion'] == 'success' for j in jobs):
    raise ValueError('original run has no successful publication bundle assembly')
artifacts = get('/actions/runs/' + run + '/artifacts?per_page=100')['artifacts']
matching = [a for a in artifacts if a['name'] == 'qualified-release' and not a['expired']]
if len(matching) != 1:
    raise ValueError('qualified bundle missing/expired; inspect original qualification run')
with open(os.environ['GITHUB_ENV'], 'a') as output:
    output.write('NYRA_CI_QUALIFICATION_SHA=' + identity['head_sha'] + '\n')
print('Authorized retained bundle from qualification run ' + run)
