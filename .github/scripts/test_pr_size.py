"""Tests for the advisory PR-size labeller.

Run with: python3 -m unittest discover -s .github/scripts -p 'test_pr_size.py'
Also wired into the `pr-size selftest` job in .github/workflows/ci.yml.
"""

import contextlib
import http.client
import io
import os
import unittest
from datetime import UTC, datetime
from typing import ClassVar
from unittest import mock

import pr_size


class Exclusions(unittest.TestCase):
    def test_lockfiles_are_excluded_at_any_depth(self):
        for path in [
            "package-lock.json",
            "web/package-lock.json",
            "Cargo.lock",
            "crates/app/Cargo.lock",
            "pnpm-lock.yaml",
            "go.sum",
            "vendor.lock",
        ]:
            self.assertTrue(pr_size.is_excluded(path), path)

    def test_generated_and_fixtures_are_excluded(self):
        for path in [
            "assets/app.min.js",
            "styles.min.css",
            "bundle.js.map",
            "src/__snapshots__/page.snap",
            "test/page.test.ts.snap",
            "fixtures/users.json",
            "src/fixtures/users.json",
            "pkg/testdata/golden.txt",
            "dist/index.js",
            "web/dist/index.js",
            "vendor/lib.rs",
        ]:
            self.assertTrue(pr_size.is_excluded(path), path)

    def test_source_is_counted(self):
        for path in [
            "src/app.ts",
            "src/fixtures.ts",  # a file named fixtures, not a fixtures tree
            "test/app.test.ts",
            "db/migrations/0042_add_table.sql",
            "Cargo.toml",
            "package.json",
        ]:
            self.assertFalse(pr_size.is_excluded(path), path)

    def test_repo_extra_globs(self):
        extra = ["public/islands/**", "public/styles.css", "drizzle/meta/**"]
        self.assertTrue(pr_size.is_excluded("public/islands/going.js", extra))
        self.assertTrue(pr_size.is_excluded("public/styles.css", extra))
        self.assertFalse(pr_size.is_excluded("public/theme.css", extra))
        # Generated drizzle-kit snapshots (~2,000 lines each) must not inflate
        # a migration PR, but the hand-written SQL beside them still counts.
        self.assertTrue(
            pr_size.is_excluded("drizzle/meta/1025_snapshot.json", extra)
        )
        self.assertTrue(pr_size.is_excluded("drizzle/meta/_journal.json", extra))
        self.assertFalse(
            pr_size.is_excluded("drizzle/1026_add_table.sql", extra)
        )


class Sizing(unittest.TestCase):
    def test_label_boundaries(self):
        self.assertEqual(pr_size.size_label(0), "size/S")
        self.assertEqual(pr_size.size_label(100), "size/S")
        self.assertEqual(pr_size.size_label(101), "size/M")
        self.assertEqual(pr_size.size_label(400), "size/M")
        self.assertEqual(pr_size.size_label(401), "size/L")
        self.assertEqual(pr_size.size_label(1000), "size/L")
        self.assertEqual(pr_size.size_label(1001), "size/XL")

    def test_count_files_splits_counted_and_excluded(self):
        files = [
            {"filename": "src/app.ts", "additions": 30, "deletions": 10},
            {"filename": "package-lock.json", "additions": 900, "deletions": 0},
            {"filename": "img/logo.png", "additions": 0, "deletions": 0},
        ]
        counted, excluded, counted_files = pr_size.count_files(files)
        self.assertEqual((counted, excluded, counted_files), (40, 900, 2))


class FakeApi:
    """Records the calls label_pr makes; serves canned PR state."""

    def __init__(self, files, labels=(), marker_comment=None):
        self._files = files
        self.labels = list(labels)
        self.marker_comment = marker_comment
        self.calls = []

    def pr_files(self, number):
        return self._files

    def pr_labels(self, number):
        return list(self.labels)

    def ensure_label(self, name):
        self.calls.append(("ensure", name))

    def add_label(self, number, name):
        self.calls.append(("add", name))
        self.labels.append(name)

    def remove_label(self, number, name):
        self.calls.append(("remove", name))
        self.labels.remove(name)

    def find_marker_comment(self, number):
        return self.marker_comment

    def upsert_comment(self, number, body):
        self.calls.append(("comment", body))


def big_files():
    return [{"filename": "src/big.ts", "additions": 500, "deletions": 0}]


class LabelFlow(unittest.TestCase):
    def test_over_threshold_labels_and_comments_once(self):
        api = FakeApi(big_files(), labels=["size/S"])
        label, counted = pr_size.label_pr(api, 7, 400, (), log=lambda *_: None)
        self.assertEqual((label, counted), ("size/L", 500))
        self.assertEqual(api.labels, ["size/L"])
        comments = [body for kind, body in api.calls if kind == "comment"]
        self.assertEqual(len(comments), 1)
        self.assertIn(pr_size.MARKER, comments[0])
        self.assertIn("500 counted lines", comments[0])

    def test_under_threshold_no_comment_without_marker(self):
        api = FakeApi([{"filename": "a.ts", "additions": 5, "deletions": 0}])
        pr_size.label_pr(api, 7, 400, (), log=lambda *_: None)
        self.assertEqual(api.labels, ["size/S"])
        self.assertEqual([c for c in api.calls if c[0] == "comment"], [])

    def test_shrinking_back_updates_existing_comment(self):
        api = FakeApi(
            [{"filename": "a.ts", "additions": 5, "deletions": 0}],
            labels=["size/L"],
            marker_comment={"id": 1, "body": pr_size.MARKER},
        )
        pr_size.label_pr(api, 7, 400, (), log=lambda *_: None)
        comments = [body for kind, body in api.calls if kind == "comment"]
        self.assertEqual(len(comments), 1)
        self.assertIn("back under", comments[0])

    def test_same_label_is_not_readded(self):
        api = FakeApi(big_files(), labels=["size/L"])
        pr_size.label_pr(api, 7, 400, (), log=lambda *_: None)
        self.assertNotIn(("add", "size/L"), api.calls)
        self.assertEqual([c for c in api.calls if c[0] == "remove"], [])


class Weekly(unittest.TestCase):
    def test_tally_counts_each_label_once(self):
        items = [
            {"labels": [{"name": "size/S"}]},
            {"labels": [{"name": "size/L"}, {"name": "bug"}]},
            {"labels": [{"name": "size/XL"}]},
            {"labels": []},
        ]
        counts = pr_size.tally(items)
        self.assertEqual(counts["size/S"], 1)
        self.assertEqual(counts["size/L"], 1)
        self.assertEqual(counts["size/XL"], 1)
        self.assertEqual(counts["size/M"], 0)
        self.assertEqual(counts["unlabelled"], 1)

    def test_weekly_window_is_a_true_seven_days(self):
        # A date-only `merged:>=YYYY-MM-DD` from a Monday 07:17 run covers
        # 7 days + 7 hours, double-counting early-Monday merges in the next
        # tally. The window must carry the full UTC timestamp.
        now = datetime(2026, 10, 14, 7, 17, 30, tzinfo=UTC)
        self.assertEqual(pr_size.weekly_since(now), "2026-10-07T07:17:30Z")

    def test_report_names_the_total_and_over_threshold(self):
        report = pr_size.weekly_report(
            {"size/S": 2, "size/M": 1, "size/L": 1, "size/XL": 1, "unlabelled": 0},
            "2026-10-07",
        )
        self.assertIn("**5**", report)
        self.assertIn("(2 over the advisory threshold)", report)


class TruncatedResponse:
    """A response whose body is shorter than its Content-Length."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        raise http.client.IncompleteRead(b"partial", 100)


class TransportErrors(unittest.TestCase):
    """The exit-0 advisory contract covers raw transport failures too.

    urllib does not wrap a dropped connection (RemoteDisconnected, raised
    out of getresponse()) or a truncated body (IncompleteRead, raised out
    of resp.read()) in URLError, so request() must catch them itself -
    otherwise one dropped connection turns an advisory run red.
    """

    ENV: ClassVar[dict[str, str]] = {"REPO": "owner/name", "GH_TOKEN": "test-token", "PR_NUMBER": "7"}

    def _run_main(self, mode, urlopen):
        out = io.StringIO()
        with mock.patch.dict(os.environ, self.ENV), \
             mock.patch("urllib.request.urlopen", urlopen), \
             contextlib.redirect_stdout(out):
            code = pr_size.main(["pr_size.py", mode])
        return code, out.getvalue()

    def test_request_wraps_remote_disconnected_as_api_error(self):
        api = pr_size.GitHubApi("test-token", "owner/name")
        dropped = mock.Mock(
            side_effect=http.client.RemoteDisconnected("closed without response")
        )
        with mock.patch("urllib.request.urlopen", dropped), self.assertRaises(pr_size.ApiError):
            api.request("GET", "/repos/owner/name/pulls/7")

    def test_label_mode_exits_0_on_remote_disconnected(self):
        dropped = mock.Mock(
            side_effect=http.client.RemoteDisconnected("closed without response")
        )
        code, out = self._run_main("label", dropped)
        self.assertEqual(code, 0)
        self.assertIn("::warning::pr_size advisory skipped:", out)

    def test_label_mode_exits_0_on_incomplete_read(self):
        code, out = self._run_main("label", mock.Mock(return_value=TruncatedResponse()))
        self.assertEqual(code, 0)
        self.assertIn("::warning::pr_size advisory skipped:", out)

    def test_weekly_mode_exits_0_on_remote_disconnected(self):
        dropped = mock.Mock(
            side_effect=http.client.RemoteDisconnected("closed without response")
        )
        code, out = self._run_main("weekly", dropped)
        self.assertEqual(code, 0)
        self.assertIn("::warning::pr_size advisory skipped:", out)


if __name__ == "__main__":
    unittest.main()
