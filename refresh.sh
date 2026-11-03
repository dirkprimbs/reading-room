#!/usr/bin/env bash
# Rebuild the reading room from live BookWyrm data into primbs.ca's public_static/reading-room/,
# which goes live with the next primbs.ca publish.sh
set -euo pipefail
cd "$(dirname "$0")"
SITE="${SITE:-$HOME/Documents/websites/primbs.ca/public_static/reading-room}"
LOGS="${LOGS:-$HOME/Documents/websites/podlogs/logs}"   # the web server's access logs, for the visitor count
visits=(); [ -d "$LOGS" ] && visits=(--visits "$LOGS")
python3 -I build.py --out "$SITE" --self-host-fonts --preview-controls "${visits[@]}"   # panel hidden (type "brain")
