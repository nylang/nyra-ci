"""A reviewed input proof preserves build identity and binds all shipped tools."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_common import check_sdk, sha, write


class InputEquivalenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.patch = patch('release_common.ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.plan = dict(version='9.0.0', source_commit='b' * 40)
        self.host = 'darwin-arm64'
        self.data = dict(format='nyra-sdk-v2', version='9.0.0', host=self.host,
                         source_revision='a' * 40,
                         acceptance={name: True for name in ('clean_source', 'fixed_point', 'full_regression', 'full_unit')},
                         tools={name: dict(version='9.0.0', sha256=value * 64) for name, value in
                                (('nyrac', '1'), ('nyra', '2'), ('nyra-analyzer', '3'))})
        self.proof = dict(kind='approved-platform-effective-input-reuse-v1', status='complete', host=self.host,
                          actual_product_build_source_commit='a' * 40, release_source_commit='b' * 40,
                          tools={name: row['sha256'] for name, row in self.data['tools'].items()},
                          frontend_gate=dict(status='ok', passed=470, ran=470, failed=0, skipped=0, exit_code=0))
        self.file = self.root / 'qualification-equivalence/mac.json'
        self.config = dict(source_commit='b' * 40, native_source_equivalence={self.host: dict(path='qualification-equivalence/mac.json')})
        self.save()

    def save(self):
        write(self.file, self.proof)
        self.config['native_source_equivalence'][self.host]['sha256'] = sha(self.file)
        write(self.root / 'qualified-inputs/9.0.0.json', self.config)

    def test_reviewed_proof_retains_actual_build_revision(self):
        check_sdk(self.data, self.plan, self.host)
        self.assertEqual(self.data['source_revision'], 'a' * 40)

    def test_tool_substitution_and_unreviewed_host_fail(self):
        self.data['tools']['nyrac']['sha256'] = '4' * 64
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            check_sdk(self.data, self.plan, self.host)
        self.data['host'] = 'windows-x86_64'
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            check_sdk(self.data, self.plan, 'windows-x86_64')

    def test_failed_partial_empty_or_other_release_proof_fails(self):
        original = json.loads(json.dumps(self.proof))
        for key, value in (('status', 'partial'), ('release_source_commit', 'c' * 40),
                           ('actual_product_build_source_commit', 'd' * 40), ('host', 'linux-x86_64')):
            self.proof[key] = value
            self.save()
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'identity differs'):
                check_sdk(self.data, self.plan, self.host)
            self.proof = json.loads(json.dumps(original))
        for field, value in (('status', 'FAILED'), ('passed', 0), ('failed', 1), ('skipped', 1), ('ran', 469)):
            self.proof['frontend_gate'][field] = value
            self.save()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'identity differs'):
                check_sdk(self.data, self.plan, self.host)
            self.proof = json.loads(json.dumps(original))

    def test_changed_proof_or_config_is_not_a_general_source_override(self):
        self.file.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'proof changed'):
            check_sdk(self.data, self.plan, self.host)
        self.save()
        self.config['source_commit'] = 'c' * 40
        write(self.root / 'qualified-inputs/9.0.0.json', self.config)
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            check_sdk(self.data, self.plan, self.host)

    def test_unqualified_gate_cannot_be_rescued_by_equivalence(self):
        self.data['acceptance']['full_unit'] = False
        with self.assertRaisesRegex(ValueError, 'all clean-source'):
            check_sdk(self.data, self.plan, self.host)


if __name__ == '__main__':
    unittest.main()
