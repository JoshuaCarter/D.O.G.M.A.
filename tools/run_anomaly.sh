#!/usr/bin/env bash
# Same as Steam shortcut:
#   start in: C:\GAMMA\
#   target:   "C:\GAMMA\ModOrganizer.exe" "moshortcut://:Anomaly (DX11)"
#
# Git Bash cannot exec the .exe directly (Permission denied), so spawn via cmd.
# MSYS_NO_PATHCONV stops Git Bash from turning start's /D into a bogus D:\ path.
set -euo pipefail

echo 'run_anomaly: "C:\GAMMA\ModOrganizer.exe" "moshortcut://:Anomaly (DX11)"'
MSYS_NO_PATHCONV=1 cmd.exe /c start "" /D "C:\GAMMA" "C:\GAMMA\ModOrganizer.exe" "moshortcut://:Anomaly (DX11)"
