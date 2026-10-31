"""Produce assigned CPU/ABI runtime packages from the qualified source snapshot."""
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from release_common import ROOT, policy, sha, write, state_path
from build_sdk import cache_root, run


def settings():
    return json.loads((cache_root() / 'nyra-providers/targets.json').read_text())


def target(key, llvm, config):
    from tools.build.targets import Target, resolve_target
    @dataclass(frozen=True)
    class ConfiguredTarget(Target):
        includes: tuple = ()
        def c_flags(self):
            flags = super().c_flags()
            if self.key.startswith('windows-'):
                flags = [flag for flag in flags if flag != '-fPIC']
            return flags + [x for p in self.includes for x in ('-I', p)]
    options = config.get('targets', {}).get(key, {})
    if key.startswith('android-'):
        return resolve_target(key, ndk=Path(config['ndk']), api=policy()['android_api'])
    if key.startswith('ios-'):
        row = resolve_target('ios-simulator-arm64' if 'simulator' in key else 'ios-arm64')
        return replace(row, key=key, arch='x86_64', triple='x86_64-apple-ios15.0-simulator') if key.endswith('x86_64') else row
    arch = 'riscv64' if key.startswith('rv64') or key.endswith('riscv64') else 'arm64' if key.endswith('arm64') else 'x86_64'
    cpu_arch = 'aarch64' if arch == 'arm64' else arch
    hosted = not key.startswith(('rv64', 'none-'))
    soft = key == 'rv64imac-lp64'
    rv = arch == 'riscv64'
    abi = 'lp64' if soft else 'lp64d' if rv else ''
    features = '+m,+a,+c,-f,-d' if soft else '+m,+a,+f,+d,+c' if rv else ''
    selector = 'darwin' if key.startswith('darwin') else 'windows' if key.startswith('windows') else 'linux' if hosted else 'NyraOS' if soft else 'none'
    triple = cpu_arch + ('-apple-macosx14.0' if selector == 'darwin' else '-pc-windows-msvc' if selector == 'windows' else '-unknown-linux-musl' if 'musl' in key else '-unknown-linux-gnu' if hosted else '-unknown-elf' if rv else '-unknown-none')
    sysroot = options.get('sysroot', '')
    if selector == 'darwin':
        triple = arch + '-apple-macosx14.0'
        sysroot = subprocess.check_output(['xcrun', '--sdk', 'macosx', '--show-sdk-path'], text=True).strip()
    suffix = '.exe' if os.name == 'nt' else ''
    return ConfiguredTarget(key, triple, selector, arch, 'generic-rv64' if rv else '', features, abi,
            'rv64imac' if soft else 'rv64gc' if rv else '', hosted, sysroot,
            str(llvm / 'bin' / ('clang' + suffix)), str(llvm / 'bin' / ('llvm-ar' + suffix)),
            str(llvm / 'bin' / ('ld.lld' + suffix)), '', 'pinned-release-llvm', tuple(options.get('includes', [])))


def produce():
    build = json.loads((ROOT / 'build-receipt.json').read_text())
    if not build['success']:
        raise ValueError('native qualification must pass first')
    host = build['host']
    source = Path(build['source'])
    sys.path.insert(0, str(source))
    from tools.build.sdk_manifest import read_package, relocate_packages, validate_object
    from tools.build import build_platform
    from runtime_consumer import accept
    sdk = Path(build['sdk'])
    snapshot = Path(build['workspace']) / 'bootstrap/native-generations/snapshot'
    compiler = sdk / 'hosts' / host / 'bin' / ('nyrac.exe' if os.name == 'nt' else 'nyrac')
    llvm = sdk / 'hosts' / host / 'llvm'
    config = settings()
    output = Path(build['workspace']) / 'runtimes'
    output.mkdir()
    lock = json.loads((sdk / 'share/nyra/source-package-lock.json').read_text())
    build_platform.SRC = snapshot / 'source/platform/src'
    build_platform.INCLUDE = snapshot / 'source/platform/include'
    records = []
    host_records = []
    for key in policy()['runtime_producers'][host]:
        from tools.ci.runner_storage import check_space
        check_space(output)
        row = target(key, llvm, config)
        packages = ('kernel', 'ntest', 'stdlib') if row.hosted else ('kernel', 'ntest')
        dest = output / 'targets' / key
        for name in packages:
            run([compiler, *row.nyra_flags(), 'precompile', snapshot / 'source' / name / (name + '.ny'),
                 '-O2', '-o', dest / name], snapshot, os.environ | {'AR': row.ar})
            package = read_package(dest / name)
            validate_object(dest / name / package['archive'], row.arch,
                            'windows' if key.startswith('windows') else 'darwin' if key.startswith('darwin') else 'ios' if key.startswith('ios') else 'linux', abi=row.abi)
        if row.hosted:
            platform = build_platform.build(output / 'platform', key, row.cc, target=row)
            shutil.copy2(platform, dest / 'stdlib/lib/libnyra_platform.a')
        else:
            # Record whole-package external provider obligations; no OS assumed.
            names = set()
            definitions = set()
            for package in packages:
                archive = read_package(dest / package)['archive']
                text = subprocess.check_output([str(llvm / 'bin' / ('llvm-nm.exe' if os.name == 'nt' else 'llvm-nm')), '--undefined-only', str(dest / package / archive)], text=True)
                names.update(line.split()[-1] for line in text.splitlines() if len(line.split()) >= 2 and line.split()[-2] == 'U')
                defined = subprocess.check_output([str(llvm / 'bin' / ('llvm-nm.exe' if os.name == 'nt' else 'llvm-nm')), '--defined-only', '--extern-only', str(dest / package / archive)], text=True)
                definitions.update(line.split()[-1] for line in defined.splitlines() if len(line.split()) >= 3)
            write(dest / 'provider-requirements.json', dict(profile=key, scope='Whole-package external upper bound; reachable symbols depend on the consumer.', required_symbols=sorted(names - definitions), implicit_hosted_libraries=False))
        relocate_packages(output, key, lock, packages=packages)
        evidence = accept(row, compiler, llvm, dest, output / 'consumers' / key, source, config)
        required = policy()['execution_required'].get(key, 'compile-only')
        if evidence['acceptance_level'] != required or evidence.get('status') != 'passed':
            raise ValueError('runtime acceptance incomplete for ' + key)
        if key in ('linux-arm64', 'linux-riscv64', 'windows-arm64'):
            from host_compile import compile_host
            host_records.append(compile_host(row, compiler, llvm, snapshot, Path(build['workspace']) / 'bootstrap', output / 'host-objects' / key))
        records.append(dict(key=key, triple=row.triple, abi=row.abi, hosted=row.hosted,
                            packages=list(packages), **evidence))
        # Successful consumer compilation/link/run has no remaining object or
        # Core dump consumer. Preserve its bounded receipts and source inputs.
        consumer = output / 'consumers' / key
        for path in consumer.rglob('*'):
            if path.is_file() and path.suffix in ('.o', '.obj', '.ast', '.ll', '.bc'):
                path.unlink()
    write(output / 'catalog.json', dict(host=host, version=build['version'], source_commit=build['source_commit'],
          control_commit=os.environ['GITHUB_SHA'], targets=records, host_compilation=host_records,
          files={p.relative_to(output).as_posix(): sha(p) for p in (output / 'targets').rglob('*') if p.is_file()}))
    # Runtime catalogs contain public package payload and metadata only.
    shutil.rmtree(ROOT / 'runtime-output', ignore_errors=True)
    shutil.copytree(output / 'targets', ROOT / 'runtime-output/targets')
    shutil.copy2(output / 'catalog.json', ROOT / 'runtime-output/catalog.json')
    write(state_path(host), build)


if __name__ == '__main__':
    produce()
