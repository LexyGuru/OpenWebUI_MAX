#!/usr/bin/env bash
# Telepíti a LaunchAgent-et: bejelentkezéskor elindul a drawthings_bridge.
set -euo pipefail

BRIDGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_SCRIPT="${BRIDGE_ROOT}/run_bridge.sh"
LABEL="com.user.drawthings-bridge"
PLIST_DST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs"
LOG_OUT="${LOG_DIR}/drawthings-bridge.log"
LOG_ERR="${LOG_DIR}/drawthings-bridge.error.log"

chmod +x "${RUN_SCRIPT}"

mkdir -p "${LOG_DIR}"

# Régi példány leállítása (ha volt)
if [[ -f "${PLIST_DST}" ]]; then
  launchctl unload "${PLIST_DST}" 2>/dev/null || true
fi

cat > "${PLIST_DST}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${RUN_SCRIPT}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${BRIDGE_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_OUT}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_ERR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
EOF

launchctl load "${PLIST_DST}"

echo "OK: LaunchAgent telepítve: ${PLIST_DST}"
echo "    Logok: ${LOG_OUT}"
echo "    Állapot: launchctl list | grep ${LABEL}"
echo "    Leállítás: launchctl unload \"${PLIST_DST}\""
