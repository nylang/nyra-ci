"""Keep analyzer configuration admission narrow and aligned with the exporter."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from public_policy import _payload_allowed


class AnalyzerConfigurationTests(unittest.TestCase):
    def test_public_defaults_are_admitted_for_the_selected_host(self):
        for host in ('darwin-arm64', 'darwin-x86_64', 'linux-x86_64', 'windows-x86_64'):
            for name in ('analysis_options.yaml', 'coding_analysis_options.yaml'):
                with self.subTest(host=host, name=name):
                    self.assertTrue(_payload_allowed(f'hosts/{host}/share/nyra/analyzer/{name}', host, {host}))

    def test_extra_configuration_private_sources_and_other_hosts_are_rejected(self):
        for name in ('private.yaml', 'coding_analysis_options.yaml.bak', 'private/source.ny',
                     '../coding_analysis_options.yaml'):
            with self.subTest(name=name):
                try:
                    allowed = _payload_allowed('hosts/darwin-arm64/share/nyra/analyzer/' + name,
                                               'darwin-arm64', {'darwin-arm64'})
                except ValueError:
                    allowed = False
                self.assertFalse(allowed)
        self.assertFalse(_payload_allowed('hosts/linux-x86_64/share/nyra/analyzer/coding_analysis_options.yaml',
                                          'darwin-arm64', {'darwin-arm64'}))


if __name__ == '__main__':
    unittest.main()
