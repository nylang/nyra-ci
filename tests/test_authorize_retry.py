"""Native-only runs cannot authorize publication; exact assembly must pass."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from authorize_retry import authorize

class RetryAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.repository = 'nylang/nyra-ci'
        self.identity = dict(path='.github/workflows/sdk-build.yml', event='workflow_dispatch',
                             head_sha='a' * 40, head_repository=dict(full_name=self.repository))
        self.jobs = [dict(name='targets / Assemble retained publication bundle', conclusion='success')]
        self.artifacts = [dict(name='qualified-release', expired=False)]

    def test_retained_target_assembly_is_authorized_by_exact_control(self):
        self.assertEqual(authorize(self.identity, self.jobs, self.artifacts, self.repository), 'a' * 40)

    def test_native_only_and_failed_assemblies_reject(self):
        for jobs in ([dict(name='build (linux-x86_64)', conclusion='success')],
                     [dict(self.jobs[0], conclusion='failure')],
                     [dict(name='untrusted / Assemble retained publication bundle', conclusion='success')]):
            with self.subTest(jobs=jobs), self.assertRaisesRegex(ValueError, 'no successful'):
                authorize(self.identity, jobs, self.artifacts, self.repository)

    def test_other_workflow_repository_or_event_rejects(self):
        for change in (dict(path='.github/workflows/arbitrary.yml'), dict(event='pull_request'),
                       dict(head_repository=dict(full_name='other/repository')), dict(head_sha='main')):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, 'not a trusted'):
                authorize(dict(self.identity, **change), self.jobs, self.artifacts, self.repository)

    def test_expired_missing_or_duplicate_artifacts_reject(self):
        for artifacts in ([], [dict(self.artifacts[0], expired=True)], self.artifacts * 2):
            with self.subTest(artifacts=artifacts), self.assertRaisesRegex(ValueError, 'missing/expired'):
                authorize(self.identity, self.jobs, artifacts, self.repository)

    def test_original_release_assembly_remains_compatible(self):
        self.identity['path'] = '.github/workflows/release-sdk.yml'
        self.identity['event'] = 'push'
        self.jobs[0]['name'] = 'Assemble sealed publication bundle'
        self.assertEqual(authorize(self.identity, self.jobs, self.artifacts, self.repository), 'a' * 40)

if __name__ == '__main__':
    unittest.main()
