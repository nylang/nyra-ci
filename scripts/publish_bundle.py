"""Validate and publish a sealed CI bundle without a source checkout or rebuild."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile
from release_common import spec, policy, sha, safe_name
from publish_release import Github, find_release, validate_archive
from public_policy import _payload_allowed


def validate_runtime(path, plan, group):
    prefix = 'nyra-runtime-' + plan['version'] + '-' + group
    observed = {}
    manifest = None
    with tarfile.open(path, mode='r|gz') as archive:
        for row in archive:
            name = safe_name(row.name.rstrip('/')).as_posix()
            if name == prefix and row.isdir():
                continue
            if not name.startswith(prefix + '/'):
                raise ValueError('unexpected runtime archive root')
            name = name[len(prefix) + 1:]
            if row.isdir():
                continue
            if not row.isfile() or name in observed:
                raise ValueError('runtime archive links or duplicate files are forbidden')
            with archive.extractfile(row) as stream:
                if name == 'manifest.json':
                    if row.size > 32 * 1024 * 1024:
                        raise ValueError('oversized runtime manifest')
                    content = stream.read()
                    manifest = json.loads(content)
                    digest = hashlib.sha256(content).hexdigest()
                else:
                    h = hashlib.sha256()
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        h.update(chunk)
                    digest = h.hexdigest()
            observed[name] = dict(sha256=digest, bytes=row.size, executable=bool(row.mode & 0o111))
    if not manifest or (manifest.get('format'), manifest.get('version'), manifest.get('group'), manifest.get('source_revision')) != ('nyra-runtime-packages-v1', plan['version'], group, plan['source_commit']):
        raise ValueError('runtime manifest release identity mismatch')
    keys = set(policy()['runtime_groups'][group])
    rows = manifest['targets']
    if len(rows) != len(keys) or {r['key'] for r in rows} != keys:
        raise ValueError('runtime group target coverage mismatch')
    for row in rows:
        if row['acceptance_level'] != policy()['execution_required'].get(row['key'], 'compile-only'):
            raise ValueError('runtime target execution acceptance missing')
        expected = {'kernel', 'ntest', 'stdlib'} if row['hosted'] else {'kernel', 'ntest'}
        if len(row['packages']) != len(expected) or {p['name'] for p in row['packages']} != expected:
            raise ValueError('runtime package inventory mismatch')
        for package in row['packages']:
            root = package['path']
            for name, field in ((root + '/' + package['archive'], 'archive_sha256'), (root + '/manifest.yaml', 'manifest_sha256'), (package['index'], 'index_sha256')):
                if observed.get(name, {}).get('sha256') != package[field]:
                    raise ValueError('runtime package archive/index mismatch')
    index = {r['path']: r for r in manifest['files']}
    if len(index) != len(manifest['files']) or set(observed) != set(index) | {'manifest.json'}:
        raise ValueError('runtime archive inventory mismatch')
    for name, row in index.items():
        safe_name(name)
        provider = name in {'providers/' + key + '.json' for key in keys}
        if observed[name] != {k: row[k] for k in ('sha256', 'bytes', 'executable')}:
            raise ValueError('runtime archive payload changed')
        if name not in ('LICENSE.txt', 'README.md') and not provider and not _payload_allowed(name, 'darwin-arm64', keys):
            raise ValueError('unsupported public runtime payload: ' + name)


def verify(directory, plan, qualification_run=None):
    directory = Path(directory)
    seal = json.loads((directory / 'release.json').read_text())
    if (seal.get('schema_version'), seal.get('version'), seal.get('source_commit'), seal.get('validation_policy')) != (2, plan['version'], plan['source_commit'], 'g1-g2-v1'):
        raise ValueError('publication seal differs from release plan')
    expected_control = os.environ.get('NYRA_CI_QUALIFICATION_SHA') or os.environ.get('GITHUB_SHA')
    if expected_control and seal.get('control_commit') != expected_control:
        raise ValueError('sealed control revision differs from original qualification run')
    if qualification_run and str(seal.get('qualification_run')) != str(qualification_run):
        raise ValueError('publication seal belongs to a different qualification run')
    expected = set()
    for host in policy()['hosts']:
        expected.update(('release-' + host + '.json', 'nyra-sdk-' + plan['version'] + '-' + host + ('.zip' if host.startswith('windows-') else '.tar.gz')))
    expected.update('nyra-runtime-' + plan['version'] + '-' + group + '.tar.gz' for group in policy()['runtime_groups'])
    rows = seal['assets']
    if len(rows) != len(expected) or {r['name'] for r in rows} != expected:
        raise ValueError('sealed assets do not cover the release policy')
    if {p.name for p in directory.iterdir()} != expected | {'release.json', 'SHA256SUMS'}:
        raise ValueError('publication directory contains unexpected files')
    for row in rows:
        safe_name(row['name'])
        path = directory / row['name']
        if path.is_symlink() or path.stat().st_size != row['bytes'] or sha(path) != row['sha256']:
            raise ValueError('sealed publication asset changed: ' + row['name'])
    files = [directory / name for name in sorted(expected | {'release.json'})]
    sums = ''.join(sha(p) + '  ' + p.name + '\n' for p in files)
    if (directory / 'SHA256SUMS').read_text() != sums:
        raise ValueError('publication checksums differ from sealed inputs')
    for host in policy()['hosts']:
        receipt = json.loads((directory / ('release-' + host + '.json')).read_text())
        if (receipt.get('host'), receipt.get('source_commit'), receipt.get('version'), receipt.get('validation_policy'), receipt.get('success')) != (host, plan['source_commit'], plan['version'], 'g1-g2-v1', True):
            raise ValueError('native receipt identity mismatch')
        archive = receipt['archive']
        name = 'nyra-sdk-' + plan['version'] + '-' + host + ('.zip' if host.startswith('windows-') else '.tar.gz')
        if archive['path'] != name:
            raise ValueError('native receipt archive name mismatch')
        path = directory / name
        if archive['sha256'] != sha(path) or archive['bytes'] != path.stat().st_size:
            raise ValueError('native receipt archive mismatch')
        validate_archive(path, receipt, plan, host)
    for group in policy()['runtime_groups']:
        validate_runtime(directory / ('nyra-runtime-' + plan['version'] + '-' + group + '.tar.gz'), plan, group)
    return files + [directory / 'SHA256SUMS']


def publish(github, files, plan, tag):
    repo = github.api('')
    if repo['private'] or repo['full_name'] != policy()['public_repository']:
        raise ValueError('SDK destination must be the expected public repository')
    body = 'Nyra SDK ' + tag + '\nSource: ' + plan['source_commit'] + '\n\n' + plan['notes'] + '\n\nQualified by CI using g1-g2-v1; exact accepted tool bytes. Runtime execution levels are recorded per target. Unsigned and not notarized.'
    release = find_release(github, tag)
    if release is None:
        from public_docs import update
        update(github, plan)
        commit = github.api('/commits/' + repo['default_branch'])['sha']
        release = github.api('/releases', dict(tag_name=tag, target_commitish=commit, name='Nyra SDK ' + tag, body=body, draft=True, prerelease=False))
    if release['body'] != body or release['prerelease']:
        raise ValueError('existing release belongs to a different plan')
    expected = {p.name: p for p in files}
    assets = {a['name']: a for a in github.api('/releases/' + str(release['id']) + '/assets?per_page=100')}
    if set(assets) - set(expected):
        raise ValueError('existing release contains unexpected assets')
    for name, path in expected.items():
        asset = assets.get(name)
        if asset and asset.get('state') == 'starter' and release['draft']:
            # Recover only an incomplete upload belonging to this exact draft.
            github.api('/releases/assets/' + str(asset['id']), method='DELETE')
            asset = None
        if asset is None:
            if not release['draft']:
                raise ValueError('published versions are immutable')
            print('Uploading ' + name, flush=True)
            asset = github.upload(release, path)
        if asset.get('size') != path.stat().st_size or asset.get('digest') != 'sha256:' + sha(path) or asset.get('state') != 'uploaded':
            raise ValueError('remote asset digest/size/state mismatch: ' + name)
    remote = github.api('/releases/' + str(release['id']) + '/assets?per_page=100')
    if len(remote) != len(expected) or {a['name'] for a in remote} != set(expected):
        raise ValueError('remote release inventory incomplete')
    for asset in remote:
        path = expected[asset['name']]
        if asset['size'] != path.stat().st_size or asset.get('digest') != 'sha256:' + sha(path):
            raise ValueError('remote asset changed before publication')
    if release['draft']:
        release = github.api('/releases/' + str(release['id']), {'tag_name': tag, 'draft': False}, method='PATCH')
    if release['draft']:
        raise RuntimeError('publication was not confirmed')
    print('Published ' + release['html_url'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--qualification-run', required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    plan = spec(args.tag)
    files = verify(args.directory, plan, args.qualification_run)
    if args.publish:
        token = os.environ.get('SDK_RELEASE_TOKEN', '')
        if not token:
            raise ValueError('SDK_RELEASE_TOKEN is missing')
        publish(Github(policy()['public_repository'], token), files, plan, args.tag)
    else:
        print('Sealed bundle and public inventories verified; no rebuild or remote mutation')


if __name__ == '__main__':
    main()
