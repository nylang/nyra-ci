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
import uuid
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
    targets = {host}
    if len(rows) != len(targets) or {r['key'] for r in rows} != targets:
        raise ValueError('SDK target capability regression')
    for row in rows:
        packages = {p['name'] for p in row['packages']}
        expected = set(policy()['packages'])
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
    required_licenses = {'Nyra SDK', 'yaml', 'json', 'nllvm', 'LLVM', 'libgit2'}
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
        # Release state changes during uploads. Bypass intermediary caches so a
        # stale empty/starter inventory cannot cause redundant uploads/deletion.
        if payload is None and method in (None, 'GET'):
            suffix += ('&' if '?' in suffix else '?') + '_nyra_read=' + uuid.uuid4().hex
        request = urllib.request.Request('https://api.github.com/repos/' + self.repository + suffix,
            data=json.dumps(payload).encode() if payload is not None else None, method=method,
            headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                     'Content-Type': 'application/json', 'User-Agent': 'Nyra-SDK-release',
                     'Cache-Control': 'no-cache'})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read()).get('message', '')
            except (ValueError, UnicodeError):
                message = ''
            finally:
                error.close()
            required = error.headers.get('X-Accepted-GitHub-Permissions', '')
            details = ' '.join(str(message).split())[:1000]
            if required:
                details += ' (required permissions: ' + required + ')'
            if details:
                error.msg = str(error.msg) + ': ' + details
            raise

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


def find_release(github, tag):
    """Find published versions or a unique draft without creating duplicates."""
    try:
        return github.api('/releases/tags/' + urllib.parse.quote(tag, safe=''))
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        error.close()
    # The by-tag endpoint returns published releases. Drafts require listing
    # releases with an authenticated actor that has access to the draft.
    matches = []
    page = 1
    while True:
        rows = github.api('/releases?per_page=100&page=' + str(page))
        matches.extend(row for row in rows if row['tag_name'] == tag)
        if len(rows) < 100:
            break
        page += 1
    if len(matches) > 1:
        raise ValueError('multiple releases use this tag; inspect before retrying')
    return matches[0] if matches else None


def main():
    # Preserve the historical entry point while using the sealed v2 controller.
    from publish_bundle import main as publish_main
    publish_main()


if __name__ == '__main__':
    main()
