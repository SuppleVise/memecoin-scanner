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
  curl -fsSL "$RAW/$f" -o "$f.new" && mv "$f.new" "$f"; rm -f "$f.new"
done
# Ctrl+C: stop the scanners, then push one summary per mode to the phone
finish() {
  trap - INT TERM HUP EXIT
  kill $(jobs -p) 2>/dev/null; wait 2>/dev/null
  for m in $MODES; do python3 run.py --summary-now --mode $m --label mac; done
  python3 sniper_paper.py --summary-now --label mac
  exit 0
}
trap finish INT TERM HUP EXIT
for m in $MODES; do
  python3 run.py --mode $m --label mac --quiet &   # no pushes while running (MikeSz, 27 Oct)
  sleep 15   # stagger so they don't all hit GeckoTerminal's ~10 calls/min at the same moment
done
python3 sniper_paper.py --label mac --quiet &   # live pump.fun feed, paper only
wait
