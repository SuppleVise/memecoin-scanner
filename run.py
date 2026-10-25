"""Main loop. Paper only.

    python3 run.py                    # moonshot mode (default): $10 tickets aiming for 5x-50x
    python3 run.py --mode momentum    # short scalps, tight stop
    python3 run.py --once             # one scan, print signals, exit
    python3 run.py --report           # paper results so far
    python3 run.py --quiet            # no notifications
    python3 run.py --test-push        # send one test alert to your iPhone (ntfy app)
    python3 run.py --tick             # one cycle then exit (used by GitHub Actions)

Every signal is written to signals_<mode>.csv with the token address, so you can
paste it into FOMO's search and compare what you'd have tapped with the paper book.
"""
import csv
import os
import platform
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone

import config as C


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


C.apply_mode(arg("--mode", C.MODE))

import scanner  # noqa: E402  (after apply_mode so it sees the mode's numbers)
from paper import Paper  # noqa: E402

QUIET = "--quiet" in sys.argv


def ntfy_topic():
    """iPhone push via the free ntfy app (ntfy.sh). The topic name is the password:
    anyone who knows it can read your alerts, so it is random and kept in a local file."""
    if os.environ.get("NTFY_TOPIC"):
        return os.environ["NTFY_TOPIC"]
    path = "ntfy_topic.txt"
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("mmfx-" + secrets.token_urlsafe(12).replace("_", "").replace("-", "") + "\n")
    with open(path) as f:
        return f.read().strip()


def notify(title, text, url=None):
    if QUIET:
        return
    if platform.system() == "Darwin":
        safe = lambda x: x.replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{safe(text)}" with title "{safe(title)}"'],
                       capture_output=True)
    cmd = ["curl", "-sS", "-m", "10", "-H", f"Title: {title}", "-H", "Tags: rocket"]
    if url:
        cmd += ["-H", f"Click: {url}"]
    subprocess.run(cmd + ["-d", text, f"https://ntfy.sh/{ntfy_topic()}"], capture_output=True)


def record_signals(sigs):
    new = not os.path.exists(C.SIGNALS_FILE)
    with open(C.SIGNALS_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "symbol", "token", "price", "liquidity", "mcap", "score", "h1", "url"])
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for s in sigs:
            w.writerow([ts, s["symbol"], s["token"], s["price"], s["liquidity"],
                        s["mcap"], s["score"], s["h1"], s["url"]])


def fomo_line(s):
    targets = f"5x = ${s['mcap'] * 5 / 1e6:.1f}M mcap, 50x = ${s['mcap'] * 50 / 1e6:.0f}M mcap"
    return (f"  >>> SIGNAL {s['symbol']}  mcap ${s['mcap']:,.0f}  liq ${s['liquidity']:,.0f}  "
            f"1h {s['h1']}%  score {s['score']}\n"
            f"      FOMO search: {s['token']}\n"
            f"      chart: {s['url']}\n"
            f"      for this coin: {targets}")


def tick(book):
    # 1. manage what we hold first, exits beat entries
    for tok, pos in list(book.s["positions"].items()):
        pair = scanner.fetch_pair(pos["pair"])
        pnl = book.check_exits(tok, pair)
        if pnl is not None:
            print(f"  EXIT {pos['symbol']}: {pnl:+.2f} USD")
            notify(f"Paper exit {pos['symbol']}", f"{pnl:+.2f} USD")
    if book.equity() < C.KILL_SWITCH_EQUITY_USD:
        book.s["halted"] = True
        book.save()
        return
    # 2. look for new entries
    sigs = scanner.scan()
    if sigs:
        record_signals(sigs)
    for s in sigs:
        print(fomo_line(s))
        why = book.enter(s)
        if why is None:
            print(f"  PAPER BUY {s['symbol']} ${C.POSITION_SIZE_USD:.0f} @ {s['price']:.8g}")
            notify(f"Signal: {s['symbol']}",
                   f"mcap ${s['mcap']:,.0f}, score {s['score']}. Paper bought ${C.POSITION_SIZE_USD:.0f}.\n"
                   f"FOMO search: {s['token']}", url=s["url"])
        else:
            print(f"  (paper skip: {why})")
    print(f"  {book.summary()}")


def main():
    book = Paper()
    if "--report" in sys.argv:
        for k, v in book.summary().items():
            print(f"{k:>16}: {v}")
        return
    if "--test-push" in sys.argv:
        notify("MMFX scanner", "Test alert. If you see this on your iPhone, push works.")
        print(f"sent test alert to ntfy topic: {ntfy_topic()}")
        return
    if "--tick" in sys.argv:
        # one full cycle (exits + scan + paper entries), then exit: for GitHub Actions / cron
        tick(book)
        return
    if "--once" in sys.argv:
        for s in scanner.scan():
            print(fomo_line(s))
        return
    print(f"PAPER MODE [{C.MODE}]. No wallet is connected and no real orders are sent.")
    if not QUIET:
        print(f"iPhone alerts: subscribe to topic '{ntfy_topic()}' in the ntfy app")
    while True:
        print(datetime.now(timezone.utc).strftime("%H:%M:%S"), "tick")
        try:
            tick(book)
        except Exception as e:  # keep running through API hiccups
            print("  error:", e)
        if book.s["halted"]:
            print("KILL SWITCH tripped. Stopping. Review the journal before changing anything.")
            notify("Kill switch", "Paper account hit its loss limit. Scanner stopped.")
            break
        time.sleep(C.POLL_SECONDS)


if __name__ == "__main__":
    main()
