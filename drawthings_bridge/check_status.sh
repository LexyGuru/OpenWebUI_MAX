#!/usr/bin/env bash
# Copyright (c) 2026 Miklos Lekszikov
# SPDX-License-Identifier: MIT
#
# Bridge futásának ellenőrzése (HTTP). Alap port: 8787 vagy DRAWTHINGS_BRIDGE_PORT.
set -euo pipefail

PORT="${DRAWTHINGS_BRIDGE_PORT:-8787}"
HOST="${DRAWTHINGS_BRIDGE_STATUS_HOST:-127.0.0.1}"
URL="http://${HOST}:${PORT}/status"

if ! curl -fsS --connect-timeout 2 "$URL" | python3 -m json.tool 2>/dev/null; then
  echo "A bridge nem elérhető: ${URL}" >&2
  echo "Próbáld: curl -v ${URL}" >&2
  exit 1
fi
