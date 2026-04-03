#!/usr/bin/env bash
# Copyright (c) 2026 Miklos Lekszikov
# SPDX-License-Identifier: MIT
#
# drawthings_bridge — uvicorn indítás (LaunchAgent / kézi futtatás)
set -euo pipefail

BRIDGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BRIDGE_ROOT"

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# LaunchAgent / nem-interaktív környezet: ne csak `activate`-re hagyatkozzunk.
if [[ -x "${BRIDGE_ROOT}/.venv/bin/python" ]]; then
  PY="${BRIDGE_ROOT}/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  echo "run_bridge: nincs python3 és nincs .venv/bin/python" >&2
  exit 1
fi

export DRAWTHINGS_BRIDGE_HOST="${DRAWTHINGS_BRIDGE_HOST:-0.0.0.0}"
export DRAWTHINGS_BRIDGE_PORT="${DRAWTHINGS_BRIDGE_PORT:-8787}"
# 0 = macOS `script` pseudo-TTY — a draw-things-cli soronként ír, az SSE progress nem marad üres.
# Gyorsabb, kevesebb overhead: DRAWTHINGS_BRIDGE_NO_SCRIPT=1
export DRAWTHINGS_BRIDGE_NO_SCRIPT="${DRAWTHINGS_BRIDGE_NO_SCRIPT:-0}"

exec "${PY}" -m uvicorn main:app --host "${DRAWTHINGS_BRIDGE_HOST}" --port "${DRAWTHINGS_BRIDGE_PORT}"
