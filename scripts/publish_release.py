"""Validate source-free SDK bundles and atomically publish one complete version."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import ssl
import stat
import tarfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from release_common import spec, policy, check_sdk, sha, safe_name, write
from public_policy import _payload_allowed


def validate_archive(path, receipt, plan, host):
    prefix = 'nyra-sdk-' + plan['version'] + '-' + host
    observed = {}
    manifest = None

    def record(name, size, stream, executable):
        nonlocal manifest
        name = safe_name(name).as_posix()
        if not name.startswith(prefix + '/'):
            raise ValueError('unexpected SDK archive root')
        name = name[len(prefix) + 1:]
        if name in observed:
            raise ValueError('duplicate SDK archive member')
        if name == 'manifest.json':
            if size > 32 * 1024 * 1024:
                raise ValueError('oversized SDK manifest')
            content = stream.read()
            digest = hashlib.sha256(content).hexdigest()
            if digest != receipt['sdk_manifest_sha256']:
                raise ValueError('public SDK manifest digest mismatch')
            manifest = json.loads(content)
        else:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
            digest = digest.hexdigest()
        observed[name] = {'sha256': digest, 'bytes': size, 'executable': executable}

    if path.suffix == '.zip':
        with zipfile.ZipFile(path) as archive:
            for row in archive.infolist():
                safe_name(row.filename.rstrip('/'))
                if row.filename.rstrip('/') != prefix and not row.filename.startswith(prefix + '/'):
                    raise ValueError('unexpected archive directory root')
                mode = row.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ValueError('SDK archive symlinks are forbidden')
                if row.is_dir():
                    continue
                with archive.open(row) as stream:
                    record(row.filename, row.file_size, stream, bool(mode & 0o111))
    else:
        with tarfile.open(path, mode='r|gz') as archive:
            for row in archive:
                safe_name(row.name.rstrip('/'))
                if row.name.rstrip('/') != prefix and not row.name.startswith(prefix + '/'):
                    raise ValueError('unexpected archive directory root')
                if row.isdir():
                    continue
                if not row.isfile():
                    raise ValueError('SDK archive must contain only regular files and directories')
                with archive.extractfile(row) as stream:
                    record(row.name, row.size, stream, bool(row.mode & 0o111))
    if not manifest:
        raise ValueError('missing public SDK manifest')
    check_sdk(manifest, plan, host)
    distribution = manifest.get('distribution', {})
    if manifest['kind'] != 'release' or distribution.get('kind') != 'public' or distribution.get('policy') != 'nyra-public-sdk-v1':
        raise ValueError('stable publication requires a source-free public SDK')
    rows = manifest['targets'] + manifest.get('runtime_targets', [])
    targets = set(policy()['targets'])
    if len(rows) != len(targets) or {r['key'] for r in rows} != targets:
        raise ValueError('SDK target capability regression')
    for row in rows:
        packages = {p['name'] for p in row['packages']}
        expected = {'kernel'} if row['key'] in ('none-riscv64', 'nyraos-riscv64') else set(policy()['packages'])
        if packages != expected or len(row['packages']) != len(expected):
            raise ValueError('SDK package closure differs from release policy')
    if host.startswith('darwin-') and manifest.get('minimum_os_version') != policy()['macos_minimum']:
        raise ValueError('macOS compatibility declaration is missing or inconsistent')
    index = {r['path']: r for r in manifest['files']}
    if len(index) != len(manifest['files']) or set(observed) != set(index) | {'manifest.json'}:
        raise ValueError('public SDK inventory does not exactly match its archive')
    for name, row in index.items():
        safe_name(name)
        if any(observed[name][k] != row[k] for k in ('sha256', 'bytes', 'executable')):
            raise ValueError('public SDK archive payload mismatch: ' + name)
        metadata = name in ('package-lock.json', 'share/nyra/source-package-lock.json', 'share/nyra/build-evidence.json') or (
            name.startswith(('share/nyra/validation/', 'share/nyra/runtime-producers/')) and name.endswith('.json'))
        legal = name in ('LICENSE.txt', 'README.md', 'THIRD-PARTY-NOTICES.txt') or name.startswith('licenses/')
        if not (metadata or legal or _payload_allowed(name, host, targets)):
            raise ValueError('private or unsupported SDK payload: ' + name)
    for tool in manifest['tools'].values():
        if index[tool['path']]['sha256'] != tool['sha256']:
            raise ValueError('tool identity does not match payload')
    required_licenses = {'Nyra SDK', 'yaml', 'json', 'nllvm', 'LLVM'}
    if not required_licenses <= {r['name'] for r in distribution.get('licenses', [])}:
        raise ValueError('missing required legal notices')
    for row in distribution['licenses']:
        if index.get(row['path'], {}).get('sha256') != row['sha256']:
            raise ValueError('license hash mismatch')
    return manifest


class Github:
    def __init__(self, repository, token):
        self.repository = repository
        self.token = token

    def api(self, suffix, payload=None, method=None):
        request = urllib.request.Request('https://api.github.com/repos/' + self.repository + suffix,
            data=json.dumps(payload).encode() if payload is not None else None, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'User-Agent': 'Nyra-SDK-release'})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
            return json.loads(data) if data else None

    def upload(self, release, file):
        url = urllib.parse.urlsplit(release['upload_url'].split('{', 1)[0])
        if url.scheme != 'https' or url.hostname != 'uploads.github.com':
            raise ValueError('unexpected release upload endpoint')
        connection = http.client.HTTPSConnection(url.hostname, timeout=600, context=ssl.create_default_context())
        try:
            endpoint = url.path + '?' + urllib.parse.urlencode({'name': file.name})
            connection.putrequest('POST', endpoint)
            connection.putheader('Authorization', 'Bearer ' + self.token)
            connection.putheader('User-Agent', 'Nyra-SDK-release')
            connection.putheader('Content-Type', 'application/octet-stream')
            connection.putheader('Content-Length', str(file.stat().st_size))
            connection.endheaders()
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    connection.send(chunk)
            response = connection.getresponse()
            body = response.read()
            if response.status != 201:
                raise RuntimeError('SDK upload rejected with HTTP ' + str(response.status))
            return json.loads(body)
        finally:
            connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--publish', action='store_true', help='publish only after complete local and remote validation')
    args = parser.parse_args()
    plan = spec(args.tag)
    directory = args.directory.resolve()
    reports = []
    files = []
    for host in policy()['hosts']:
        receipt_path = directory / ('release-' + host + '.json')
        receipt = json.loads(receipt_path.read_text())
        extension = '.zip' if host.startswith('windows-') else '.tar.gz'
        name = 'nyra-sdk-' + plan['version'] + '-' + host + extension
        archive = directory / name
        if receipt.get('host') != host or receipt.get('version') != plan['version'] or receipt.get('source_commit') != plan['source_commit']:
            raise ValueError('public release receipt identity mismatch')
        if receipt['archive'] != {'path': name, 'sha256': sha(archive), 'bytes': archive.stat().st_size}:
            raise ValueError('public SDK archive checksum or size mismatch')
        validate_archive(archive, receipt, plan, host)
        reports.append(receipt)
        files.extend((archive, receipt_path))
    unexpected = {p.name for p in directory.iterdir()} - {f.name for f in files} - {'release.json', 'SHA256SUMS'}
    if unexpected:
        raise ValueError('publication directory contains unapproved files')
    metadata = {'schema_version': 1, 'version': plan['version'], 'source_commit': plan['source_commit'],
        'targets': policy()['targets'], 'hosts': policy()['hosts'], 'releases': reports,
        'signing': 'not-attested', 'control_commit': os.environ.get('GITHUB_SHA', '')}
    write(directory / 'release.json', metadata)
    files.append(directory / 'release.json')
    (directory / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in sorted(files)))
    files.append(directory / 'SHA256SUMS')
    if not args.publish:
        print('Publication inputs validated; no remote changes made')
        return
    if os.environ.get('GITHUB_REF_TYPE') != 'tag' or os.environ.get('GITHUB_REF_NAME') != args.tag:
        raise ValueError('publishing requires the matching pushed tag workflow')
    token = os.environ.get('SDK_RELEASE_TOKEN', '')
    if not token:
        raise ValueError('SDK_RELEASE_TOKEN is not configured')
    github = Github(policy()['public_repository'], token)
    repo = github.api('')
    if repo['private'] or repo['full_name'] != policy()['public_repository']:
        raise ValueError('SDK destination must be the expected public repository')
    marker = 'Nyra SDK ' + args.tag + '\nSource: ' + plan['source_commit']
    body = marker + '\n\n' + plan['notes'] + '\n\nUnsigned and not notarized. macOS x86_64 execution environment is recorded in release.json.'
    try:
        release = github.api('/releases/tags/' + args.tag)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        commit = github.api('/commits/' + urllib.parse.quote(repo['default_branch'], safe=''))['sha']
        release = github.api('/releases', {'tag_name': args.tag, 'target_commitish': commit,
            'name': 'Nyra SDK ' + args.tag, 'body': body, 'draft': True, 'prerelease': False})
    if not release['draft']:
        raise ValueError('published versions are immutable; create a new version')
    if release['body'] != body or release['prerelease']:
        raise ValueError('existing draft belongs to a different release plan')
    assets = {a['name']: a for a in github.api('/releases/' + str(release['id']) + '/assets?per_page=100')}
    expected = {p.name: p for p in files}
    if set(assets) - set(expected):
        raise ValueError('existing draft contains unexpected assets; inspect before retrying')
    for name, path in expected.items():
        asset = assets.get(name) or github.upload(release, path)
        if asset.get('size') != path.stat().st_size or asset.get('digest') != 'sha256:' + sha(path) or asset.get('state') != 'uploaded':
            raise ValueError('remote asset SHA-256, size or state mismatch: ' + name)
    remote = github.api('/releases/' + str(release['id']) + '/assets?per_page=100')
    if len(remote) != len(expected) or {a['name'] for a in remote} != set(expected):
        raise ValueError('remote release inventory is incomplete')
    for asset in remote:
        path = expected[asset['name']]
        if asset.get('digest') != 'sha256:' + sha(path) or asset['size'] != path.stat().st_size:
            raise ValueError('remote asset changed before publication')
    published = github.api('/releases/' + str(release['id']), {'draft': False}, method='PATCH')
    if published['draft']:
        raise RuntimeError('GitHub did not confirm publication')
    print('Published ' + published['html_url'])


if __name__ == '__main__':
    main()
