# Automated Check-in Tool — Windows Setup

A background tool that, every 30 minutes, commits whatever you have open and
pushes a copy to GitHub — so a dead disk can't lose your work. Setup takes
about 5 minutes.

**What it does**

- Auto-commits your **feature branches** every 30 minutes
- Pushes a copy to a personal branch `wip/<you>/<branch>`
- Auto-discovers every git repo in your projects folder
- Starts again automatically at every login

**What it never does**

- Touch `main`, `master`, or `develop` — it skips those entirely
- Push your real feature branch — only the `wip/…` copy
- Open, merge, or close pull requests
- Delete anything

---

## Before you start

| Need | Check with | If missing |
|------|-----------|------------|
| Python 3.8+ | `python --version` | Install from [python.org](https://www.python.org/downloads/), tick **“Add Python to PATH”** |
| Git | `git --version` | Install from [git-scm.com](https://git-scm.com/download/win) |
| A working `git push` | you push branches daily | Push any branch once so Windows caches your credentials |

---

## Setup

### 1. Check your prerequisites

Open **PowerShell** and run both — you want a version number from each, not
“not recognized”:

```powershell
python --version
git --version
```

### 2. Clone the repo and run the installer

```powershell
git clone https://github.com/master-storilabs/automed-github-checkin-tool.git "$HOME\automed-github-checkin-tool"
cd "$HOME\automed-github-checkin-tool"
python install.py
```

The installer asks four questions. **Press Enter for every one** — the
defaults are what we want:

| Prompt | Default (press Enter) |
|--------|----------------------|
| Folder that contains your git repos | the folder holding your projects |
| Your name for the backup branch prefix | taken from your git email |
| Check in every how many minutes | `30` |
| Push each check-in to your backup branch | `yes` |

### 3. Confirm it installed

Near the end of the output you should see:

```
Task Scheduler entry "Automed GitHub Check-in" created (runs at logon) and started
...
Setup complete.
```

Below that, a **verification pass** runs once and prints `committed` /
`pushed` / `no changes` lines for your repos. Keep that output — your lead
will want to see it.

### 4. Confirm the backup reached GitHub

Open one of your project repos on GitHub → **Branches** tab. Look for a
branch named:

```
wip/<your-name>/<branch-you-have-checked-out>
```

If a repo was on `main` or had nothing uncommitted, it won't have a `wip/`
branch yet — that's expected.

### 5. Confirm it survives a restart

This is the real test — the scheduler must come back on its own.

1. Sign out of Windows and back in (or restart)
2. Search **“Task Scheduler”**, open it
3. Find **Automed GitHub Check-in** in the library list
4. Check the **Status** column — it should read `Running` or `Ready`

Wait ~30 minutes, then open `auto_checkin.log` in the checkout folder — you
should see a fresh check-in pass logged.

---

## Send your lead

- [ ] The installer's final output (screenshot is fine), including the verification-pass lines
- [ ] Confirmation the Task Scheduler entry says **Running** / **Ready** after a sign-out and back in
- [ ] Confirmation you can see a `wip/<you>/…` branch on at least one repo
- [ ] Any errors — copied as text, not paraphrased

---

## If something goes wrong

| Symptom | Fix |
|---------|-----|
| `python` is not recognized | Python isn't on PATH. Reinstall from python.org with **“Add Python to PATH”** checked, reopen PowerShell, retry. |
| `git push` asks for a username/password during the verification pass | Credentials aren't cached. Push any branch manually once (Git Credential Manager stores them), then re-run `python install.py`. |
| The log never updates after login | The scheduled task may be using a Python without `pythonw.exe`. Tell your lead — it's a one-line fix to the task. |
| You want it gone | From the checkout folder: `python install.py --uninstall`. Removes the login entry, leaves your config file. |

---

## Your day-to-day doesn't change

Keep committing, pushing, and raising PRs exactly as you do now. The tool
only fills the gaps in between.

**One habit to pick up:** before you push your feature branch for review,
squash the `Automated check-in:` commits it made so your PR stays clean:

```powershell
git reset --soft origin/<your-branch>
git commit -m "Meaningful message"
git push origin <your-branch>
```

Ask your lead to walk you through it the first time.

---

Questions or anything broken → message your team lead. Full configuration
reference is in [`README.md`](../README.md).
