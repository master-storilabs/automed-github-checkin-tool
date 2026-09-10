# Developer setup

One-time setup so your work is auto-committed and backed up to GitHub while
you work. It commits your **feature branches** on a schedule and pushes a
copy to a personal `wip/<you>/<branch>` branch — it never touches `main` or
your real feature branch, and it never `git push`es your feature branch for
you.

> **On Windows?** Follow the step-by-step [Windows setup guide](docs/windows-setup.md).

## Quick start

**Requirements:** Python 3.8+, git, and `git push` that works without
prompting for a password (SSH key or a cached credential / token).

### Windows (PowerShell)

```powershell
git clone https://github.com/master-storilabs/automed-github-checkin-tool.git "$HOME\automed-github-checkin-tool"
cd "$HOME\automed-github-checkin-tool"
python install.py
```

### macOS / Linux

```bash
git clone https://github.com/master-storilabs/automed-github-checkin-tool.git ~/automed-github-checkin-tool
cd ~/automed-github-checkin-tool
python3 install.py
```

`install.py` asks four things (all have sensible defaults):

| Prompt | Default | Notes |
|---|---|---|
| Folder that holds your repos | the folder above this checkout | The tool auto-discovers every git repo directly inside it — clone a new project there later and it's picked up automatically, no config edit. |
| Your name for the backup branch | from your git email | Becomes the `wip/<name>/` prefix. Keep it unique per person. |
| Check-in interval (minutes) | `30` | |
| Push to the backup branch | `yes` | Say yes — this is what survives a dead laptop. |

It then registers the scheduler to start at login (Task Scheduler / launchd /
systemd), and runs one check-in pass so you can see it working.

## After setup

- **Config:** `config.local.json` in the checkout (git-ignored — your machine only).
- **Log:** `auto_checkin.log` in the checkout.
- **It starts at login** and runs every N minutes after that.

### If a repo uses a long-lived branch other than `main`/`master`/`develop`

Open `config.local.json` and add it to `protected_branches`, e.g.:

```json
"protected_branches": ["main", "master", "develop", "dev", "production"]
```

The tool will then refuse to auto-commit while any of those is checked out.

### Updating your feature branch / opening a PR

That stays manual. The auto check-ins pile up as `Automated check-in: …`
commits on your local branch. Before you push for review, squash them:

```bash
git reset --soft origin/<your-branch>     # collapse auto-commits into staged changes
git commit -m "Meaningful message"
git push origin <your-branch>             # this is what updates your PR
```

### Remove it

```bash
python install.py --uninstall
```

## How it behaves

- Only commits on branches **not** in `protected_branches`; skips detached HEAD.
- `git add` respects `.gitignore` plus any `exclude_patterns` in the config.
- Auto-push goes only to `wip/<name>/<branch>` with `--force-with-lease`.
  Your real feature branch and `main` are never pushed by the tool.
- A failed push (offline, etc.) is logged and retried next run — your local
  commit is safe regardless.

See [README.md](README.md) for the full configuration reference.
