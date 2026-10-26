#!/bin/bash
# Mac side of the Mac-vs-GitHub test: all three modes, scan every 60 s, own paper books
# (paper_*_mac.*) and "[mac ...]" alert titles. Ctrl+C stops both.
cd "$(dirname "$0")"
# Ctrl+C: stop the scanners, then push one summary per mode to the phone
finish() {
  trap - INT TERM EXIT
  kill $(jobs -p) 2>/dev/null; wait 2>/dev/null
  for m in moonshot early turbo; do python3 run.py --summary-now --mode $m --label mac; done
  exit 0
}
trap finish INT TERM EXIT
python3 run.py --mode moonshot --label mac &
sleep 20   # stagger so the two don't hit GeckoTerminal's ~10 calls/min at the same moment
python3 run.py --mode early --label mac &
sleep 20
python3 run.py --mode turbo --label mac &
wait
