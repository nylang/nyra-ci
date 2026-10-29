"""Verify managed native providers before spending time on compiler qualification."""
import json
from pathlib import Path
import shutil
import subprocess
from release_common import sha
from build_sdk import cache_root, native_environment, seed_sdk


def verify_providers(host):
    root = cache_root() / 'nyra-providers'
    inputs = json.loads((root / 'inputs.json').read_text())
    if not inputs:
        raise ValueError('managed provider inventory is empty')
    for name, digest in inputs.items():
        path = root / name
        if path.is_symlink() or not path.is_file() or sha(path) != digest:
            raise ValueError('managed provider changed: ' + name)
    if not (root / 'openssl-3.5.9/include/openssl/ssl.h').is_file():
        raise ValueError('OpenSSL development headers missing')
    return dict(provider_inventory_sha256=sha(root / 'inputs.json'), provider_files=len(inputs))


def verify_build_tools(host, sdk):
    env = native_environment(sdk, host)
    if host == 'darwin-x86_64':
        # CMake/Ninja are orchestration tools; x86 Clang determines code output.
        # The ARM Homebrew prefix is added only after the pinned x86 LLVM tools.
        env['PATH'] += ':/opt/homebrew/bin'
    for tool in ('cmake', 'ninja'):
        path = shutil.which(tool, path=env['PATH'])
        if not path:
            raise ValueError('missing build orchestrator: ' + tool)
        subprocess.run([path, '--version'], env=env, check=True, stdout=subprocess.DEVNULL)
    return env
