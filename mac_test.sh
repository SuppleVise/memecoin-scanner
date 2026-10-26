#!/bin/bash
# Mac side of the Mac-vs-GitHub test: all three modes, scan every 60 s, own paper books
# (paper_*_mac.*) and "[mac ...]" alert titles. Ctrl+C stops both.
cd "$(dirname "$0")"
trap 'kill 0' EXIT
python3 run.py --mode moonshot --label mac &
sleep 20   # stagger so the two don't hit GeckoTerminal's ~10 calls/min at the same moment
python3 run.py --mode early --label mac &
sleep 20
python3 run.py --mode turbo --label mac &
wait
