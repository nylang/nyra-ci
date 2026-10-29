"""Retain compile-only tool worksets without claiming linked host SDKs."""
import json
import os
from pathlib import Path
from build_sdk import run, cache_root
from release_common import sha, write


def compile_host(row, compiler, llvm, snapshot, bootstrap, out):
    out.mkdir(parents=True, exist_ok=True)
    records = []
    for name, package in (('nyrac', 'nyra'), ('nyra-analyzer', 'analyzer'), ('nyra', 'package_manager')):
        dest = out / name
        manifest = dest / 'objects.list'
        run([compiler, *row.nyra_flags(), 'build', snapshot / 'source' / package / 'main.ny',
             '-O2', '--jobs', '2', '--out-dir', dest, '--object-manifest', manifest], snapshot,
            os.environ | {'AR': row.ar})
        paths = [Path(p) for p in manifest.read_text().splitlines()]
        if not paths or not all(p.is_file() and p.stat().st_size > 0 for p in paths):
            raise ValueError('empty/incomplete host tool object workset')
        records.append(dict(name=name, object_units=len(paths), objects={p.relative_to(dest).as_posix(): sha(p) for p in paths}))
    from tools.dev.paths import source_package_path
    bridges = [('nllvm', source_package_path(snapshot, 'nllvm') / 'private/nyra_llvm_initialize_all.c'),
               ('ngit', source_package_path(snapshot, 'ngit') / 'private/native/git_bridge.c')]
    include = [llvm / 'include', bootstrap / 'package-manager/libgit2-1.9.7/include',
               cache_root() / 'nyra-providers/openssl-3.5.9/include']
    native = []
    for name, source in bridges:
        obj = out / (name + '.o')
        run([row.cc, *row.c_flags(), '-std=c11', '-O2', *[x for path in include for x in ('-I', str(path))],
             '-c', source, '-o', obj])
        native.append(dict(name=name, source_sha256=sha(source), object_sha256=sha(obj)))
    record = dict(key=row.key, acceptance_level='compile-only', executable_package_ready=False,
                  compiler_sha256=sha(compiler), tools=records, bridges=native,
                  limitation='Matching target LLVM/libgit2/OpenSSL native libraries and CRT are required to link host products.')
    write(out / 'acceptance.json', record)
    return record
