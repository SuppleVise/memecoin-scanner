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
    python3 run.py --label mac        # separate paper book + "[mac ...]" alert titles

Every signal is written to signals_<mode>.csv with the token address, so you can
paste it into FOMO's search and compare what you'd have tapped with the paper book.
"""
import csv
import math
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


LABEL = arg("--label", "")
C.apply_mode(arg("--mode", C.MODE), LABEL)

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


def notify(title, text, url=None, copy=None, tags="rocket", priority=None):
    if QUIET:
        return
    if platform.system() == "Darwin":
        safe = lambda x: x.replace('"', "'")
        subprocess.run(["osascript", "-e", f'display notification "{safe(text)}" with title "{safe(title)}"'],
                       capture_output=True)
    topic = ntfy_topic()
    if not topic:
        return
    cmd = ["curl", "-sS", "-m", "10", "-H", f"Title: {title}", "-H", f"Tags: {tags}"]
    actions = []
    if copy:   # FOMO has no public link that opens a coin in the app, so: copy, open FOMO, paste in search
        actions.append(f"copy, Copy address, {copy}")
    if url:
        actions.append(f"view, Chart, {url}")
    if actions:
        cmd += ["-H", "Actions: " + "; ".join(actions)]
    if priority:  # 5 = urgent: long vibration, shows even in Focus if ntfy is allowed
        cmd += ["-H", f"Priority: {priority}"]
    cmd += ["-w", "\n%{http_code}", "-d", text, f"https://ntfy.sh/{topic}"]
    for attempt in range(3):
        out = subprocess.run(cmd, capture_output=True, text=True)
        code = out.stdout.rpartition("\n")[2]
        if out.returncode == 0 and code == "200":
            print(f"  push sent: {title}")
            return
        print(f"  push FAILED ({code or out.stderr.strip()}): {title}")
        time.sleep(3 * (attempt + 1))


def px(x):
    """Memecoin prices are tiny: show 4 significant digits, no scientific notation."""
    if x <= 0:
        return "$0"
    if x >= 1:
        return f"${x:,.2f}"
    return f"${x:.{3 - math.floor(math.log10(x))}f}"


def usd(x):
    return f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k" if x >= 1e3 else f"${x:.0f}"


def signal_text(s, bought, why=None):
    lines = [f"mcap {usd(s['mcap'])} | score {s['score']:.0f}/100 | 1h {s['h1']:+.0f}%",
             f"BUY at {px(s['price'])}  (mcap {usd(s['mcap'])})"]
    for mult, frac in C.LADDER:
        lines.append(f"SELL {frac:.0%} at {mult:g}x: {px(s['price'] * mult)}  (mcap {usd(s['mcap'] * mult)})")
    lines.append(f"Rest: sell if it falls {C.TRAIL_PCT:.0f}% from its top")
    if C.STOP_LOSS_PCT_ACTIVE:
        lines.append(f"STOP: sell all at {px(s['price'] * (1 - C.STOP_LOSS_PCT / 100))} "
                     f"(-{C.STOP_LOSS_PCT:.0f}%) or if liquidity is pulled")
    elif C.DEAD_EXIT_MULT:
        lines.append(f"Sell all if it drops to {px(s['price'] * C.DEAD_EXIT_MULT)} "
                     f"(-{(1 - C.DEAD_EXIT_MULT) * 100:.0f}%) or liquidity is pulled")
    lines.append(f"Paper: bought ${C.POSITION_SIZE_USD:.0f}" if bought else f"Paper: not bought ({why})")
    lines.append(f"Address: {s['token']}")
    return "\n".join(lines)


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
            f"All signals so far: {sh['signals']}, reached {hits}. Best {sh['best'] or '-'}.\n"
            f"Hit the -{C.STOP_LOSS_PCT:.0f}% stop but later doubled: {sh['stopped_then_2x']}"
            + ("\nPaper account is out of money; signals are still being tracked." if sm["halted"] else ""))


def tick(book, now=None):
    tag = f"[{LABEL + ' ' if LABEL else ''}{C.MODE}] "
    # 1. manage what we hold first, exits beat entries
    for tok, pos in list(book.s["positions"].items()):
        pair = scanner.fetch_pair(pos["pair"])
        pnl = book.check_exits(tok, pair)
        if pnl is not None:
            last = book.journal_tail()
            print(f"  EXIT {pos['symbol']}: {pnl:+.2f} USD ({last})")
            mult = pos["last"] / pos["entry"]
            rug = "liquidity pulled" in last or "pair missing" in last
            if rug:
                title = f"{tag}RUG {pos['symbol']}: liquidity pulled, paper sold at {mult:.2f}x"
            elif "stop loss" in last:
                title = f"{tag}STOP LOSS {pos['symbol']}: sold all at {mult:.2f}x"
            else:
                title = f"{tag}SELL {pos['symbol']} at {mult:.2f}x"
            notify(title,
                   f"{last}\nPrice now {px(pos['last'])} (bought {px(pos['entry'])})\n"
                   f"Paper result of this sell: {pnl:+.2f} USD"
                   + ("\nIf you hold it in FOMO: sell everything now, whatever is left." if rug else ""),
                   copy=tok, tags="rotating_light" if rug else "moneybag" if pnl > 0 else "chart_with_downwards_trend",
                   priority=5 if rug or "stop loss" in last else None)
    # 2. follow every past signal (shadow tickets) for the hit-rate statistics
    if book.s["shadow"]:
        pairs = scanner.fetch_pairs(list(book.s["shadow"]))
        if pairs:  # empty means the API call failed: don't count everything as rugged
            for tok, sym, kind, mult, price, url in book.shadow_update(pairs, now):
                if tok in book.s["traded"]:
                    continue  # the paper account bought it: its own SELL alerts cover it
                if kind == "rug":
                    notify(f"{tag}RUG {sym}: liquidity pulled ({mult:.2f}x)",
                           f"A signal the paper account did not buy lost its liquidity (price {px(price)}).\n"
                           f"If you bought it in FOMO: sell everything now, whatever is left.",
                           url=url, copy=tok, tags="rotating_light", priority=5)
                elif kind == "stop":
                    notify(f"{tag}STOP {sym}: down {(1 - mult) * 100:.0f}%",
                           f"A signal the paper account did not buy fell to {px(price)} ({mult:.2f}x).\n"
                           f"If you bought it in FOMO: rule says sell all now.",
                           url=url, copy=tok, tags="warning", priority=5)
                else:
                    notify(f"{tag}{sym} hit {kind}",
                           f"A signal the paper account did not buy is now {px(price)} ({mult:.2f}x).\n"
                           f"If you bought it in FOMO: follow the sell steps from the signal alert.",
                           url=url, copy=tok, tags="moneybag")
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
            notify(f"{tag}BUY signal: {s['symbol']}", signal_text(s, True), url=s["url"], copy=s["token"])
        else:
            print(f"  (paper skip: {why}; still tracked as a shadow ticket)")
            notify(f"{tag}Signal (not bought): {s['symbol']}", signal_text(s, False, why),
                   url=s["url"], copy=s["token"], tags="eyes")
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
    if "--test-signal" in sys.argv:
        demo = {"symbol": "DEMO", "token": "45rVFMxTWbzat7KFEnDkdaga49e4hWYrpq2W34aQpump", "price": 0.000237,
                "mcap": 237_055, "score": 90, "h1": 347, "liquidity": 40_097,
                "url": "https://dexscreener.com/solana/87k8mb4cbodh3eyuvvl7qkkkc8kwuvmddrs29jg6dzsz"}
        notify(f"[{C.MODE}] TEST BUY signal: DEMO", signal_text(demo, True), url=demo["url"], copy=demo["token"])
        print(signal_text(demo, True))
        e, now_px = demo["price"] * 1.02, demo["price"] * 2.08
        notify(f"[{C.MODE}] TEST SELL DEMO at {now_px / e:.2f}x",
               f"take profit at 2.0x (now {now_px / e:.2f}x)\nPrice now {px(now_px)} (bought {px(e)})\n"
               f"Paper result of this sell: +4.88 USD", copy=demo["token"], tags="moneybag")
        notify(f"[{C.MODE}] TEST STOP LOSS DEMO: sold all at 0.54x",
               f"stop loss -46.0%\nPrice now {px(e * 0.54)} (bought {px(e)})\n"
               f"Paper result of this sell: -4.79 USD", copy=demo["token"], tags="chart_with_downwards_trend",
               priority=5)
        notify(f"[{C.MODE}] TEST RUG DEMO: liquidity pulled, paper sold at 0.01x",
               f"liquidity pulled 2,075 vs 40,097\nPrice now {px(e * 0.008)} (bought {px(e)})\n"
               f"Paper result of this sell: -10.00 USD\n"
               f"If you hold it in FOMO: sell everything now, whatever is left.",
               copy=demo["token"], tags="rotating_light", priority=5)
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
