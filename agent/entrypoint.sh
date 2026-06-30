#!/bin/bash
set -e

export HOME=/tmp/home
mkdir -p "$HOME" /tmp/crashpad-db

DISPLAY_NUM="${DISPLAY_NUM:-99}"
SCREEN_RESOLUTION="${SCREEN_RESOLUTION:-1920x1080x24}"
VNC_PORT="${VNC_PORT:-5900}"
NOVNC_PORT="${NOVNC_PORT:-6080}"

export DISPLAY=":${DISPLAY_NUM}"

echo "Starting virtual display ${DISPLAY} at ${SCREEN_RESOLUTION}..."
Xvfb "${DISPLAY}" -screen 0 "${SCREEN_RESOLUTION}" -ac +extension GLX +render -noreset &
sleep 1

echo "Starting window manager..."
fluxbox &
sleep 0.5

echo "Starting VNC server on port ${VNC_PORT}..."
x11vnc -display "${DISPLAY}" -forever -nopw -shared -rfbport "${VNC_PORT}" -quiet &
sleep 0.5

echo "Starting noVNC web client on port ${NOVNC_PORT}..."
/usr/share/novnc/utils/novnc_proxy --vnc "localhost:${VNC_PORT}" --listen "${NOVNC_PORT}" &
sleep 0.5

echo "Display stack ready. noVNC available on port ${NOVNC_PORT}."
echo "---"

exec python3 -u agent.py
