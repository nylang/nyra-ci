"""Update the public landing documents from the same immutable release plan."""
import base64
import json
import re
from release_common import policy


def update(github, plan):
    version = plan['version']
    repo = github.api('')
    branch = repo['default_branch']
    ref = github.api('/git/ref/heads/' + branch)
    parent = ref['object']['sha']
    previous = github.api('/contents/README.md?ref=' + parent)
    readme = base64.b64decode(previous['content']).decode()
    current = re.search(r'Download \[Nyra SDK (\d+\.\d+\.\d+)\]', readme)
    if current and tuple(map(int, current[1].split('.'))) >= tuple(map(int, version.split('.'))):
        return  # Retried older publications must not move the landing page back.
    base = 'https://github.com/' + policy()['public_repository'] + '/releases/'
    text = '# Nyra SDK\n\nOfficial binary distributions of the Nyra programming language.\n\n'
    text += 'Download [Nyra SDK ' + version + '](' + base + 'tag/v' + version + ').\n'
    text += 'Each native SDK includes nyrac, nyra-analyzer, nyra, pinned LLVM and its native runtime.\n\n## Native SDKs\n\n'
    for host in policy()['hosts']:
        file = 'nyra-sdk-' + version + '-' + host + ('.zip' if host.startswith('windows-') else '.tar.gz')
        text += '- [' + host + '](' + base + 'download/v' + version + '/' + file + ')\n'
    text += '\n## Runtime packages\n\n'
    for group, targets in policy()['runtime_groups'].items():
        file = 'nyra-runtime-' + version + '-' + group + '.tar.gz'
        text += '- [' + group + '](' + base + 'download/v' + version + '/' + file + '): ' + ', '.join(targets) + '\n'
    text += '\nDeclare runtime dependencies through packages/<name>/package.yaml; manifests select target leaves. CPU/ISA, ABI and object format must match. Freestanding kernel/ntest require consumer-owned providers.\n\n'
    text += 'Native qualification covers G1/G2 identity and the complete G2 suites once, plus bootstrap-built tool units once. Publication preserves accepted bytes. Runtime execution/compile-only levels are recorded per target. macOS x86_64 currently uses Rosetta. Compile-only does not establish runtime execution or a linked host SDK.\n\n'
    text += '[Installation](INSTALL.md), [release notes](RELEASE-' + version + '.md), [acceptance](ACCEPTANCE-' + version + '.json), [license](LICENSE.txt).\n'
    installation = '# Install Nyra SDK ' + version + '\n\nDownload the archive matching your host from the release. Check its SHA-256 against SHA256SUMS, extract it, and add hosts/<host>/bin to PATH. Keep the full directory together so tools can resolve LLVM and packages.\n\nAdditional targets use the separate runtime archives listed in README.md. Their package roots are packages/kernel/package.yaml, packages/ntest/package.yaml and, for hosted targets, packages/stdlib/package.yaml. Preserve the archive layout when moving it. amd64 is x86_64; aarch64 is arm64.\n\nmacOS tools require macOS 14 or newer; Intel execution currently uses Rosetta qualification. Binaries are not Developer ID signed/notarized. Compile-only target evidence does not prove execution. Windows ARM64 consumer linking needs the matching MSVC static CRT.\n'
    acceptance = dict(version=version, source_commit=plan['source_commit'], validation_policy='g1-g2-v1',
                      hosts=policy()['hosts'], runtime_execution_required=policy()['execution_required'],
                      compile_only_targets=sorted(set(policy()['targets']) - set(policy()['execution_required'])),
                      complete_suites_repeated_at_publication=False)
    documents = {'README.md': text, 'INSTALL.md': installation,
                 'RELEASE-' + version + '.md': '# Nyra SDK ' + version + '\n\n' + plan['notes'] + '\n\n' + text[text.index('Native qualification'):],
                 'ACCEPTANCE-' + version + '.json': json.dumps(acceptance, indent=2) + '\n'}
    tree = []
    for name, content in documents.items():
        blob = github.api('/git/blobs', dict(content=content, encoding='utf-8'))
        tree.append(dict(path=name, mode='100644', type='blob', sha=blob['sha']))
    old = github.api('/git/commits/' + parent)
    new_tree = github.api('/git/trees', dict(base_tree=old['tree']['sha'], tree=tree))
    commit = github.api('/git/commits', dict(message='release: document SDK ' + version,
                        tree=new_tree['sha'], parents=[parent]))
    github.api('/git/refs/heads/' + branch, dict(sha=commit['sha'], force=False), method='PATCH')
