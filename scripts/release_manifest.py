"""Seal the exact source-free publication inventory after qualification."""
import json
import os
from pathlib import Path
from release_common import policy, sha, write


def seal(directory, plan):
    directory = Path(directory)
    records = []
    expected = set()
    for host in policy()['hosts']:
        file = 'release-' + host + '.json'
        receipt = json.loads((directory / file).read_text())
        if (receipt['host'], receipt['version'], receipt['source_commit'], receipt['validation_policy'], receipt['success']) != (host, plan['version'], plan['source_commit'], 'g1-g2-v1', True):
            raise ValueError('native qualification receipt identity mismatch')
        archive = receipt['archive']
        name = 'nyra-sdk-' + plan['version'] + '-' + host + ('.zip' if host.startswith('windows-') else '.tar.gz')
        path = directory / name
        if archive != dict(path=name, sha256=sha(path), bytes=path.stat().st_size):
            raise ValueError('qualified native archive changed')
        expected.update((file, name))
    expected.update('nyra-runtime-' + plan['version'] + '-' + group + '.tar.gz' for group in policy()['runtime_groups'])
    if {p.name for p in directory.iterdir()} != expected:
        raise ValueError('publication inventory contains missing or unexpected files')
    for name in sorted(expected):
        path = directory / name
        records.append(dict(name=name, sha256=sha(path), bytes=path.stat().st_size))
    write(directory / 'release.json', dict(schema_version=2, version=plan['version'], source_commit=plan['source_commit'],
          control_commit=os.environ['GITHUB_SHA'], qualification_run=os.environ['GITHUB_RUN_ID'],
          validation_policy='g1-g2-v1', hosts=policy()['hosts'], targets=policy()['targets'], assets=records))
    files = [directory / name for name in sorted(expected | {'release.json'})]
    (directory / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in files))
