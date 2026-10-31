"""Admit an explicitly pinned native compiler for a source compatibility transition.

This input replaces only G0. It is never a released SDK, and cannot substitute
for the normal frozen-source G1/G2 and complete-suite qualification.
"""
import argparse
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
from release_common import ROOT, sha, write


def selected_compiler(host, source, sdk, cache):
    version = (source / 'VERSION').read_text().strip()
    config = ROOT / 'transitional-seeds.json'
    transitions = json.loads(config.read_text()) if config.is_file() else {
        'schema_version': 1, 'required_versions': [], 'compilers': {}}
    if transitions.get('schema_version') != 1:
        raise ValueError('unsupported transition compiler manifest')
    entry = transitions['compilers'].get(version)
    suffix = '.exe' if host.startswith('windows-') else ''
    if entry is None:
        # Ordinary releases still bootstrap from the selected preceding SDK.
        # Only a recorded incompatible API transition needs a different G0.
        pinned = json.loads((ROOT / 'sdk-seeds.json').read_text())['version']
        if version in transitions['required_versions']:
            raise ValueError('source version needs an explicit native transition seed: ' + version)
        compiler = sdk / 'hosts' / host / 'bin' / ('nyrac' + suffix)
        return compiler, dict(mode='released-sdk', version=pinned, sha256=sha(compiler))
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    if entry['source_commit'] != commit:
        raise ValueError('transition seed is pinned to a different frozen source commit')
    row = entry['hosts'][host]
    digest = row['sha256']
    if not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('invalid transition compiler checksum')
    compiler = cache / 'nyra-bootstrap-transition' / version / host / ('nyrac' + suffix)
    if not compiler.is_file() or sha(compiler) != digest:
        raise ValueError('missing or changed explicit transition compiler: ' + str(compiler))
    return compiler, dict(mode='explicit-transition', source_commit=commit,
                          sha256=digest, provenance=row['provenance'])


def link(inputs, sdk, host, output):
    """Link authenticated cross-compiled ObjectUnits on their matching native host."""
    record = json.loads((inputs / 'inputs.json').read_text())
    machine = platform.machine().lower()
    native = platform.system().lower() + '-' + ('arm64' if machine in ('arm64', 'aarch64') else 'x86_64')
    if host != native:
        raise ValueError('transition seed must link on its matching native host')
    if record['host'] != host or record.get('qualification') is not False:
        raise ValueError('invalid transition object scope')
    if 'nyra_llvm_initialize_all.c' not in record['files']:
        raise ValueError('missing authenticated LLVM bridge source')
    objects = []
    for name, digest in record['files'].items():
        if '/' in name or '\\' in name or name in ('.', '..'):
            raise ValueError('invalid transition input path')
        path = inputs / name
        if not path.is_file() or sha(path) != digest:
            raise ValueError('transition object changed: ' + name)
        if name.endswith('.o'):
            objects.append(path)
    if len(objects) != record['object_units'] or not objects:
        raise ValueError('incomplete transition ObjectUnits')
    suffix = '.exe' if host.startswith('windows-') else ''
    kit = sdk / 'hosts' / host / 'llvm'
    cc = kit / 'bin' / ('clang' + suffix)
    output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output).free < 80 * 1024**3:
        raise ValueError('transition seed requires 80 GiB storage reserve')
    bridge = output / 'llvm-bridge.o'
    native_flags = []
    if host.startswith('darwin-'):
        sysroot = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
        native_flags = ['-isysroot', sysroot, '-mmacosx-version-min=14.0']
    command = [cc, *native_flags, '-std=c11', '-O2', '-I', kit / 'include', '-c',
               inputs / 'nyra_llvm_initialize_all.c', '-o', bridge]
    subprocess.run(list(map(str, command)), check=True)
    system = shlex.split(subprocess.check_output(
        [str(kit / 'bin' / ('llvm-config' + suffix)), '--link-static', '--system-libs'], text=True),
        posix=not host.startswith('windows-'))
    libraries = []
    for word in system:
        word = word.strip('"')
        if host.startswith('windows-'):
            packaged = kit / 'lib' / word
            if packaged.is_file():
                word = str(packaged)
            elif word.endswith('.lib'):
                word = '-l' + word[:-4]
            else:
                raise ValueError('unsupported Windows LLVM provider: ' + word)
        elif word.endswith('/libzstd.a') or word == 'libzstd.a':
            word = str(kit / 'lib/libzstd.a')
        libraries.append(word)
    metadata = json.loads((sdk / 'manifest.json').read_text())
    target = next(row for row in metadata['targets'] if row['key'] == host)
    stdlib = next(row for row in target['packages'] if row['name'] == 'stdlib')
    platform_archive = (sdk / stdlib['path'] / 'lib/libnyra_platform.a').resolve()
    if not platform_archive.is_relative_to(sdk.resolve()) or not platform_archive.is_file():
        raise ValueError('missing matching platform archive')
    flags = []
    if host.startswith('darwin-'):
        sysroot = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
        flags = ['-Wl,-stack_size,0x20000000', str(Path(sysroot) / 'usr/lib/libc++.tbd'),
                 str(Path(sysroot) / 'usr/lib/libc++abi.tbd')]
    elif host.startswith('linux-'):
        flags = ['-fuse-ld=' + str(kit / 'bin/ld.lld'), '-Wl,-z,stack-size=33554432']
        libraries.append('-lstdc++')
    else:
        flags = ['-fuse-ld=lld', '-Wl,/STACK:33554432', '-Wl,/Brepro',
                 '-Wl,/call-graph-profile-sort:no', '-lws2_32', '-lcrypt32', '-lbcrypt',
                 '-ladvapi32', '-luser32', '-lshell32', '-lole32', '-lpsapi']
    binary = output / ('nyrac' + suffix)
    command = [cc, '--target=' + record['triple'], *native_flags, '-o', binary, *flags]
    if host.startswith('linux-'):
        command += ['-Wl,--start-group']
    command += [*objects, bridge, platform_archive, kit / 'lib/libLLVM.a', *libraries]
    if host.startswith('linux-'):
        command += ['-Wl,--end-group']
    response = output / 'link.rsp'
    # Clang response files handle Windows path spaces without shell evaluation.
    response.write_text('\n'.join('"' + str(p).replace('\\', '/') + '"' for p in command[1:]) + '\n')
    subprocess.run([str(cc), '@' + str(response)], check=True)
    if host.startswith('darwin-'):
        subprocess.run(['codesign', '--force', '--sign', '-', str(binary)], check=True)
    version = subprocess.check_output([str(binary), 'version'], text=True).strip()
    write(output / 'provenance.json', dict(record, compiler_sha256=sha(binary), version=version,
          platform_sha256=sha(platform_archive), llvm_sha256=sha(kit / 'lib/libLLVM.a'),
          bridge_object_sha256=sha(bridge), purpose='native G0 input only; not SDK qualification'))
    response.unlink()
    bridge.unlink()
    return binary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    print(link(args.inputs.resolve(), args.sdk.resolve(), args.host, args.out_dir.resolve()))
