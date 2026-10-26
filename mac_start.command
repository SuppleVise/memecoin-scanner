#!/bin/bash
# MMFX Mac test: double-click to fetch the latest code from GitHub and start all paper modes.
# Paper only (no wallet, no real orders). To stop: click this window, press Ctrl+C once, wait for
# the summaries on your phone. Your paper books and ntfy_topic.txt are kept between runs.
DIR="$HOME/memecoin-scanner-mac"
RAW="https://raw.githubusercontent.com/SuppleVise/memecoin-scanner/main"
mkdir -p "$DIR" && cd "$DIR" || exit 1
ok=1
for f in config.py scanner.py paper.py run.py mac_test.sh; do
  curl -fsSL "$RAW/$f" -o "$f.new" && mv "$f.new" "$f" || ok=0
done
[ $ok = 1 ] && echo "Code updated from GitHub." || echo "Update failed (no internet?), starting the version you have."
rm -f ./*.new
if [ ! -s ntfy_topic.txt ]; then
  echo "No ntfy_topic.txt in $DIR: alerts would go to a new random topic. Copy your topic file there first."
fi
caffeinate -i bash mac_test.sh
