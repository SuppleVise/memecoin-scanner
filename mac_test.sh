#!/bin/bash
# Mac side of the Mac-vs-GitHub test: four modes, own paper books (paper_*_mac.*) and "[mac ...]"
# alert titles. Silent while running: only the per-mode summary is pushed when you stop.
# gamble and sniper run only here: GitHub checks too rarely for coins minutes or seconds old.
# Ctrl+C stops all.
cd "$(dirname "$0")"
MODES="moonshot early turbo gamble"
# Files added after the Desktop launcher was made: that launcher only refreshes its own list,
# so fetch these here (mac_test.sh itself is always refreshed).
RAW="https://raw.githubusercontent.com/SuppleVise/memecoin-scanner/main"
for f in sniper_paper.py; do
  curl -fsSL "$RAW/$f?t=$(date +%s)" -o "$f.new" && mv "$f.new" "$f"; rm -f "$f.new"
done
# Ctrl+C: stop the scanners, then push one summary per mode to the phone
finish() {
  trap - INT TERM HUP EXIT
  kill $(jobs -p) 2>/dev/null; wait 2>/dev/null
  for m in $MODES; do python3 run.py --summary-now --mode $m --label mac; done
  python3 sniper_paper.py --summary-now --label mac
  exit 0
}
# New rules = new $50 books. When BOOKS_VERSION changes, the old books move to archive/<version>/
# (kept for the comparison, never deleted).
BOOKS_VERSION="2026-10-27-new-rules"
if [ "$(cat books_version.txt 2>/dev/null)" != "$BOOKS_VERSION" ]; then
  if ls paper_* signals_* >/dev/null 2>&1; then
    mkdir -p "archive/$(cat books_version.txt 2>/dev/null || echo before-$BOOKS_VERSION)"
    mv paper_* signals_* "archive/$(cat books_version.txt 2>/dev/null || echo before-$BOOKS_VERSION)/" 2>/dev/null
    echo "Old paper books moved to archive/. All modes start again at \$50."
  fi
  echo "$BOOKS_VERSION" > books_version.txt
fi
trap finish INT TERM HUP EXIT
for m in $MODES; do
  python3 run.py --mode $m --label mac --quiet &   # no pushes while running (MikeSz, 27 Oct)
  sleep 15   # stagger so they don't all hit GeckoTerminal's ~10 calls/min at the same moment
done
python3 sniper_paper.py --label mac --quiet &   # live pump.fun feed, paper only
wait
