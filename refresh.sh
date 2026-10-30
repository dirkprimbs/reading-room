#!/usr/bin/env bash
# Rebuild the reading room from live BookWyrm data into primbs.ca's public_static/reading-room/,
# which goes live with the next primbs.ca publish.sh
set -euo pipefail
cd "$(dirname "$0")"
SITE="${SITE:-$HOME/Documents/websites/primbs.ca/public_static/reading-room}"
python3 -I build.py --out "$SITE" --self-host-fonts --preview-controls   # panel hidden (type "brain")
