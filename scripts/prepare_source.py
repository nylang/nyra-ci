"""Resolve a manual candidate source ref once for every matrix worker."""
import json
import os
from pathlib import Path
import re
import tempfile
from release_common import checkout

HOSTS = {
    'darwin-arm64': ['self-hosted', 'macOS', 'ARM64', 'nyra-sdk', 'nyra-macos-arm64'],
    'darwin-x86_64': ['self-hosted', 'macOS', 'X64', 'nyra-sdk', 'nyra-macos-x86_64'],
    'linux-x86_64': ['self-hosted', 'Linux', 'X64', 'nyra-sdk', 'nyra-linux-x64'],
    'windows-x86_64': ['self-hosted', 'Windows', 'X64', 'nyra-sdk', 'nyra-windows-x64'],
}


def matrix(selection):
    hosts = selection.split(',') if selection else list(HOSTS)
    if not hosts or len(hosts) != len(set(hosts)) or any(host not in HOSTS for host in hosts):
        raise ValueError('select distinct supported native hosts')
    return {'include': [dict(host=host, labels=HOSTS[host]) for host in hosts]}


def main():
    ref = os.environ['NYRA_CI_SOURCE_REF']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]*', ref):
        raise ValueError('invalid source ref')
    selected = matrix(os.environ.get('NYRA_CI_HOSTS', ''))
    with tempfile.TemporaryDirectory(prefix='nyra-ci-source-') as temp:
        commit = checkout(ref, Path(temp) / 'source')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('source_commit=' + commit + '\n')
        output.write('matrix=' + json.dumps(selected, separators=(',', ':')) + '\n')
    print('Frozen source commit: ' + commit)


if __name__ == '__main__':
    main()
