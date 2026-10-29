"""Link target consumers and enforce the configured native/virtual evidence level."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from release_common import policy, sha, write
from build_sdk import run

SOURCE = '''import "stdlib:stdlib.ny";
import "ntest:ntest.ny";
func main() -> int32 {
  var values: Array<int32> = Array<int32>{};
  values.Push(40);
  values.Push(2);
  ClearFailCount();
  if AssertEqI32(values.Get(0) + values.Get(1), 42) != 0 { return 1; }
  if FailMsg("target-message") != 1 || FailCount() != 1 { return 2; }
  Console.Println("target-runtime-ok");
  return 0;
}
'''
COUNTER = '''#include <stdint.h>
static int32_t count;
int32_t FailCount(void) {return count;}
void ClearFailCount(void) {count=0;}
void AddFail(int32_t n) {count+=n;}
'''


def accept(row, compiler, llvm, packages, out, source, config):
    out.mkdir(parents=True)
    if not row.hosted:
        if row.key != 'none-x86_64':
            command = [sys.executable, str(source / 'tests/tools/ntest_portable_acceptance.py'),
                       '--compiler', str(compiler), '--packages', str(packages), '--llvm', str(llvm),
                       '--profile', row.key, '--out-dir', str(out)]
            if row.key == 'rv64imac-lp64':
                command += ['--compiler-rt', config['compiler_rt']]
            run(command)
            report = json.loads((out / 'acceptance.json').read_text())
            if not report.get('run') or not report.get('link'):
                raise ValueError('freestanding execution failed')
            return dict(status='passed', acceptance_level='virtual-execution', consumer_executable_linked=True)
        # Compile-only is backed by the package archive object/ABI checks.
        return dict(status='passed', acceptance_level='compile-only', consumer_executable_linked=False,
                    limitation='No configured x86_64 freestanding execution provider.')
    (out / 'main.ny').write_text(SOURCE)
    (out / 'counter.c').write_text(COUNTER)
    (out / 'package.yaml').write_text('name: target_consumer\ndependencies:\n' + ''.join(
        f'  {n}:\n    precompiled: {json.dumps(str(packages / n))}\n' for n in ('kernel', 'stdlib', 'ntest')))
    manifest = out / 'objects.list'
    run([str(compiler), *row.nyra_flags(), 'build', str(out / 'main.ny'), '-O2',
         '--out-dir', str(out / 'objects'), '--object-manifest', str(manifest)])
    objects = manifest.read_text().splitlines()
    counter = out / 'counter.o'
    run([row.cc, *row.c_flags(), '-c', str(out / 'counter.c'), '-o', str(counter)])
    platform = packages / 'stdlib/lib/libnyra_platform.a'
    exe = out / ('consumer.exe' if row.key.startswith('windows') else 'consumer')
    options = config.get('targets', {}).get(row.key, {})
    if row.key.startswith('windows'):
        libraries = options.get('library_dirs', [])
        if row.arch == 'arm64' and not any((Path(p) / 'libcmt.lib').is_file() for p in libraries):
            return dict(status='passed', acceptance_level='compile-only', consumer_executable_linked=False,
                        limitation='MSVC ARM64 static CRT unavailable; target packages, consumer objects and platform archive compiled.')
        command = [row.cc, *row.c_flags(), '-fuse-ld=lld', str(counter), *objects, str(platform),
                   *['-Wl,/libpath:' + p for p in libraries], '-o', str(exe)]
    elif row.key.startswith(('ios-', 'darwin-')):
        command = ['/usr/bin/clang', '--target=' + row.triple, '-isysroot', row.sysroot,
                   str(counter), *objects, str(platform), '-o', str(exe)]
    elif row.key.startswith('android-'):
        command = [row.cc, *row.c_flags(), str(counter), *objects, str(platform), *row.runtime_link_flags(), '-o', str(exe)]
    elif row.key == 'linux-x86_64':
        command = [row.cc, str(counter), *objects, str(platform), '-pthread', '-ldl', '-lm', '-o', str(exe)]
    else:
        root = Path(row.sysroot)
        lib = root / ('usr/lib' if 'musl' in row.key else 'lib')
        loader = ('ld-musl-aarch64.so.1' if row.arch == 'arm64' else 'ld-musl-x86_64.so.1') if 'musl' in row.key else ('ld-linux-aarch64.so.1' if row.arch == 'arm64' else 'ld-linux-riscv64-lp64d.so.1')
        libc = lib / ('libc.so' if 'musl' in row.key else 'libc.so.6')
        command = [row.linker, '-pie', '--dynamic-linker', '/lib/' + loader, '-o', str(exe),
                   str(lib / 'Scrt1.o'), str(lib / 'crti.o'), str(counter), *objects,
                   str(platform), str(libc), str(lib / 'crtn.o')]
        if 'musl' not in row.key:
            command.append(str(lib / 'libm.so.6'))
    run(command)
    level = policy()['execution_required'].get(row.key, 'compile-only')
    if level != 'compile-only':
        if row.key == 'ios-simulator-arm64':
            sim = config['ios_simulator']
            args = ['xcrun', 'simctl', '--set', sim['set']]
            state = json.loads(subprocess.check_output([*args, 'list', 'devices', '-j'], text=True))
            device = next(d for group in state['devices'].values() for d in group if d['udid'] == sim['device'])
            if device['state'] != 'Booted':
                run([*args, 'boot', sim['device']])
            run([*args, 'bootstatus', sim['device'], '-b'])
            # CoreSimulator must be able to load the executable from its data dir.
            home = Path(subprocess.check_output([*args, 'getenv', sim['device'], 'HOME'], text=True).strip())
            destination = home / 'tmp' / ('nyra-ci-' + os.environ.get('GITHUB_RUN_ID', 'manual'))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exe, destination)
            try:
                run(['codesign', '--force', '--sign', '-', str(destination)])
                command = [*args, 'spawn', sim['device'], str(destination)]
                result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
            finally:
                destination.unlink(missing_ok=True)
                if device['state'] != 'Booted':
                    run([*args, 'shutdown', sim['device']])
        else:
            command = [str(exe)]
            if row.key == 'darwin-x86_64':
                command = ['arch', '-x86_64', str(exe)]
            elif row.key == 'linux-musl-x86_64':
                command = [str(Path(row.sysroot) / 'lib/ld-musl-x86_64.so.1'), str(exe)]
            elif row.key.startswith('linux-') and row.key != 'linux-x86_64':
                command = [options['qemu'], '-L', row.sysroot, str(exe)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=True)
        (out / 'run.log').write_text(result.stdout + result.stderr)
        if 'target-runtime-ok' not in result.stdout:
            raise ValueError('consumer success marker missing: ' + row.key)
    return dict(status='passed', acceptance_level=level, consumer_executable_linked=True, executable_sha256=sha(exe))
