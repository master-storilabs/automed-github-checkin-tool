#!/usr/bin/env python3
"""Interactive installer for the Automated GitHub Check-in Tool.

    python install.py              # install / reconfigure on this machine
    python install.py --uninstall  # remove the login autostart entry

What it does:
  1. checks Python + git
  2. installs the Python dependency (apscheduler)
  3. writes a personal config.local.json (which repos to watch, your WIP
     branch name, how often)
  4. registers the scheduler to start at login
     (Task Scheduler / launchd / systemd --user)
  5. runs one verification pass and prints the result

config.local.json is git-ignored, so your machine-specific settings are
never committed.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.local.json"
SCRIPT_PATH = HERE / "auto_checkin.py"
TASK_NAME = "Automed GitHub Check-in"
LAUNCHD_LABEL = "com.storilabs.autocheckin"
SYSTEMD_UNIT = "auto-checkin.service"


# ---------------------------------------------------------------------------
# small console helpers
# ---------------------------------------------------------------------------

def say(msg=""):
    print(msg, flush=True)


def step(msg):
    say(f"\n==> {msg}")


def die(msg):
    say(f"\nERROR: {msg}")
    sys.exit(1)


def ask(prompt, default):
    """Prompt with a default. Falls back to the default when non-interactive."""
    interactive = bool(sys.stdin) and sys.stdin.isatty()
    if not interactive:
        say(f"{prompt}: {default}  (non-interactive, using default)")
        return default
    try:
        raw = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        say(f"{prompt}: {default}  (no input, using default)")
        return default
    return raw or default


def ask_yes_no(prompt, default=True):
    d = "Y/n" if default else "y/N"
    ans = ask(f"{prompt} ({d})", "yes" if default else "no").lower()
    return ans in ("y", "yes", "true", "1")


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------

def preflight():
    step("Checking prerequisites")
    if sys.version_info < (3, 8):
        die(f"Python 3.8+ required, this is {platform.python_version()}.")
    say(f"  Python {platform.python_version()} OK")
    if not shutil.which("git"):
        die("git was not found on PATH. Install git and re-run.")
    say("  git OK")
    if not SCRIPT_PATH.is_file():
        die(f"auto_checkin.py not found next to this script ({SCRIPT_PATH}).")


def install_deps():
    step("Installing the Python dependency (apscheduler)")
    req = HERE / "requirements.txt"
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", str(req)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        say(result.stdout)
        say(result.stderr)
        die("pip install failed. Fix the error above and re-run.")
    say("  dependency installed")


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def git_config_value(key):
    result = subprocess.run(
        ["git", "config", "--get", key], capture_output=True, text=True
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def default_wip_name():
    email = git_config_value("user.email")
    if email and "@" in email:
        local = "".join(c for c in email.split("@", 1)[0].lower() if c.isalnum() or c in "._-")
        if local:
            return local
    name = git_config_value("user.name")
    if name:
        return "".join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c in "._-")
    return os.environ.get("USER") or os.environ.get("USERNAME") or "dev"


def build_config():
    step("Configuring")

    default_scan = str(HERE.parent)
    scan = ask(
        "Folder that contains your git repos (the tool auto-discovers repos inside it)",
        default_scan,
    )
    scan_path = Path(scan).expanduser()
    if not scan_path.is_dir():
        die(f"Not a folder: {scan_path}")

    name = ask("Your name for the backup branch prefix wip/<name>/", default_wip_name())
    branch_prefix = f"wip/{name.strip().strip('/')}/"

    minutes = ask("Check in every how many minutes", "30")
    try:
        minutes = int(minutes)
        if minutes <= 0:
            raise ValueError
    except ValueError:
        die("minutes must be a positive whole number.")

    enable_push = ask_yes_no(
        "Push each check-in to your wip/ backup branch on the remote (needed to survive a disk crash)",
        default=True,
    )

    config = {
        "scan_directories": [str(scan_path)],
        "repositories": [],
        "schedule": {"type": "interval", "hours": 0, "minutes": minutes},
        "commit_message_template": "Automated check-in: {name} at {timestamp}",
        "log_file": str(HERE / "auto_checkin.log"),
        "protected_branches": ["main", "master", "develop"],
        "push": {
            "enabled": enable_push,
            "remote": "origin",
            "branch_prefix": branch_prefix,
        },
    }

    if CONFIG_PATH.exists():
        backup = CONFIG_PATH.parent / (CONFIG_PATH.name + ".bak")
        shutil.copy2(CONFIG_PATH, backup)
        say(f"  existing config backed up to {backup.name}")

    CONFIG_PATH.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    say(f"  wrote {CONFIG_PATH.name}")

    say("\n  Review the protected_branches list if any of your repos use a")
    say("  long-lived branch other than main/master/develop (e.g. 'dev',")
    say("  'production') - add it so the tool never auto-commits there.")
    return config


# ---------------------------------------------------------------------------
# autostart registration (per-OS)
# ---------------------------------------------------------------------------

def pythonw_executable():
    """Prefer pythonw.exe on Windows so no console window pops up at login."""
    if os.name == "nt":
        candidate = Path(sys.executable).with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _run_powershell(script):
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        capture_output=True, text=True,
    )


def install_autostart_windows():
    # Use the Scheduled Tasks API via PowerShell rather than schtasks.exe:
    # on some Windows editions `schtasks /Create /SC ONLOGON` is refused for a
    # non-elevated user ("Access is denied"), while Register-ScheduledTask for
    # the current user's logon works without elevation.
    py = pythonw_executable()
    script = f"""
$ErrorActionPreference = 'Stop'
$user = "$env:USERDOMAIN\\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute '{py}' -Argument '"{SCRIPT_PATH}" --config "{CONFIG_PATH}"' -WorkingDirectory '{HERE}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName '{TASK_NAME}'
"""
    result = _run_powershell(script)
    if result.returncode != 0:
        say(result.stdout)
        say(result.stderr)
        die("Could not register the scheduled task.")
    say(f'  Task Scheduler entry "{TASK_NAME}" created (runs at logon) and started')


def uninstall_autostart_windows():
    result = _run_powershell(
        f"try {{ Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false "
        f"-ErrorAction Stop; 'removed' }} catch {{ 'absent' }}"
    )
    say("  removed the Task Scheduler entry" if "removed" in result.stdout
        else "  no Task Scheduler entry to remove")


def _launch_agents_dir():
    d = Path.home() / "Library" / "LaunchAgents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_autostart_macos():
    plist = _launch_agents_dir() / f"{LAUNCHD_LABEL}.plist"
    plist.write_text(textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key><string>{LAUNCHD_LABEL}</string>
          <key>ProgramArguments</key>
          <array>
            <string>{sys.executable}</string>
            <string>{SCRIPT_PATH}</string>
            <string>--config</string>
            <string>{CONFIG_PATH}</string>
          </array>
          <key>RunAtLoad</key><true/>
          <key>KeepAlive</key><true/>
          <key>StandardOutPath</key><string>{HERE / 'launchd.out.log'}</string>
          <key>StandardErrorPath</key><string>{HERE / 'launchd.err.log'}</string>
        </dict>
        </plist>
    """), encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, text=True)
    result = subprocess.run(["launchctl", "load", str(plist)], capture_output=True, text=True)
    if result.returncode != 0:
        say(result.stderr)
        die("launchctl load failed.")
    say(f"  LaunchAgent {plist.name} installed and loaded")


def uninstall_autostart_macos():
    plist = _launch_agents_dir() / f"{LAUNCHD_LABEL}.plist"
    if plist.exists():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, text=True)
        plist.unlink()
        say("  removed the LaunchAgent")
    else:
        say("  no LaunchAgent to remove")


def _systemd_unit_path():
    d = Path.home() / ".config" / "systemd" / "user"
    d.mkdir(parents=True, exist_ok=True)
    return d / SYSTEMD_UNIT


def install_autostart_linux():
    if not shutil.which("systemctl"):
        say("  systemd not available. Add this to your login startup manually:")
        say(f"    {sys.executable} {SCRIPT_PATH} --config {CONFIG_PATH} &")
        return
    unit = _systemd_unit_path()
    unit.write_text(textwrap.dedent(f"""\
        [Unit]
        Description=Automated GitHub Check-in Tool

        [Service]
        ExecStart={sys.executable} {SCRIPT_PATH} --config {CONFIG_PATH}
        Restart=on-failure
        WorkingDirectory={HERE}

        [Install]
        WantedBy=default.target
    """), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        say(result.stderr)
        die("systemctl --user enable failed. You may need: loginctl enable-linger $USER")
    say(f"  systemd user service {SYSTEMD_UNIT} enabled and started")


def uninstall_autostart_linux():
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT],
            capture_output=True, text=True,
        )
    unit = _systemd_unit_path()
    if unit.exists():
        unit.unlink()
        say("  removed the systemd user service")
    else:
        say("  no systemd user service to remove")


def autostart(action):
    system = platform.system()
    handlers = {
        "Windows": (install_autostart_windows, uninstall_autostart_windows),
        "Darwin": (install_autostart_macos, uninstall_autostart_macos),
        "Linux": (install_autostart_linux, uninstall_autostart_linux),
    }
    if system not in handlers:
        die(f"Unsupported OS: {system}")
    install, uninstall = handlers[system]
    (install if action == "install" else uninstall)()


# ---------------------------------------------------------------------------
# verification
# ---------------------------------------------------------------------------

def verify():
    step("Running one verification pass (this may create a check-in commit)")
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--config", str(CONFIG_PATH), "--once"],
        text=True,
    )
    if result.returncode != 0:
        die("Verification pass exited with an error - see the output above.")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Installer for the Automated GitHub Check-in Tool.",
    )
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove the login autostart entry and exit.")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Do not run the verification pass at the end.")
    args = parser.parse_args(argv)

    if args.uninstall:
        step("Removing autostart entry")
        autostart("uninstall")
        say("\nDone. config.local.json was left in place; delete it by hand if you want.")
        return 0

    say("Automated GitHub Check-in Tool - setup")
    say("=" * 40)
    preflight()
    install_deps()
    build_config()
    step("Registering login autostart")
    autostart("install")
    if not args.skip_verify:
        verify()

    say("\n" + "=" * 40)
    say("Setup complete.")
    say(f"  config:   {CONFIG_PATH}")
    say(f"  log:      {HERE / 'auto_checkin.log'}")
    say("  schedule: runs at login, then on the interval you chose")
    say("\nTo change what it watches, edit config.local.json.")
    say("To remove it:  python install.py --uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
