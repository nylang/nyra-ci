"""Guard immutable bundles, target coverage and publication retry behavior."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import publish_bundle
from release_common import check_sdk, sha, write
from release_manifest import seal

PLAN = dict(version='9.0.0', source_commit='a' * 40, notes='Fixture')
POLICY = dict(hosts=['linux-x86_64'], targets=['none-arm64'], packages=['kernel', 'ntest', 'stdlib'],
              runtime_groups={'kernel-ntest-arm64': ['none-arm64']}, execution_required={}, public_repository='nylang/sdk')
ENV = dict(GITHUB_SHA='b' * 40, GITHUB_RUN_ID='123')


class SealedBundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, ENV, clear=True).start()
        patch('release_manifest.policy', return_value=POLICY).start()
        patch('publish_bundle.policy', return_value=POLICY).start()
        patch('publish_bundle.validate_archive').start()
        patch('publish_bundle.validate_runtime').start()
        sdk = self.directory / 'nyra-sdk-9.0.0-linux-x86_64.tar.gz'
        sdk.write_bytes(b'qualified compiler archive')
        (self.directory / 'nyra-runtime-9.0.0-kernel-ntest-arm64.tar.gz').write_bytes(b'qualified runtime archive')
        write(self.directory / 'release-linux-x86_64.json', dict(host='linux-x86_64', version='9.0.0', source_commit='a' * 40,
              validation_policy='g1-g2-v1', success=True,
              archive=dict(path=sdk.name, sha256=sha(sdk), bytes=sdk.stat().st_size)))
        seal(self.directory, PLAN)

    def test_complete_sealed_bundle_passes_without_builder(self):
        files = publish_bundle.verify(self.directory, PLAN, '123')
        self.assertEqual(len(files), 5)

    def test_changed_archive_is_rejected(self):
        (self.directory / 'nyra-sdk-9.0.0-linux-x86_64.tar.gz').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'sealed publication asset changed'):
            publish_bundle.verify(self.directory, PLAN, '123')

    def test_private_extra_file_is_rejected(self):
        (self.directory / 'source.ny').write_text('private')
        with self.assertRaisesRegex(ValueError, 'unexpected files'):
            publish_bundle.verify(self.directory, PLAN, '123')

    def test_other_run_and_control_revision_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'different qualification run'):
            publish_bundle.verify(self.directory, PLAN, '124')
        with patch.dict(os.environ, {'NYRA_CI_QUALIFICATION_SHA': 'c' * 40}):
            with self.assertRaisesRegex(ValueError, 'control revision'):
                publish_bundle.verify(self.directory, PLAN, '123')

    def test_omitting_runtime_group_cannot_reseal(self):
        (self.directory / 'release.json').unlink()
        (self.directory / 'SHA256SUMS').unlink()
        (self.directory / 'nyra-runtime-9.0.0-kernel-ntest-arm64.tar.gz').unlink()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            seal(self.directory, PLAN)

    def test_checksum_file_tampering_is_rejected(self):
        (self.directory / 'SHA256SUMS').write_text('')
        with self.assertRaisesRegex(ValueError, 'checksums'):
            publish_bundle.verify(self.directory, PLAN, '123')


class NativePolicyTests(unittest.TestCase):
    def fixture(self):
        return dict(format='nyra-sdk-v2', version='9.0.0', host='linux-x86_64', source_revision='a' * 40,
                    acceptance={k: True for k in ('clean_source', 'fixed_point', 'full_regression', 'full_unit')},
                    tools={k: dict(version='9.0.0') for k in ('nyrac', 'nyra-analyzer', 'nyra')})

    def test_three_tool_package_first_sdk(self):
        check_sdk(self.fixture(), PLAN, 'linux-x86_64')

    def test_missing_manager_or_incomplete_gate_fails(self):
        data = self.fixture()
        del data['tools']['nyra']
        with self.assertRaises(ValueError):
            check_sdk(data, PLAN, 'linux-x86_64')
        data = self.fixture()
        data['acceptance']['full_unit'] = False
        with self.assertRaises(ValueError):
            check_sdk(data, PLAN, 'linux-x86_64')


class PublicationTests(unittest.TestCase):
    def test_publication_patch_preserves_the_reviewed_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'asset'
            file.write_bytes(b'accepted')
            body = 'Nyra SDK v9.0.0\nSource: ' + 'a' * 40 + '\n\nFixture\n\nQualified by CI using g1-g2-v1; exact accepted tool bytes. Runtime execution levels are recorded per target. Unsigned and not notarized.'
            release = dict(id=1, body=body, prerelease=False, draft=True, html_url='https://example.test/release')
            asset = dict(name='asset', state='uploaded', size=8, digest='sha256:' + sha(file))
            github = Mock()
            github.api.side_effect = [dict(private=False, full_name='nylang/sdk'), [asset], [asset],
                                      dict(release, draft=False)]
            with patch('publish_bundle.find_release', return_value=release), patch('publish_bundle.policy', return_value=POLICY):
                publish_bundle.publish(github, [file], PLAN, 'v9.0.0')
            github.upload.assert_not_called()
            self.assertEqual(github.api.call_args.args[1], {'tag_name': 'v9.0.0', 'draft': False})
            self.assertEqual(github.api.call_args.kwargs['method'], 'PATCH')

    def test_already_published_matching_bytes_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'asset'
            file.write_bytes(b'accepted')
            body = 'Nyra SDK v9.0.0\nSource: ' + 'a' * 40 + '\n\nFixture\n\nQualified by CI using g1-g2-v1; exact accepted tool bytes. Runtime execution levels are recorded per target. Unsigned and not notarized.'
            release = dict(id=1, body=body, prerelease=False, draft=False, html_url='https://example.test/release')
            asset = dict(name='asset', state='uploaded', size=8, digest='sha256:' + sha(file))
            github = Mock()
            github.api.side_effect = [dict(private=False, full_name='nylang/sdk'), [asset], [asset]]
            with patch('publish_bundle.find_release', return_value=release), patch('publish_bundle.policy', return_value=POLICY):
                publish_bundle.publish(github, [file], PLAN, 'v9.0.0')
            github.upload.assert_not_called()
            self.assertTrue(all(c.kwargs.get('method') is None for c in github.api.call_args_list))

    def test_conflicting_published_asset_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'asset'
            file.write_bytes(b'accepted')
            release = dict(id=1, body='wrong plan', prerelease=False, draft=False)
            github = Mock()
            github.api.return_value = dict(private=False, full_name='nylang/sdk')
            with patch('publish_bundle.find_release', return_value=release), patch('publish_bundle.policy', return_value=POLICY):
                with self.assertRaisesRegex(ValueError, 'different plan'):
                    publish_bundle.publish(github, [file], PLAN, 'v9.0.0')
            github.upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
