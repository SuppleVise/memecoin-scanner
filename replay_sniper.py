"""Replay a recorded pump.fun feed (feed/pump_<date>.jsonl.gz) through the paper sniper.

    python3 replay_sniper.py feed/pump_2026-10-28.jsonl.gz          # current rules + coin outcomes
    python3 replay_sniper.py FILE --grid                             # try entry-rule variations

The recording only holds trades for coins the live sniper followed (first 75 s of every launch,
10 min for coins with 3+ buyers, everything for coins it held), so coins that stayed dead are
short and runners are complete: enough to compare entry rules, not to study coins after 10 min.
"""
import contextlib
import gzip
import io
import itertools
import json
import os
import sys
import tempfile
import zlib

import sniper_paper as SP


def load(path):
    out = []
    try:
        with gzip.open(path, "rt") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass   # a line cut in half at the end of a copy taken mid-run
    except (EOFError, OSError, zlib.error):
        pass           # same: the file was still being written when copied
    out.sort(key=lambda e: e["t"])
    return out


def replay(events, **overrides):
    saved = dict(SP.S)
    SP.S.update(overrides)
    try:
        d = tempfile.mkdtemp()
        bot = SP.Sniper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"), sol_usd=100.0)
        bot.s["started"] = events[0]["t"] if events else 0
        with contextlib.redirect_stdout(io.StringIO()):   # no per-trade prints during replays
            _run(bot, events)
        return bot
    finally:
        SP.S.clear()
        SP.S.update(saved)


def _run(bot, events):
    last_tick = 0
    for ev in events:
        t = ev["t"]
        if t - last_tick >= 1:
            bot.tick(t)
            last_tick = t
        bot.on_event(ev, t)
    if events:
        bot.tick(events[-1]["t"] + 1)
        bot.close_all(events[-1]["t"] + 1, "end of recording")


def outcomes(events):
    """Per launch: best price within 10 min vs the launch price, and vs the price at 30 s."""
    launch, best, at30 = {}, {}, {}
    for ev in events:
        p = SP.curve_price(ev)
        if not p:
            continue
        m = ev.get("mint")
        if ev.get("txType") == "create":
            launch[m] = (ev["t"], p)
            continue
        if m not in launch or ev["t"] - launch[m][0] > 600:
            continue
        best[m] = max(best.get(m, 0), p)
        if ev["t"] - launch[m][0] <= 30:
            at30[m] = p
    mults = sorted((best[m] / launch[m][1] for m in best), reverse=True)
    late = sorted((best[m] / at30[m] for m in best if m in at30), reverse=True)

    def share(xs, k):
        return f"{sum(x >= k for x in xs)} ({100 * sum(x >= k for x in xs) / max(1, len(xs)):.1f}%)"
    lines = [f"Launches {len(launch)}, with trades {len(best)}"]
    for name, xs in (("from launch price", mults), ("from price at 30 s", late)):
        lines.append(f"  best within 10 min {name}: " + ", ".join(f"{k}x {share(xs, k)}" for k in (2, 3, 6, 11, 21)))
    return "\n".join(lines)


def main():
    path = sys.argv[1]
    ev = load(path)
    if not ev:
        raise SystemExit("no events in file")
    hours = (ev[-1]["t"] - ev[0]["t"]) / 3600
    print(f"{len(ev)} events over {hours:.1f} h")
    print(outcomes(ev))
    bot = replay(ev)
    print("\nCurrent rules:\n" + bot.summary(now=ev[-1]["t"]))
    if "--grid" in sys.argv:
        print("\nbuyers  net_sol  ratio  max_dev  | trades  wins  total P&L")
        rows = []
        for buyers, net, ratio, dev in itertools.product((4, 6, 10, 15), (0.5, 1.5, 3.0, 6.0), (1.5, 2.0, 3.0), (1.0, 3.0)):
            b = replay(ev, MIN_BUYERS=buyers, MIN_NET_SOL=net, MIN_BUY_SELL_RATIO=ratio, MAX_DEV_BUY_SOL=dev)
            closed = b.s.get("closed", [])
            rows.append((b.total_pnl(), buyers, net, ratio, dev, len(closed), sum(c["pnl"] > 0 for c in closed)))
        for pnl, buyers, net, ratio, dev, n, w in sorted(rows, reverse=True)[:15]:
            print(f"{buyers:>6}  {net:>7}  {ratio:>5}  {dev:>7}  | {n:>6}  {w:>4}  ${pnl:+.2f}")


if __name__ == "__main__":
    main()
