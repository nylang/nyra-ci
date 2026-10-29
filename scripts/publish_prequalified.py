"""Publish the frozen, previously accepted 1.0.4 bundle; never invoke a compiler."""
import argparse
import json
import os
from pathlib import Path
from build_sdk import cache_root
from publish_release import Github, find_release
from release_common import ROOT, sha, write

plan = json.loads((ROOT / 'releases/prequalified-v1.0.4.json').read_text())
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--stage', action='store_true')
parser.add_argument('--directory', type=Path)
args = parser.parse_args()
rows = plan['assets']
if args.stage:
    rows = [r for r in rows if r['producer'] == os.environ['NYRA_CI_HOST']]
    directory = cache_root() / 'prequalified/1.0.4/assets'
else:
    directory = args.directory
if directory is None:
    parser.error('--directory is required for publication')
expected = {r['name']: r for r in rows}
if {p.name for p in directory.iterdir()} != set(expected):
    raise ValueError('prequalified asset inventory mismatch')
for name, row in expected.items():
    path = directory / name
    if path.is_symlink() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
        raise ValueError('prequalified asset bytes changed: ' + name)
if args.stage:
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('directory=' + str(directory) + '\n')
    print('Existing accepted files verified; no compiler or complete suites run')
else:
    github = Github('nylang/sdk', os.environ['SDK_RELEASE_TOKEN'])
    release = find_release(github, 'v1.0.4')
    if release is None:
        release = github.api('/releases', dict(tag_name='v1.0.4', target_commitish=plan['public_commit'], name='Nyra SDK 1.0.4', body=plan['body'], draft=True, prerelease=False))
    if release['body'] != plan['body'] or release['prerelease']:
        raise ValueError('existing release belongs to another plan')
    assets = {r['name']: r for r in github.api('/releases/' + str(release['id']) + '/assets?per_page=100')}
    if set(assets) - set(expected):
        raise ValueError('unexpected remote assets')
    for name, row in expected.items():
        asset = assets.get(name)
        if asset and asset['state'] == 'starter' and release['draft']:
            github.api('/releases/assets/' + str(asset['id']), method='DELETE')
            asset = None
        if asset is None:
            if not release['draft']:
                raise ValueError('published releases are immutable')
            print('Uploading ' + name, flush=True)
            asset = github.upload(release, directory / name)
        if asset.get('state') != 'uploaded' or asset['size'] != row['bytes'] or asset.get('digest') != 'sha256:' + row['sha256']:
            raise ValueError('remote asset digest mismatch: ' + name)
    remote = github.api('/releases/' + str(release['id']) + '/assets?per_page=100')
    if len(remote) != len(expected) or {r['name'] for r in remote} != set(expected):
        raise ValueError('incomplete remote inventory')
    for asset in remote:
        row = expected[asset['name']]
        if asset['size'] != row['bytes'] or asset.get('digest') != 'sha256:' + row['sha256']:
            raise ValueError('remote asset changed before publication')
    if release['draft']:
        release = github.api('/releases/' + str(release['id']), {'draft': False}, method='PATCH')
    if release['draft']:
        raise ValueError('publication unconfirmed')
    write(ROOT / 'publication.json', dict(status='published', version='1.0.4', url=release['html_url'],
          published_at=release['published_at'], source_commit=plan['source_commit'],
          qualification='prequalified-existing-1.0.4', compiler_rebuilt=False, complete_suites_repeated=False,
          assets=[{k: a[k] for k in ('id', 'name', 'size', 'digest', 'browser_download_url')} for a in remote]))
    print('Published ' + release['html_url'])
