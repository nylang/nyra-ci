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


def authorize(identity, jobs, artifacts, repository):
    workflow = identity.get('path')
    names = {
        '.github/workflows/release-sdk.yml': {'Assemble sealed publication bundle'},
        '.github/workflows/sdk-build.yml': {'targets / Assemble retained publication bundle'},
    }
    events = ('workflow_dispatch',) if workflow == '.github/workflows/sdk-build.yml' else ('push', 'workflow_dispatch')
    if (workflow not in names or identity.get('event') not in events or
            identity.get('head_repository', {}).get('full_name') != repository or
            not re.fullmatch(r'[0-9a-f]{40}', identity.get('head_sha', ''))):
        raise ValueError('original run is not a trusted release qualification workflow')
    if not any(j.get('name') in names[workflow] and j.get('conclusion') == 'success' for j in jobs):
        raise ValueError('original run has no successful publication bundle assembly')
    matching = [a for a in artifacts if a.get('name') == 'qualified-release' and a.get('expired') is False]
    if len(matching) != 1:
        raise ValueError('qualified bundle missing/expired; inspect original qualification run')
    return identity['head_sha']


def main():
    run = os.environ['NYRA_CI_QUALIFICATION_RUN']
    if not re.fullmatch(r'[1-9][0-9]*', run):
        raise ValueError('qualification run ID must be numeric')
    identity = get('/actions/runs/' + run)
    jobs = []
    page = 1
    while True:
        batch = get('/actions/runs/' + run + '/jobs?filter=latest&per_page=100&page=' + str(page))['jobs']
        jobs += batch
        if len(batch) < 100:
            break
        page += 1
    artifacts = get('/actions/runs/' + run + '/artifacts?per_page=100')['artifacts']
    sha = authorize(identity, jobs, artifacts, os.environ['GITHUB_REPOSITORY'])
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.write('NYRA_CI_QUALIFICATION_SHA=' + sha + '\n')
    print('Authorized retained bundle from qualification run ' + run)


if __name__ == '__main__':
    main()
