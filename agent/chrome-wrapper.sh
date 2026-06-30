#!/bin/bash
CHROME_REAL=$(cat /opt/playwright-mcp/.chrome-path)
rm -rf /tmp/home/.cache/ms-playwright-mcp/*/Default/Sessions 2>/dev/null
rm -rf /tmp/home/.cache/ms-playwright-mcp/*/Default/Session\ Storage 2>/dev/null
exec "$CHROME_REAL" \
  --no-sandbox \
  --crashpad-handler-pid=0 \
  --disable-breakpad \
  --disable-gpu \
  --disable-session-crashed-bubble \
  --noerrdialogs \
  --hide-crash-restore-bubble \
  "$@"
