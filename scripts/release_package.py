"""Assemble separately qualified runtime catalogs; preserve native archive bytes."""
import json
import os
from pathlib import Path
import shutil
import sys
from release_common import ROOT, spec, policy, sha, write, state_path, safe_name


def assemble():
    plan = spec(os.environ['NYRA_CI_RELEASE_TAG'])
    state = json.loads(state_path('darwin-arm64').read_text())
    if not state['success'] or state['source_commit'] != plan['source_commit']:
        raise ValueError('retained source snapshot differs from release plan')
    sys.path.insert(0, state['source'])
    from tools.build.sdk_manifest import relocate_packages, package_first_layout, inventory
    from tools.build.distribution import _archive
    from public_policy import _payload_allowed
    output = ROOT / 'publish-input'
    output.mkdir(exist_ok=True)
    work = Path(state['workspace']) / 'runtime-bundles'
    work.mkdir(exist_ok=False)
    lock = json.loads((Path(state['sdk']) / 'share/nyra/source-package-lock.json').read_text())
    targets = {}
    for host in policy()['hosts']:
        base = ROOT / 'catalog-input' / ('runtime-catalog-' + host)
        catalog = json.loads((base / 'catalog.json').read_text())
        if (catalog['host'], catalog['version'], catalog['source_commit'], catalog['control_commit']) != (host, plan['version'], plan['source_commit'], os.environ['GITHUB_SHA']):
            raise ValueError('runtime catalog producer identity mismatch')
        actual = {p.relative_to(base).as_posix(): p for p in (base / 'targets').rglob('*') if p.is_file()}
        if set(actual) != set(catalog['files']):
            raise ValueError('runtime catalog inventory mismatch')
        for name, digest in catalog['files'].items():
            safe_name(name)
            if actual[name].is_symlink() or sha(actual[name]) != digest:
                raise ValueError('runtime catalog changed: ' + name)
        rows = catalog['targets']
        if {r['key'] for r in rows} != set(policy()['runtime_producers'][host]):
            raise ValueError('runtime producer target coverage mismatch')
        for row in rows:
            key = row['key']
            if key in targets or row.get('status') != 'passed' or row['acceptance_level'] != policy()['execution_required'].get(key, 'compile-only'):
                raise ValueError('duplicate target or incomplete runtime acceptance')
            targets[key] = (row, base / 'targets' / key)
    if set(targets) != set(policy()['targets']):
        raise ValueError('release runtime target coverage incomplete')
    for label, keys in policy()['runtime_groups'].items():
        payload = work / ('nyra-runtime-' + plan['version'] + '-' + label)
        payload.mkdir()
        rows = []
        for key in keys:
            info, source = targets[key]
            for name in info['packages']:
                shutil.copytree(source / name, payload / 'targets' / key / name)
            records = relocate_packages(payload, key, lock, packages=tuple(info['packages']))
            records = package_first_layout(payload, key, records, profile='hosted' if info['hosted'] else key)
            rows.append({k: info[k] for k in ('key', 'triple', 'abi', 'hosted', 'acceptance_level', 'consumer_executable_linked')} | {'packages': records})
            if not info['hosted']:
                contracts = payload / 'providers'
                contracts.mkdir(exist_ok=True)
                shutil.copy2(source / 'provider-requirements.json', contracts / (key + '.json'))
        for row in rows:
            for record in row['packages']:
                record['index_sha256'] = sha(payload / record['index'])
        shutil.rmtree(payload / 'targets')
        shutil.copy2(Path(state['source']) / 'licenses/NYRA-SDK-LICENSE.txt', payload / 'LICENSE.txt')
        (payload / 'README.md').write_text('Nyra precompiled runtime packages ' + plan['version'] + '\n\nDeclare dependencies through packages/<name>/package.yaml. Select an exact LLVM triple and matching CPU/ABI profile. Freestanding providers belong to the consumer. Acceptance levels are recorded in manifest.json.\n')
        for p in payload.rglob('*'):
            if not p.is_file():
                continue
            name = p.relative_to(payload).as_posix()
            if name not in ('LICENSE.txt', 'README.md') and not name.startswith('providers/') and not _payload_allowed(name, 'darwin-arm64', set(keys)):
                raise ValueError('unsupported public runtime payload: ' + name)
        write(payload / 'manifest.json', dict(format='nyra-runtime-packages-v1', version=plan['version'], group=label,
              source_revision=plan['source_commit'], targets=rows, files=inventory(payload)))
        archive = output / (payload.name + '.tar.gz')
        _archive(payload, archive, windows=False)
    from release_manifest import seal
    seal(output, plan)


if __name__ == '__main__':
    assemble()
