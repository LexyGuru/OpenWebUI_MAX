#!/usr/bin/env bash
set -euo pipefail

LABEL="com.user.drawthings-bridge"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "${PLIST_DST}" ]]; then
  launchctl unload "${PLIST_DST}" 2>/dev/null || true
  rm -f "${PLIST_DST}"
  echo "Eltávolítva: ${PLIST_DST}"
else
  echo "Nincs ilyen plist: ${PLIST_DST}"
fi
