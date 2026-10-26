#!/bin/bash
# Mac side of the Mac-vs-GitHub test: four modes, own paper books (paper_*_mac.*) and "[mac ...]"
# alert titles. gamble runs only here: GitHub checks too rarely for 2-minute-old coins. Ctrl+C stops all.
cd "$(dirname "$0")"
MODES="moonshot early turbo gamble"
# Ctrl+C: stop the scanners, then push one summary per mode to the phone
finish() {
  trap - INT TERM EXIT
  kill $(jobs -p) 2>/dev/null; wait 2>/dev/null
  for m in $MODES; do python3 run.py --summary-now --mode $m --label mac; done
  exit 0
}
trap finish INT TERM EXIT
for m in $MODES; do
  python3 run.py --mode $m --label mac &
  sleep 15   # stagger so they don't all hit GeckoTerminal's ~10 calls/min at the same moment
done
wait
