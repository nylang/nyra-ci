"""A partial or substituted original gate cannot become a resumed qualification."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from release_common import sha
from resume_native import corrected_test, validate_original


class NativeContinuationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.compiler = self.root / 'compiler'
        self.compiler.write_bytes(b'exact original native fixed point')
        self.platform = self.root / 'platform.a'
        self.platform.write_bytes(b'exact selected native provider')
        self.host = 'linux-x86_64'
        self.config = dict(actual_product_build_source_commit='a' * 40, hosts={self.host: sha(self.compiler)})
        self.report = dict(source_revision='a' * 40, source_worktree_clean=True,
                           validation_policy='g1-g2-v1', fixed_point=dict(identical=True,
                           G1_sha256=sha(self.compiler), G2_sha256=sha(self.compiler)),
                           native_inputs=dict(platform_sha256=sha(self.platform)))
        self.regression = dict(compiler_sha256=sha(self.compiler), total=536, passed=536,
                               failed=0, cases=[dict(ok=True, rel='case/' + str(index)) for index in range(536)])

    def validate(self):
        validate_original(self.report, self.regression, self.config, self.host, self.compiler, self.platform)

    def test_complete_actual_gates_can_continue_without_rebuilding(self):
        self.validate()

    def test_original_535_of_536_cannot_be_reused_as_complete(self):
        self.regression.update(passed=535, failed=1)
        self.regression['cases'][0]['ok'] = False
        with self.assertRaisesRegex(ValueError, 'complete regression gate'):
            self.validate()

    def test_short_or_false_inventory_is_rejected(self):
        saved = copy.deepcopy(self.regression)
        self.regression['cases'].pop()
        with self.assertRaisesRegex(ValueError, 'complete regression gate'):
            self.validate()
        self.regression = saved
        self.regression['cases'][0]['ok'] = False
        with self.assertRaisesRegex(ValueError, 'complete regression gate'):
            self.validate()

    def test_duplicate_cases_cannot_replace_the_complete_inventory(self):
        self.regression['cases'][0]['rel'] = self.regression['cases'][1]['rel']
        with self.assertRaisesRegex(ValueError, 'complete regression gate'):
            self.validate()

    def test_compiler_provider_source_or_fixed_point_substitution_fails(self):
        for target in (self.compiler, self.platform):
            original = target.read_bytes()
            target.write_bytes(b'substituted')
            with self.subTest(target=target.name), self.assertRaisesRegex(ValueError, 'input identity'):
                self.validate()
            target.write_bytes(original)
        self.report['fixed_point']['identical'] = False
        with self.assertRaisesRegex(ValueError, 'input identity'):
            self.validate()
        self.report['fixed_point']['identical'] = True
        self.report['source_revision'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'input identity'):
            self.validate()

    def test_only_reviewed_test_body_initialization_is_excluded(self):
        original = '''// Other source remains frozen.
@test(tag = "core-lowering") func test_production_heap_binding_excludes_unselected_test_provider() {
  var active: Program = Program.Empty();
  var test_only: Program = Program.Empty();
}
'''
        corrected = original.replace('  var active: Program = Program.Empty();',
            '  var active: Program = Program.Empty();\n  CheckedUnit.BeginAt(&mut active);').replace(
            '  var test_only: Program = Program.Empty();',
            '  var test_only: Program = Program.Empty();\n  CheckedUnit.BeginAt(&mut test_only);')
        self.assertEqual(len(corrected_test(original, corrected)), 64)
        for changed in (original, corrected.replace('Other source remains frozen', 'Changed other source'),
                        corrected.replace('Program.Empty()', 'Program.Other()', 1)):
            with self.assertRaisesRegex(ValueError, 'more than the reviewed'):
                corrected_test(original, changed)


if __name__ == '__main__':
    unittest.main()
