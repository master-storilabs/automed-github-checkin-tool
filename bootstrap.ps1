# Bootstrap the Automated GitHub Check-in Tool on Windows.
# Clones (or updates) the repo under your home folder and runs the installer.
#
#   Invoke-WebRequest is NOT used - just paste this whole file into PowerShell,
#   or run:  powershell -ExecutionPolicy Bypass -File bootstrap.ps1

$ErrorActionPreference = "Stop"
$repo = "https://github.com/master-storilabs/automed-github-checkin-tool.git"
$dir  = Join-Path $HOME "automed-github-checkin-tool"

if (Test-Path (Join-Path $dir ".git")) {
    Write-Host "Updating existing checkout at $dir"
    git -C $dir pull --ff-only
} else {
    git clone $repo $dir
}

Set-Location $dir
python install.py
