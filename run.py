"""Main loop. Paper only.

    python3 run.py                    # moonshot mode (default): $10 tickets aiming for 5x-50x
    python3 run.py --mode momentum    # short scalps, tight stop
    python3 run.py --once             # one scan, print signals, exit
    python3 run.py --report           # paper results so far
    python3 run.py --quiet            # no notifications
    python3 run.py --test-push        # send one test alert to your iPhone (ntfy app)
    python3 run.py --tick             # one cycle then exit (used by GitHub Actions)
    python3 run.py --summary-now      # send the daily summary right away
    python3 run.py --mode early       # fresh launches, 10 min to 2 h old

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
    if "NTFY_TOPIC" in os.environ:
        return os.environ["NTFY_TOPIC"].strip() or None  # set but empty (CI without secret): no push
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
    topic = ntfy_topic()
    if not topic:
        return
    cmd = ["curl", "-sS", "-m", "10", "-H", f"Title: {title}", "-H", "Tags: rocket"]
    if url:
        cmd += ["-H", f"Click: {url}"]
    subprocess.run(cmd + ["-d", text, f"https://ntfy.sh/{topic}"], capture_output=True)


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


def daily_summary(book):
    sm = book.summary()
    sh = sm["shadow"]
    hits = ", ".join(f"{k} {v}" for k, v in sh["peaked_at_least"].items())
    return (f"Paper ${sm['equity']:.2f} (start ${sm['start']:.0f}), {sm['tickets']} tickets, "
            f"{len(sm['open'])} open.\n"
            f"All signals so far: {sh['signals']}, reached {hits}. Best {sh['best'] or '-'}."
            + ("\nPaper account is out of money; signals are still being tracked." if sm["halted"] else ""))


def tick(book, now=None):
    tag = f"[{C.MODE}] "
    # 1. manage what we hold first, exits beat entries
    for tok, pos in list(book.s["positions"].items()):
        pair = scanner.fetch_pair(pos["pair"])
        pnl = book.check_exits(tok, pair)
        if pnl is not None:
            last = book.journal_tail()
            print(f"  EXIT {pos['symbol']}: {pnl:+.2f} USD ({last})")
            notify(f"{tag}Paper sell {pos['symbol']}", f"{pnl:+.2f} USD. {last}")
    # 2. follow every past signal (shadow tickets) for the hit-rate statistics
    if book.s["shadow"]:
        pairs = scanner.fetch_pairs(list(book.s["shadow"]))
        if pairs:  # empty means the API call failed: don't count everything as rugged
            book.shadow_update(pairs, now)
    if not book.s["halted"] and book.equity() < C.KILL_SWITCH_EQUITY_USD:
        book.s["halted"] = True
        notify(f"{tag}Paper account empty",
               "The $50 paper budget is spent. New signals are still tracked for the statistics.")
    # 3. look for new signals
    seen = set(book.s["shadow"]) | set(book.s["shadow_done"]) | set(book.s["traded"])
    sigs = scanner.scan(skip=seen)
    if sigs:
        record_signals(sigs)
    for s in sigs:
        print(fomo_line(s))
        book.shadow_add(s, now)
        why = book.enter(s, now)
        if why is None:
            print(f"  PAPER BUY {s['symbol']} ${C.POSITION_SIZE_USD:.0f} @ {s['price']:.8g}")
            notify(f"{tag}Signal: {s['symbol']}",
                   f"mcap ${s['mcap']:,.0f}, score {s['score']}. Paper bought ${C.POSITION_SIZE_USD:.0f}.\n"
                   f"FOMO search: {s['token']}", url=s["url"])
        else:
            print(f"  (paper skip: {why}; still tracked as a shadow ticket)")
            notify(f"{tag}Signal (not bought): {s['symbol']}",
                   f"mcap ${s['mcap']:,.0f}, score {s['score']}. Paper account skipped it: {why}.\n"
                   f"FOMO search: {s['token']}", url=s["url"])
    # 4. MikeSz's rule: once the $50 has doubled, take the $50 out
    if book.withdraw_due():
        notify(f"{tag}Take your $50 out",
               f"Paper equity reached ${book.equity():.2f}. With real money this is the moment to "
               f"withdraw the starting $50 and keep trading only profits.")
    # 5. once-a-day summary
    t = datetime.fromtimestamp(now or time.time(), timezone.utc)
    if t.hour >= C.DAILY_SUMMARY_UTC_HOUR and book.s["summary_day"] != t.strftime("%Y-%m-%d"):
        book.s["summary_day"] = t.strftime("%Y-%m-%d")
        notify(f"{tag}Daily summary", daily_summary(book))
    book.save()
    print(f"  {book.summary()}")


def main():
    book = Paper()
    if "--report" in sys.argv:
        for k, v in book.summary().items():
            print(f"{k:>16}: {v}")
        return
    if "--summary-now" in sys.argv:
        notify(f"[{C.MODE}] Summary", daily_summary(book))
        print(daily_summary(book))
        return
    if "--test-push" in sys.argv:
        notify("MMFX scanner", "Test alert. If you see this on your iPhone, push works.")
        print("sent test alert" if ntfy_topic() else "no NTFY_TOPIC set, nothing sent")
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
        time.sleep(C.POLL_SECONDS)


if __name__ == "__main__":
    main()
