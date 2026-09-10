"""Unit tests for the pure-logic parts of auto_checkin.py.

Run:  python -m unittest -v   (no extra dependencies)

Git-touching functions (stage/commit/push) are exercised manually via
`python auto_checkin.py --once`; these cover config handling and the helpers
that decide *what* to do.
"""

import json
import tempfile
import unittest
from pathlib import Path

import auto_checkin as ac


def write_config(tmp: Path, data: dict) -> Path:
    p = tmp / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


BASE_SCHEDULE = {"type": "interval", "hours": 0, "minutes": 30}


class ScheduleValidation(unittest.TestCase):
    def test_rejects_missing_type(self):
        with self.assertRaises(ac.ConfigError):
            ac.validate_schedule({}, "x")

    def test_rejects_zero_interval(self):
        with self.assertRaises(ac.ConfigError):
            ac.validate_schedule({"type": "interval", "hours": 0, "minutes": 0}, "x")

    def test_accepts_cron_and_interval(self):
        ac.validate_schedule({"type": "cron", "hour": 9, "minute": 0}, "x")
        ac.validate_schedule(BASE_SCHEDULE, "x")


class PushValidation(unittest.TestCase):
    def test_disabled_needs_nothing(self):
        ac.validate_push({"enabled": False}, "x")
        ac.validate_push({}, "x")

    def test_enabled_requires_branch_prefix(self):
        with self.assertRaises(ac.ConfigError):
            ac.validate_push({"enabled": True}, "x")
        with self.assertRaises(ac.ConfigError):
            ac.validate_push({"enabled": True, "branch_prefix": "  "}, "x")

    def test_enabled_ok_with_prefix(self):
        ac.validate_push({"enabled": True, "branch_prefix": "wip/me/"}, "x")


class WipBranchName(unittest.TestCase):
    def test_joins_and_collapses_slashes(self):
        self.assertEqual(
            ac.wip_branch_name("wip/me/", "feature/x"), "wip/me/feature/x"
        )
        self.assertEqual(
            ac.wip_branch_name("wip/me", "feature/x"), "wip/me/feature/x"
        )


class ExcludePatternFilter(unittest.TestCase):
    def test_keeps_globs_and_missing_paths(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            kept = ac.filter_exclude_patterns(repo, ["*.log", "does-not-exist/"])
            self.assertEqual(kept, ["*.log", "does-not-exist/"])


class DiscoverRepositories(unittest.TestCase):
    def _make_repo(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        (path / ".git").mkdir()

    def test_finds_nested_and_self(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self._make_repo(base / "alpha")
            self._make_repo(base / "beta")
            (base / "not-a-repo").mkdir()
            found = ac.discover_repositories([str(base)], set())
            names = sorted(r["name"] for r in found)
            self.assertEqual(names, ["alpha", "beta"])

    def test_skips_already_listed(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            self._make_repo(base / "alpha")
            already = {str((base / "alpha").resolve())}
            self.assertEqual(ac.discover_repositories([str(base)], already), [])

    def test_ignores_missing_directory(self):
        self.assertEqual(ac.discover_repositories(["/no/such/dir"], set()), [])


class LoadConfig(unittest.TestCase):
    def test_backward_compatible_repositories_only(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cfg = write_config(tmp, {
                "repositories": [{"name": "r", "path": str(tmp)}],
                "schedule": BASE_SCHEDULE,
            })
            loaded = ac.load_config(cfg)
            self.assertEqual(len(loaded["repositories"]), 1)
            self.assertEqual(loaded["push"], {"enabled": False})
            self.assertEqual(loaded["protected_branches"], ["main", "master", "develop"])

    def test_scan_directories_only(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "svc" / ".git").mkdir(parents=True)
            cfg = write_config(tmp, {
                "scan_directories": [str(tmp)],
                "schedule": BASE_SCHEDULE,
            })
            loaded = ac.load_config(cfg)
            self.assertEqual([r["name"] for r in loaded["repositories"]], ["svc"])

    def test_errors_when_nothing_to_watch(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cfg = write_config(tmp, {
                "scan_directories": [str(tmp)],
                "repositories": [],
                "schedule": BASE_SCHEDULE,
            })
            with self.assertRaises(ac.ConfigError):
                ac.load_config(cfg)

    def test_rejects_bad_scan_directories_type(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cfg = write_config(tmp, {
                "scan_directories": "not-a-list",
                "schedule": BASE_SCHEDULE,
            })
            with self.assertRaises(ac.ConfigError):
                ac.load_config(cfg)

    def test_explicit_repo_wins_over_scan(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "svc" / ".git").mkdir(parents=True)
            cfg = write_config(tmp, {
                "scan_directories": [str(tmp)],
                "repositories": [
                    {"name": "custom", "path": str(tmp / "svc"),
                     "protected_branches": ["main", "release"]}
                ],
                "schedule": BASE_SCHEDULE,
            })
            loaded = ac.load_config(cfg)
            self.assertEqual(len(loaded["repositories"]), 1)
            self.assertEqual(loaded["repositories"][0]["name"], "custom")
            self.assertEqual(
                loaded["repositories"][0]["protected_branches"], ["main", "release"]
            )


if __name__ == "__main__":
    unittest.main()
