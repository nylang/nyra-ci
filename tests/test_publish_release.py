"""Regression checks for draft recovery and actionable SDK API failures."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
import urllib.error
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publish_release import Github, find_release


def missing(code=404):
    return urllib.error.HTTPError("https://api.github.com/repos/nylang/sdk/releases/tags/v1.0.2",
                                  code, "Not Found", {}, None)


class ReleaseRecoveryTests(unittest.TestCase):
    def test_read_inventory_bypasses_stale_intermediary_cache(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'[]'
        with patch('publish_release.urllib.request.urlopen', return_value=response) as opened:
            github = Github('nylang/sdk', 'never-log-this-token')
            github.api('/releases/12/assets?per_page=100')
            github.api('/releases/12/assets?per_page=100')
        first, second = [call.args[0] for call in opened.call_args_list]
        self.assertIn('/assets?per_page=100&_nyra_read=', first.full_url)
        self.assertNotEqual(first.full_url, second.full_url)
        self.assertEqual(first.get_header('Cache-control'), 'no-cache')

    def test_write_endpoint_and_payload_are_preserved(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"draft":false}'
        with patch('publish_release.urllib.request.urlopen', return_value=response) as opened:
            Github('nylang/sdk', 'never-log-this-token').api('/releases/12', {'draft': False}, method='PATCH')
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/repos/nylang/sdk/releases/12')
        self.assertEqual(request.get_method(), 'PATCH')
        self.assertEqual(json.loads(request.data), {'draft': False})

    def test_existing_draft_is_found_after_by_tag_returns_404(self):
        draft = {"id": 12, "tag_name": "v1.0.2", "draft": True}
        github = Mock()
        github.api.side_effect = [missing(), [draft]]
        self.assertEqual(find_release(github, "v1.0.2"), draft)
        self.assertEqual(github.api.call_count, 2)

    def test_draft_lookup_checks_later_pages(self):
        draft = {"id": 12, "tag_name": "v1.0.2", "draft": True}
        github = Mock()
        github.api.side_effect = [missing(), [{"tag_name": "v0.9.0"}] * 100, [draft]]
        self.assertEqual(find_release(github, "v1.0.2"), draft)
        self.assertEqual(github.api.call_args.args[0], "/releases?per_page=100&page=2")

    def test_conflicting_drafts_require_inspection(self):
        github = Mock()
        github.api.side_effect = [missing(), [{"tag_name": "v1.0.2", "draft": True}] * 2]
        with self.assertRaisesRegex(ValueError, "multiple releases"):
            find_release(github, "v1.0.2")

    def test_authorization_and_server_errors_are_not_treated_as_missing(self):
        for code in (403, 429, 500):
            with self.subTest(code=code):
                github = Mock()
                github.api.side_effect = missing(code)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    find_release(github, "v1.0.2")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(github.api.call_count, 1)

    def test_api_error_preserves_status_and_explains_required_permissions(self):
        error = urllib.error.HTTPError("https://api.github.com/repos/nylang/sdk/releases", 403,
            "Forbidden", {"X-Accepted-GitHub-Permissions": "contents=write"},
            io.BytesIO(json.dumps({"message": "Resource not accessible by personal access token"}).encode()))
        with patch("publish_release.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                Github("nylang/sdk", "never-log-this-token").api("/releases", {})
        self.assertEqual(caught.exception.code, 403)
        self.assertIn("Resource not accessible", str(caught.exception))
        self.assertIn("contents=write", str(caught.exception))
        self.assertNotIn("never-log-this-token", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
