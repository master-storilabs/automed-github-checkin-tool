# Automated GitHub Check-in Tool

A small, cross-platform Python tool that watches a list of local git
repositories and, on a schedule, automatically **commits** any pending local
changes (staged, unstaged, or untracked files) in each of them.

**This tool only commits. It never runs `git push`.** Pushing remains a
manual, deliberate action you take yourself.

**By default, it only auto-commits on feature/developer branches.** It
refuses to commit while `main`, `master`, or `develop` is checked out (or the
repo is in a detached HEAD state) — see `protected_branches` below.

Scheduling is done in-process with [APScheduler](https://apscheduler.readthedocs.io/),
so it behaves identically on Windows, macOS, and Linux — no dependency on
cron, Task Scheduler, or launchd for the scheduling logic itself (those are
only used, optionally, to auto-start the script on login — see below).

## Install

Requires Python 3.8+.

```bash
pip install -r requirements.txt
```

## Configuration (`config.json`)

By default the tool looks for `config.json` next to `auto_checkin.py`. Use
`--config <path>` to point at a different file.

```json
{
  "repositories": [
    {
      "name": "example-project",
      "path": "C:/Users/you/projects/example-project",
      "exclude_patterns": ["*.log", "node_modules/", ".env"]
    },
    {
      "name": "another-project",
      "path": "/home/you/projects/another-project",
      "schedule": { "type": "interval", "hours": 0, "minutes": 15 }
    }
  ],
  "schedule": {
    "type": "cron",
    "hour": 18,
    "minute": 0
  },
  "commit_message_template": "Automated check-in: {name} at {timestamp}",
  "log_file": "auto_checkin.log",
  "protected_branches": ["main", "master", "develop"]
}
```

### `repositories` (array, required)

One entry per local git repository to watch:

| Field              | Required | Description                                                                 |
|--------------------|----------|-------------------------------------------------------------------------------|
| `name`             | yes      | A friendly label used in log messages and the commit message template.       |
| `path`             | yes      | Absolute path to the local repository's working directory.                   |
| `exclude_patterns` | no       | List of gitignore-style patterns to exclude from staging (e.g. `*.log`, `node_modules/`). Files matching these are left untouched even if changed. |
| `schedule`         | no       | Per-repo schedule override, same shape as the top-level `schedule` (below). If omitted, the repo uses the top-level `schedule`. |
| `protected_branches` | no     | Per-repo override for `protected_branches` (below). If omitted, the repo uses the top-level list. |

Per-repo `schedule` is useful on a shared team config where different
projects warrant different cadences — e.g. a fast-moving repo checked in
every 15 minutes, while quieter repos stick to the once-daily default:

```json
{
  "name": "fast-moving-project",
  "path": "C:/Users/you/projects/fast-moving-project",
  "schedule": { "type": "interval", "hours": 0, "minutes": 15 }
}
```

Under the hood, repos are grouped by their effective schedule (own override,
or the top-level default) and each group runs as its own independent
scheduled job — so repos on different cadences never wait on each other.
`--once` always runs every repo immediately regardless of any schedule.

### `schedule` (object, required)

The default schedule for any repo that doesn't define its own override.
Either a fixed daily time or a recurring interval:

**Fixed daily time:**
```json
{ "type": "cron", "hour": 18, "minute": 0 }
```
Runs once a day at the given hour/minute (24-hour, local time).

**Recurring interval:**
```json
{ "type": "interval", "hours": 1, "minutes": 30 }
```
Runs repeatedly every `hours`/`minutes` (both optional, but at least one must
be non-zero), starting one interval after the scheduler is launched.

### `commit_message_template` (string, optional)

Default: `"Automated check-in: {name} at {timestamp}"`.

Supports two placeholders:
- `{name}` — the repository's configured `name`.
- `{timestamp}` — the commit time as `YYYY-MM-DD HH:MM:SS`.

### `log_file` (string, optional)

Default: `auto_checkin.log`. Path (relative or absolute) to the log file.
All actions are logged here **and** printed to stdout.

### `protected_branches` (array, optional)

Default: `["main", "master", "develop"]` (case-insensitive).

Before touching a repo, the tool checks out the current branch name. If it
matches an entry in this list — or the repo is in a detached HEAD state —
the pass is **skipped** for that repo and logged as such; nothing is staged
or committed. This keeps auto check-ins confined to feature/developer
branches instead of landing WIP commits directly on shared branches.

Override per repo if a project uses different branch names for its main
line (e.g. `["main", "release"]`), or to widen/narrow the list for a
specific repo.

## Testing with `--once`

Before trusting the scheduler, do a dry run against your real config to
confirm it detects and commits changes correctly:

```bash
python auto_checkin.py --once
python auto_checkin.py --config /path/to/other-config.json --once
```

This runs a single pass over every configured repository and exits — no
scheduler is started. Check the log output (and `git log` in each repo) to
confirm the expected commits were made.

## Starting the real scheduler

```bash
python auto_checkin.py
```

This loads the config, starts an APScheduler `BlockingScheduler` using your
`schedule` settings, and runs indefinitely until you press `Ctrl+C`. Each
scheduled tick runs the same check-in pass as `--once`.

Keep this process running (in a terminal, `screen`/`tmux` session, or as a
background/login service — see below) for the schedule to actually fire.

## Auto-starting on login (optional, documentation only)

The scheduler only runs while the Python process is alive. If you want it to
start automatically when you log in, use your OS's native mechanism to
launch `python auto_checkin.py` (without `--once`) as a background process.
These are documented here for reference; none of this is implemented by the
tool itself.

### Windows — Task Scheduler

1. Open **Task Scheduler** → **Create Task...**
2. **General** tab: name it (e.g. "Auto Check-in"), and consider "Run whether
   user is logged on or not" if desired.
3. **Triggers** tab: **New...** → "At log on" (for the current user).
4. **Actions** tab: **New...** → Action: "Start a program"
   - Program/script: path to `python.exe` (or `pythonw.exe` to avoid a
     console window)
   - Add arguments: `auto_checkin.py`
   - Start in: the folder containing `auto_checkin.py`
5. Save. The task will launch the scheduler process at every login.

### macOS — launchd

Create `~/Library/LaunchAgents/com.yourname.autocheckin.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.yourname.autocheckin</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/auto_checkin.py</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/tmp/auto_checkin.stdout.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/auto_checkin.stderr.log</string>
</dict>
</plist>
```

Then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.autocheckin.plist
```

### Linux — systemd (user service) or cron

**systemd user service** (`~/.config/systemd/user/auto-checkin.service`):

```ini
[Unit]
Description=Automated GitHub Check-in Tool

[Service]
ExecStart=/usr/bin/python3 /path/to/auto_checkin.py
Restart=on-failure
WorkingDirectory=/path/to

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now auto-checkin.service
```

**Or, simpler cron approach:** since the tool already handles its own
scheduling internally, a login-time launch is sufficient — you don't need
cron to fire it repeatedly. A minimal `@reboot` or login-triggered cron entry
that runs `python auto_checkin.py &` once achieves the same effect as
systemd/launchd above, if you prefer not to write a unit file.

## A note on workflow noise

Auto-committing work-in-progress changes directly onto `main`/`develop` can
create a noisy history and, if anyone else pulls from that branch, may
surface half-finished work. That's why `protected_branches` is enabled by
default (see above) — the tool refuses to auto-commit on `main`, `master`,
or `develop`, so it only acts on feature/working branches unless you
explicitly reconfigure it. Squash or rebase before merging if you want a
clean history upstream. Remember: this tool never pushes, so noisy local
commits stay local until you decide otherwise.

<!-- branch-guard test note -->
