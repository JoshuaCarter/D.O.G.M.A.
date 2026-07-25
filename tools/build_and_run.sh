#!/usr/bin/env bash
# Build DOGMA (deploy to MO2), then launch Anomaly (DX11) via ModOrganizer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DOGMA_DEPLOY="${DOGMA_DEPLOY:-C:/GAMMA/mods/DOGMA}"

bash "$ROOT/tools/build.sh"
bash "$ROOT/tools/run_anomaly.sh"
