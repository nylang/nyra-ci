"""G0 selection cannot silently fall back across source/API generations."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import transition_seed
from prepare_source import matrix


class NativeMatrixTests(unittest.TestCase):
    def test_default_retains_four_hosts(self):
        self.assertEqual(len(matrix('')['include']), 4)

    def test_single_host_does_not_replay_other_accepted_hosts(self):
        self.assertEqual([r['host'] for r in matrix('linux-x86_64')['include']], ['linux-x86_64'])

    def test_unknown_duplicate_or_empty_component_rejected(self):
        for text in ('linux-x86_64,', 'linux-x86_64,linux-x86_64', 'untrusted-runner'):
            with self.assertRaises(ValueError):
                matrix(text)


class SeedAdmissionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.sdk = self.root / 'sdk'
        self.cache = self.root / 'cache'
        self.host = 'linux-x86_64'
        (self.source / 'VERSION').write_text('1.0.5\n')
        (self.root / 'sdk-seeds.json').write_text(json.dumps({'version': '1.0.4'}))
        (self.root / 'transitional-seeds.json').write_text(json.dumps({
            'schema_version': 1, 'required_versions': ['1.0.5'], 'compilers': {}}))
        self.addCleanup(patch.stopall)
        patch('transition_seed.ROOT', self.root).start()
        patch('transition_seed.subprocess.check_output', return_value='a' * 40).start()

    def admit(self):
        return transition_seed.selected_compiler(self.host, self.source, self.sdk, self.cache)

    def transition(self):
        tool = self.cache / 'nyra-bootstrap-transition/1.0.5/linux-x86_64/nyrac'
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b'explicit native G0')
        data = {'schema_version': 1, 'required_versions': ['1.0.5'], 'compilers': {
            '1.0.5': {'source_commit': 'a' * 40, 'hosts': {self.host: {
            'sha256': transition_seed.sha(tool), 'provenance': 'controlled cross-object/native-link receipt'}}}}}
        (self.root / 'transitional-seeds.json').write_text(json.dumps(data))
        return tool, data

    def test_api_generation_change_cannot_use_old_default(self):
        with self.assertRaisesRegex(ValueError, 'explicit native transition seed'):
            self.admit()

    def test_matching_released_sdk_remains_default(self):
        (self.source / 'VERSION').write_text('1.0.4')
        tool = self.sdk / 'hosts/linux-x86_64/bin/nyrac'
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b'released native compiler')
        actual, receipt = self.admit()
        self.assertEqual(actual, tool)
        self.assertEqual(receipt['mode'], 'released-sdk')

    def test_compatible_future_source_can_use_preceding_release(self):
        (self.source / 'VERSION').write_text('1.0.6')
        tool = self.sdk / 'hosts/linux-x86_64/bin/nyrac'
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b'released native compiler')
        actual, receipt = self.admit()
        self.assertEqual(actual, tool)
        self.assertEqual(receipt['version'], '1.0.4')

    def test_exact_transition_records_compiler_identity(self):
        tool, _ = self.transition()
        actual, receipt = self.admit()
        self.assertEqual(actual, tool)
        self.assertEqual(receipt['sha256'], transition_seed.sha(tool))
        self.assertEqual(receipt['source_commit'], 'a' * 40)

    def test_changed_compiler_is_rejected(self):
        tool, _ = self.transition()
        tool.write_bytes(b'changed compiler')
        with self.assertRaisesRegex(ValueError, 'changed explicit transition'):
            self.admit()

    def test_other_source_commit_is_rejected(self):
        self.transition()
        with patch('transition_seed.subprocess.check_output', return_value='b' * 40):
            with self.assertRaisesRegex(ValueError, 'different frozen source'):
                self.admit()

    def test_absent_native_seed_cannot_fall_back(self):
        tool, _ = self.transition()
        tool.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or changed'):
            self.admit()


if __name__ == '__main__':
    unittest.main()
