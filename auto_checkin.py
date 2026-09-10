#!/usr/bin/env python3
"""Automated GitHub Check-in Tool.

Watches a list of local git repositories and, on a schedule, commits any
pending local changes (staged, unstaged, or untracked) in each of them.
This tool NEVER pushes -- it only stages and commits locally.

By default, auto check-in is restricted to feature/developer branches: it
refuses to commit while a repo has "main", "master", or "develop" checked
out (or is in a detached HEAD state). See the 'protected_branches' config
option to customize this per repo.

Usage:
    python auto_checkin.py --once             # run a single pass and exit
    python auto_checkin.py                    # start the in-process scheduler
    python auto_checkin.py --config path.json # use a non-default config file
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_CONFIG_FILENAME = "config.json"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when the config file is missing, malformed, or invalid."""


def validate_schedule(schedule, context: str) -> None:
    """Validate a schedule object (used for both the global and per-repo schedule)."""
    if not isinstance(schedule, dict) or schedule.get("type") not in (
        "cron",
        "interval",
    ):
        raise ConfigError(
            f"{context} must be an object with type 'cron' or 'interval'."
        )
    if schedule["type"] == "interval":
        hours = schedule.get("hours", 0)
        minutes = schedule.get("minutes", 0)
        if hours == 0 and minutes == 0:
            raise ConfigError(
                f"{context}: interval schedule must specify a non-zero "
                "'hours' and/or 'minutes'."
            )


def validate_push(push, context: str) -> None:
    """Validate a 'push' object (used for both the global and per-repo push config).

    When 'enabled' is true the tool force-pushes each auto-commit to a
    per-developer work-in-progress branch so the work has an off-machine copy
    on the remote. 'branch_prefix' namespaces that branch (e.g. "wip/fidha/")
    so shared/feature branches are never touched.
    """
    if not isinstance(push, dict):
        raise ConfigError(f"{context} must be an object.")
    if not push.get("enabled"):
        return
    prefix = push.get("branch_prefix")
    if not isinstance(prefix, str) or not prefix.strip():
        raise ConfigError(
            f"{context}: 'branch_prefix' is required (e.g. \"wip/yourname/\") "
            "when push is enabled."
        )
    remote = push.get("remote", "origin")
    if not isinstance(remote, str) or not remote.strip():
        raise ConfigError(f"{context}: 'remote' must be a non-empty string.")


def discover_repositories(scan_directories: list, already_listed: set) -> list:
    """Find git repositories under each path in 'scan_directories'.

    A path counts if it is itself a git repo, or if any of its immediate
    subdirectories is. This lets a developer point the tool at the folder
    that holds all their projects (e.g. "D:/work") and never touch the
    config again when they clone another repo into it. Repos whose path is
    already in the explicit 'repositories' list are skipped so per-repo
    overrides there still win.
    """
    discovered = []
    for raw in scan_directories:
        base = Path(raw).expanduser()
        if not base.is_dir():
            continue
        candidates = [base] + sorted(p for p in base.iterdir() if p.is_dir())
        for path in candidates:
            resolved = str(path.resolve())
            if resolved in already_listed or not is_git_repo(path):
                continue
            already_listed.add(resolved)
            discovered.append({"name": path.name, "path": resolved})
    return discovered


def load_config(config_path: Path) -> dict:
    """Load and minimally validate the JSON config file."""
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {exc}") from exc

    repositories = config.get("repositories")
    if repositories is None:
        repositories = config["repositories"] = []
    if not isinstance(repositories, list):
        raise ConfigError("Config 'repositories' must be a list.")

    scan_directories = config.get("scan_directories", [])
    if not isinstance(scan_directories, list) or not all(
        isinstance(d, str) for d in scan_directories
    ):
        raise ConfigError("Config 'scan_directories' must be a list of paths.")

    for i, repo in enumerate(repositories):
        if "name" not in repo or "path" not in repo:
            raise ConfigError(
                f"repositories[{i}] must have 'name' and 'path' fields."
            )
        # Per-repo schedule override is optional; validate it if present so
        # a typo surfaces at startup instead of silently falling back.
        if "schedule" in repo:
            validate_schedule(
                repo["schedule"], f"repositories[{i}] ('{repo['name']}') schedule"
            )
        if "push" in repo:
            validate_push(
                repo["push"], f"repositories[{i}] ('{repo['name']}') push"
            )

    validate_schedule(config.get("schedule"), "Top-level 'schedule'")
    if "push" in config:
        validate_push(config["push"], "Top-level 'push'")

    if scan_directories:
        listed_paths = {str(Path(r["path"]).expanduser().resolve()) for r in repositories}
        repositories.extend(discover_repositories(scan_directories, listed_paths))

    if not repositories:
        raise ConfigError(
            "No repositories to watch: 'repositories' is empty and "
            "'scan_directories' found no git repos."
        )

    config.setdefault(
        "commit_message_template", "Automated check-in: {name} at {timestamp}"
    )
    config.setdefault("log_file", "auto_checkin.log")
    # Auto check-in is restricted to feature/developer branches by default;
    # commits are skipped on these shared branches unless a repo overrides
    # the list explicitly.
    config.setdefault("protected_branches", ["main", "master", "develop"])
    # Off by default: the tool commits locally only unless push is enabled.
    config.setdefault("push", {"enabled": False})

    return config


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging(log_file: str) -> logging.Logger:
    """Configure a logger that writes to both a log file and stdout."""
    logger = logging.getLogger("auto_checkin")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()  # avoid duplicate handlers if called more than once

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    log_path = Path(log_file)
    if log_path.parent and not log_path.parent.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def run_git(args: list, cwd: Path) -> subprocess.CompletedProcess:
    """Run a git command in the given working directory and return the result.

    GIT_TERMINAL_PROMPT=0 ensures a git operation that would otherwise block on
    an interactive credential/passphrase prompt (e.g. `git push` with no cached
    auth) fails fast instead of hanging the unattended scheduler.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )


def is_git_repo(repo_path: Path) -> bool:
    """A directory counts as a repo if it (or an ancestor, for worktrees) has .git."""
    return (repo_path / ".git").exists()


def get_current_branch(repo_path: Path) -> str:
    """Return the current branch name, or "HEAD" if in a detached HEAD state."""
    result = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed: {result.stderr.strip()}")
    return result.stdout.strip()


def get_status_porcelain(repo_path: Path) -> str:
    """Return `git status --porcelain` output (staged/unstaged/untracked changes)."""
    result = run_git(["status", "--porcelain"], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"git status failed: {result.stderr.strip()}")
    return result.stdout


def filter_exclude_patterns(repo_path: Path, exclude_patterns: list) -> list:
    """Drop exclude patterns that name a path git already ignores.

    A `:(exclude)<path>` pathspec that points at an existing, gitignored path
    makes `git add` abort with "paths are ignored ... use -f" and stage
    nothing. Such an exclude is redundant anyway -- `git add -A` never stages
    ignored files -- so we simply leave those patterns out of the pathspec.
    Glob patterns (e.g. `*.log`) and paths that don't exist are kept as-is.
    """
    kept = []
    for pattern in exclude_patterns:
        candidate = pattern.rstrip("/")
        if (repo_path / candidate).exists():
            check = run_git(["check-ignore", "-q", candidate], cwd=repo_path)
            if check.returncode == 0:
                continue  # already ignored by git; excluding it breaks `git add`
        kept.append(pattern)
    return kept


def stage_changes(repo_path: Path, exclude_patterns: list) -> None:
    """Stage all changes with `git add -A`, honoring exclude_patterns via pathspecs."""
    args = ["add", "-A"]
    effective_excludes = filter_exclude_patterns(repo_path, exclude_patterns)
    if effective_excludes:
        args.append("--")
        args.append(".")
        for pattern in effective_excludes:
            args.append(f":(exclude){pattern}")
    result = run_git(args, cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"git add failed: {result.stderr.strip()}")


def has_staged_changes(repo_path: Path) -> bool:
    """Return True if there is anything staged for commit."""
    result = run_git(["diff", "--cached", "--quiet"], cwd=repo_path)
    # `git diff --cached --quiet` exits 1 if there are staged differences.
    return result.returncode == 1


def commit_changes(repo_path: Path, message: str) -> None:
    """Commit currently staged changes with the given message."""
    result = run_git(["commit", "-m", message], cwd=repo_path)
    if result.returncode != 0:
        raise RuntimeError(f"git commit failed: {result.stderr.strip()}")


def head_matches_remote_branch(repo_path: Path, remote: str, target_branch: str) -> bool:
    """True if the remote already has this exact commit at target_branch (nothing to push)."""
    local = run_git(["rev-parse", "HEAD"], cwd=repo_path)
    remote_ref = run_git(
        ["ls-remote", "--heads", remote, target_branch], cwd=repo_path
    )
    if local.returncode != 0 or remote_ref.returncode != 0:
        return False
    remote_sha = remote_ref.stdout.split("\t")[0].strip() if remote_ref.stdout else ""
    return bool(remote_sha) and remote_sha == local.stdout.strip()


def push_wip_branch(
    repo_path: Path, remote: str, target_branch: str, logger: logging.Logger, name: str
) -> str:
    """Force-push (with lease) the current HEAD to a per-developer WIP branch.

    This is what makes an auto check-in survive a disk failure: the commit is
    mirrored to `<remote>/<target_branch>`, an isolated namespace that never
    touches shared or real feature branches.

    Non-fatal by design -- the local commit is already on disk, so a failed
    push (offline, missing credentials, someone else advanced the branch) is
    logged and simply retried on the next scheduled run.

    Returns one of: "pushed", "up to date", "push failed".
    """
    if head_matches_remote_branch(repo_path, remote, target_branch):
        logger.info(f"[{name}] {remote}/{target_branch} already up to date -> up to date")
        return "up to date"

    refspec = f"HEAD:refs/heads/{target_branch}"
    result = run_git(
        ["push", "--force-with-lease", remote, refspec], cwd=repo_path
    )
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip()).replace("\n", " ")
        logger.error(f"[{name}] push to {remote}/{target_branch} failed: {detail} -> push failed")
        return "push failed"
    logger.info(f"[{name}] Mirrored HEAD to {remote}/{target_branch} -> pushed")
    return "pushed"


def resolve_push_config(repo_config: dict, default_push: dict) -> dict:
    """Return the effective push config for a repo (own 'push' override, else the default)."""
    return repo_config.get("push", default_push) or {"enabled": False}


def wip_branch_name(prefix: str, branch: str) -> str:
    """Join the configured WIP prefix and the current branch into a valid ref name."""
    return re.sub(r"/{2,}", "/", f"{prefix.rstrip('/')}/{branch}")


# ---------------------------------------------------------------------------
# Core check-in logic
# ---------------------------------------------------------------------------

def process_repository(
    repo_config: dict,
    commit_message_template: str,
    default_protected_branches: list,
    default_push: dict,
    logger: logging.Logger,
) -> tuple:
    """Process a single repository: stage and commit pending changes, then
    (if push is enabled) mirror HEAD to a per-developer WIP branch.

    Returns a (commit_outcome, push_outcome) tuple.
    commit_outcome: "committed", "no changes", "skipped", "error", "not a repo".
    push_outcome:   None (not attempted), "pushed", "up to date", "push failed".
    """
    name = repo_config["name"]
    path = Path(repo_config["path"]).expanduser()
    exclude_patterns = repo_config.get("exclude_patterns") or []
    protected_branches = {
        b.lower()
        for b in repo_config.get("protected_branches", default_protected_branches)
    }
    push_config = resolve_push_config(repo_config, default_push)

    if not path.exists():
        logger.error(f"[{name}] Path does not exist: {path} -> not a repo")
        return "not a repo", None

    if not is_git_repo(path):
        logger.error(f"[{name}] No .git directory found at: {path} -> not a repo")
        return "not a repo", None

    try:
        branch = get_current_branch(path)
        if branch == "HEAD":
            logger.info(
                f"[{name}] Detached HEAD state, refusing to auto-commit -> skipped"
            )
            return "skipped", None
        if branch.lower() in protected_branches:
            logger.info(
                f"[{name}] On protected branch '{branch}', auto check-in only runs "
                "on feature/developer branches -> skipped"
            )
            return "skipped", None

        status_output = get_status_porcelain(path)
        if not status_output.strip():
            logger.info(f"[{name}] No changes detected -> no changes")
            outcome = "no changes"
        else:
            stage_changes(path, exclude_patterns)
            if not has_staged_changes(path):
                # Everything that changed was excluded by exclude_patterns.
                logger.info(
                    f"[{name}] Changes present but all excluded by exclude_patterns -> no changes"
                )
                outcome = "no changes"
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                message = commit_message_template.format(timestamp=timestamp, name=name)
                commit_changes(path, message)
                logger.info(f"[{name}] Committed pending changes -> committed")
                outcome = "committed"

        # Mirror HEAD to the WIP branch even when nothing was committed this
        # pass -- the dev may have unpushed local commits of their own, and
        # the whole point is that nothing on this disk is irreplaceable.
        push_outcome = None
        if push_config.get("enabled"):
            remote = push_config.get("remote", "origin")
            target = wip_branch_name(push_config["branch_prefix"], branch)
            push_outcome = push_wip_branch(path, remote, target, logger, name)

        return outcome, push_outcome

    except RuntimeError as exc:
        logger.error(f"[{name}] {exc} -> error")
        return "error", None
    except Exception as exc:  # unexpected failure; still log and continue
        logger.error(f"[{name}] Unexpected error: {exc} -> error")
        return "error", None


def run_checkin_pass(config: dict, logger: logging.Logger, repos: list = None) -> dict:
    """Run one check-in pass over the given repositories (default: all of them)."""
    if repos is None:
        repos = config["repositories"]

    logger.info("=== Starting check-in pass ===")
    results = {
        "committed": 0,
        "no changes": 0,
        "skipped": 0,
        "error": 0,
        "not a repo": 0,
    }
    push_results = {"pushed": 0, "up to date": 0, "push failed": 0}

    for repo_config in repos:
        outcome, push_outcome = process_repository(
            repo_config,
            config["commit_message_template"],
            config["protected_branches"],
            config["push"],
            logger,
        )
        results[outcome] += 1
        if push_outcome is not None:
            push_results[push_outcome] += 1

    push_summary = ""
    if any(push_results.values()):
        push_summary = (
            f"; push: {push_results['pushed']} pushed, "
            f"{push_results['up to date']} up to date, "
            f"{push_results['push failed']} failed"
        )
    logger.info(
        "=== Check-in pass complete: "
        f"{results['committed']} committed, "
        f"{results['no changes']} no changes, "
        f"{results['skipped']} skipped (protected branch), "
        f"{results['error']} errors, "
        f"{results['not a repo']} not a repo"
        f"{push_summary} ==="
    )
    results["push"] = push_results
    return results


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def build_trigger(schedule: dict):
    """Build an APScheduler trigger from a schedule config dict.

    Returns (trigger, human_readable_description).
    """
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    if schedule["type"] == "cron":
        hour = schedule.get("hour", 0)
        minute = schedule.get("minute", 0)
        trigger = CronTrigger(hour=hour, minute=minute)
        description = f"daily at {hour:02d}:{minute:02d}"
    else:  # interval
        hours = schedule.get("hours", 0)
        minutes = schedule.get("minutes", 0)
        trigger = IntervalTrigger(hours=hours, minutes=minutes)
        description = f"every {hours}h {minutes}m"

    return trigger, description


def group_repos_by_schedule(config: dict) -> list:
    """Group repositories by their effective schedule.

    Each repo uses its own 'schedule' override if present, otherwise falls
    back to the top-level 'schedule'. Repos sharing an identical schedule
    are grouped so they run together as a single scheduled job.
    """
    global_schedule = config["schedule"]
    groups = {}  # json-serialized schedule -> {"schedule": ..., "repos": [...]}

    for repo in config["repositories"]:
        schedule = repo.get("schedule", global_schedule)
        key = json.dumps(schedule, sort_keys=True)
        groups.setdefault(key, {"schedule": schedule, "repos": []})
        groups[key]["repos"].append(repo)

    return list(groups.values())


def start_scheduler(config: dict, logger: logging.Logger) -> None:
    """Start BlockingScheduler jobs, one per distinct repo schedule."""
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()

    for group in group_repos_by_schedule(config):
        trigger, description = build_trigger(group["schedule"])
        repo_names = ", ".join(r["name"] for r in group["repos"])
        scheduler.add_job(
            run_checkin_pass, trigger=trigger, args=[config, logger, group["repos"]]
        )
        logger.info(f"Scheduled [{repo_names}] to check in {description}.")

    logger.info("Scheduler started. Press Ctrl+C to stop.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped by user (Ctrl+C).")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    default_config = Path(__file__).resolve().parent / DEFAULT_CONFIG_FILENAME
    parser = argparse.ArgumentParser(
        description="Automated GitHub Check-in Tool (commit-only, never pushes)."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help=f"Path to config JSON file (default: {default_config}).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single check-in pass immediately and exit (no scheduler).",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    logger = setup_logging(config["log_file"])
    logger.info(f"Loaded config from: {args.config}")

    if args.once:
        run_checkin_pass(config, logger)
        return 0

    try:
        start_scheduler(config, logger)
    except ConfigError as exc:
        logger.error(f"Configuration error: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
