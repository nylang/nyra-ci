"""Retained native products cannot be substituted, relabelled or rebuilt."""
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from retained_targets import retained_matrix, stage_git_headers, verify_native
from release_common import sha, write


class RetainedInputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.host = 'linux-x86_64'
        self.plan = dict(version='9.0.0', source_commit='a' * 40)
        self.archive = self.root / 'nyra-sdk-9.0.0-linux-x86_64.tar.gz'
        self.archive.write_bytes(b'exact accepted native SDK')
        self.receipt = dict(self.plan, host=self.host, success=True, validation_policy='g1-g2-v1',
                            sdk_manifest_sha256='b' * 64,
                            archive=dict(path=self.archive.name, sha256=sha(self.archive), bytes=self.archive.stat().st_size))
        self.file = self.root / 'release-linux-x86_64.json'
        self.save()

    def save(self):
        write(self.file, self.receipt)

    def test_managed_macos_imports_do_not_download_nonexistent_ci_artifacts(self):
        config = dict(managed_imports={'darwin-arm64': {}, 'darwin-x86_64': {}})
        rows = {row['host']: row for row in retained_matrix(config, '123')['include']}
        self.assertEqual(set(rows), {'darwin-arm64', 'darwin-x86_64', 'linux-x86_64', 'windows-x86_64'})
        for host in ('darwin-arm64', 'darwin-x86_64'):
            self.assertEqual(rows[host]['native_run'], '')
        for host in ('linux-x86_64', 'windows-x86_64'):
            self.assertEqual(rows[host]['native_run'], '123')
        with self.assertRaisesRegex(ValueError, 'unknown managed'):
            retained_matrix(dict(managed_imports={'linux-riscv64': {}}), '123')

    def test_accepted_native_archive_is_reused_as_is(self):
        path, receipt = verify_native(self.root, self.host, self.plan)
        self.assertEqual(path, self.archive)
        self.assertEqual(receipt, self.receipt)

    def test_changed_archive_is_rejected(self):
        self.archive.write_bytes(b'different bytes')
        with self.assertRaisesRegex(ValueError, 'archive changed'):
            verify_native(self.root, self.host, self.plan)

    def test_partial_or_other_source_is_rejected(self):
        for key, value in (('success', False), ('success', 1), ('source_commit', 'c' * 40),
                           ('validation_policy', 'legacy')):
            with self.subTest(key=key):
                prior = self.receipt[key]
                self.receipt[key] = value
                self.save()
                with self.assertRaisesRegex(ValueError, 'identity mismatch'):
                    verify_native(self.root, self.host, self.plan)
                self.receipt[key] = prior

    def test_managed_import_requires_all_reviewed_digests(self):
        pin = dict(archive_sha256=sha(self.archive), receipt_sha256=sha(self.file),
                   sdk_manifest_sha256=self.receipt['sdk_manifest_sha256'])
        verify_native(self.root, self.host, self.plan, pin)
        pin['receipt_sha256'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'reviewed pin'):
            verify_native(self.root, self.host, self.plan, pin)

    def test_native_scope_cannot_admit_cross_object_only_host(self):
        with self.assertRaisesRegex(ValueError, 'unsupported retained native host'):
            verify_native(self.root, 'linux-riscv64', self.plan)

    def test_git_bridge_stages_only_authenticated_public_headers(self):
        sdk = self.root / 'sdk'
        sources = sdk / 'share/nyra/sources'
        sources.mkdir(parents=True)
        archive = sources / 'libgit2-1.9.7.tar.gz'
        with tarfile.open(archive, 'w:gz') as output:
            for name in ('upstream/include/git2.h', 'upstream/include/git2/types.h',
                         'upstream/src/implementation.c', 'upstream/tests/include/private.h'):
                item = tarfile.TarInfo(name)
                item.size = 1
                output.addfile(item, io.BytesIO(b'x'))
        write(sdk / 'share/nyra/package-manager.json', dict(libgit2=dict(version='1.9.7'),
              corresponding_source=dict(path='share/nyra/sources/' + archive.name, sha256=sha(archive))))
        bootstrap = self.root / 'bootstrap'
        stage_git_headers(sdk, bootstrap)
        files = [p.relative_to(bootstrap).as_posix() for p in sorted(bootstrap.rglob('*')) if p.is_file()]
        self.assertEqual(files, ['package-manager/libgit2-1.9.7/include/git2/types.h',
                                 'package-manager/libgit2-1.9.7/include/git2.h'])


if __name__ == '__main__':
    unittest.main()
