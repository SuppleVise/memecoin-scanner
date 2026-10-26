"""Paper portfolio: simulated $50 account with FOMO-like fees, slippage and hard limits.

No wallet, no keys, no orders. Prices come from DexScreener polling, so a fill
here is a guess at what a manual tap in FOMO would have got. Treat results as
optimistic: real memecoin slippage on exits during a dump is worse.
"""
import csv
import json
import os
import time
from datetime import datetime, timezone

import config as C

JOURNAL_FIELDS = ["time", "symbol", "token", "action", "reason", "price",
                  "usd", "fee", "pnl_usd", "peak_mult", "equity"]
# A quote this far from the buy price, or from the previous quote, is bad API data, not a real move.
# Without this one glitch sells the ladder at a fake price and shows a 1,000,000x winner.
MAX_MULT_FROM_ENTRY, MAX_JUMP_PER_CHECK = 1000, 200


def bad_tick(price, entry, last):
    return price / entry > MAX_MULT_FROM_ENTRY or (last and price / last > MAX_JUMP_PER_CHECK)


MILESTONES = (2, 3, 6, 11, 31)   # price multiples: +100%, +200%, +500%, +1000%, +3000%


def utc_day(ts=None):
    return datetime.fromtimestamp(ts or time.time(), timezone.utc).strftime("%Y-%m-%d")


class Paper:
    def __init__(self, state_file=None, journal_file=None, variant=None):
        """variant: None = the real rules. "all" = buy every signal, no limits (cash may go negative:
        that is money you would have had to add). "pct10" = ticket is 10% of equity, never below
        POSITION_SIZE_USD (so it only differs once the account is above $100)."""
        self.variant = variant
        self.state_file = state_file or C.STATE_FILE
        self.journal_file = journal_file or C.JOURNAL_FILE
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                self.s = json.load(f)
        else:
            self.s = {"cash": C.START_BALANCE_USD, "positions": {}, "day": utc_day(),
                      "day_pnl": 0.0, "day_trades": 0, "halted": False, "traded": [],
                      "peaks": {}}
        self.s.setdefault("peaks", {})
        self.s.setdefault("shadow", {})       # every signal, followed for SHADOW_HOURS
        self.s.setdefault("shadow_done", {})  # token -> peak multiple when tracking ended
        self.s.setdefault("withdraw_alerted", False)
        self.s.setdefault("summary_day", "")

    # --- bookkeeping ---------------------------------------------------------
    def save(self):
        tmp = self.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.s, f, indent=2)
        os.replace(tmp, self.state_file)

    def log(self, **row):
        new = not os.path.exists(self.journal_file)
        with open(self.journal_file, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            if new:
                w.writeheader()
            row.setdefault("time", datetime.now(timezone.utc).isoformat(timespec="seconds"))
            row.setdefault("equity", round(self.equity(), 2))
            w.writerow({k: row.get(k, "") for k in JOURNAL_FIELDS})

    def roll_day(self, now=None):
        d = utc_day(now)
        if d != self.s["day"]:
            self.s.update(day=d, day_pnl=0.0, day_trades=0)

    def equity(self, prices=None):
        prices = prices or {}
        val = self.s["cash"]
        for tok, p in self.s["positions"].items():
            val += p["qty"] * prices.get(tok, p["last"])
        return val

    # --- gates ---------------------------------------------------------------
    def ticket_usd(self):
        if self.variant == "pct10":
            return max(C.POSITION_SIZE_USD, round(0.10 * self.equity(), 2))
        return C.POSITION_SIZE_USD

    def can_enter(self, token, now=None):
        self.roll_day(now)
        if self.variant == "all":
            if token in self.s["positions"] or token in self.s["traded"]:
                return "already traded"
            return None
        if self.s["halted"]:
            return "KILL SWITCH: equity fell below limit, stop and review"
        if self.equity() < C.KILL_SWITCH_EQUITY_USD:
            self.s["halted"] = True
            return "KILL SWITCH tripped"
        if self.s["day_pnl"] <= -C.DAILY_LOSS_LIMIT_USD:
            return "daily loss limit hit"
        if self.s["day_trades"] >= C.MAX_TRADES_PER_DAY:
            return "max trades today"
        if len(self.s["positions"]) >= C.MAX_OPEN_POSITIONS:
            return "max open positions"
        if token in self.s["positions"]:
            return "already holding"
        if token in self.s["traded"]:
            return "already traded this token (no revenge re-entries)"
        if self.s["cash"] < self.ticket_usd():
            return "not enough cash"
        return None

    # --- actions -------------------------------------------------------------
    def enter(self, sig, now=None):
        why = self.can_enter(sig["token"], now)
        if why:
            return why
        usd = self.ticket_usd()
        fee = C.fomo_fee(usd)
        fill = sig["price"] * (1 + C.SLIPPAGE_PCT / 100)
        qty = (usd - fee) / fill
        self.s["cash"] -= usd
        self.s["min_cash"] = min(self.s.get("min_cash", self.s["cash"]), self.s["cash"])
        self.s["positions"][sig["token"]] = {
            "symbol": sig["symbol"], "pair": sig["pair"], "qty": qty, "orig_qty": qty,
            "entry": fill, "cost": usd, "peak": fill, "last": fill,
            "opened": now or time.time(), "rungs_done": 0, "entry_liq": sig.get("liquidity") or 0,
        }
        self.s["day_trades"] += 1
        self.s["traded"].append(sig["token"])
        self.s["round_tickets"] = self.s.get("round_tickets", 0) + 1
        self.log(symbol=sig["symbol"], token=sig["token"], action="BUY",
                 reason=f"score {sig['score']}", price=fill, usd=usd, fee=round(fee, 4))
        self.save()
        return None

    def _sell(self, tok, qty, price, reason):
        p = self.s["positions"][tok]
        qty = min(qty, p["qty"])
        full = qty >= p["qty"] * 0.999
        frac = 1.0 if full else qty / p["qty"]
        fill = price * (1 - C.SLIPPAGE_PCT / 100)
        gross = qty * fill
        fee = min(C.fomo_fee(gross), gross)
        net = gross - fee
        cost_part = p["cost"] * frac
        pnl = net - cost_part
        self.s["cash"] += net
        self.s["day_pnl"] += pnl
        p["qty"] -= qty
        p["cost"] -= cost_part
        peak_mult = round(p["peak"] / p["entry"], 2)
        if full:
            self.s["peaks"][tok] = peak_mult
            del self.s["positions"][tok]
        self.log(symbol=p["symbol"], token=tok, action="SELL" if full else "SELL_PART",
                 reason=reason, price=fill, usd=round(net, 4), fee=round(fee, 4),
                 pnl_usd=round(pnl, 4), peak_mult=peak_mult if full else "")
        return pnl

    def check_exits(self, tok, pair, now=None):
        """Apply exit rules to one open position given a fresh DexScreener pair dict."""
        now = now or time.time()
        p = self.s["positions"].get(tok)
        if not p:
            return None
        p.setdefault("orig_qty", p["qty"])
        p.setdefault("rungs_done", 1 if p.get("tp1_done") else 0)
        if pair is None:
            # Pair vanished from the API. In real life that is usually a rug.
            return self._sell(tok, p["qty"], p["last"] * 0.1, "pair missing: assume -90% rug fill")
        price = float(pair.get("priceUsd") or 0)
        if price <= 0 or bad_tick(price, p["entry"], p["last"]):
            return None
        p["last"] = price
        p["peak"] = max(p["peak"], price)
        mult = price / p["entry"]
        liq = (pair.get("liquidity") or {}).get("usd") or 0
        ladder = C.LADDER
        out = None
        if p["entry_liq"] and liq < p["entry_liq"] * (1 - C.LIQUIDITY_DROP_EXIT_PCT / 100):
            out = self._sell(tok, p["qty"], price, f"liquidity pulled ${liq:,.0f} (was ${p['entry_liq']:,.0f} at buy)")
        elif C.STOP_LOSS_PCT_ACTIVE and (mult - 1) * 100 <= -C.STOP_LOSS_PCT:
            out = self._sell(tok, p["qty"], price, f"stop loss {(mult - 1) * 100:.1f}%")
        elif C.DEAD_EXIT_MULT and mult <= C.DEAD_EXIT_MULT:
            out = self._sell(tok, p["qty"], price, f"dead coin {mult:.2f}x, salvage")
        elif p["rungs_done"] < len(ladder) and mult >= ladder[p["rungs_done"]][0]:
            total = 0.0
            while p["rungs_done"] < len(ladder) and mult >= ladder[p["rungs_done"]][0] and tok in self.s["positions"]:
                target, frac = ladder[p["rungs_done"]]
                p["rungs_done"] += 1
                total += self._sell(tok, p["orig_qty"] * frac, price, f"take profit at {target}x (now {mult:.2f}x)")
            out = total
        elif (p["rungs_done"] > 0 and price <= p["peak"] * (1 - C.TRAIL_PCT / 100)
              and not (C.MOONBAG_NO_TRAIL and p["rungs_done"] >= len(ladder) - 1)):
            out = self._sell(tok, p["qty"], price,
                             f"trailing stop {mult:.2f}x, peak {p['peak'] / p['entry']:.2f}x")
        elif (now - p["opened"]) / 3600 >= C.TIME_STOP_H and mult < C.TIME_STOP_MIN_MULT:
            out = self._sell(tok, p["qty"], price, f"time stop {mult:.2f}x")
        self.save()
        return out

    def journal_tail(self):
        """Reason of the last journal row, for notifications."""
        if not os.path.exists(self.journal_file):
            return ""
        with open(self.journal_file) as f:
            rows = list(csv.DictReader(f))
        return rows[-1]["reason"] if rows else ""

    # --- shadow tickets: measure the hit rate without the cash limit ----------
    def shadow_add(self, sig, now=None):
        tok = sig["token"]
        if tok in self.s["shadow"] or tok in self.s["shadow_done"] or not sig.get("price"):
            return
        self.s["shadow"][tok] = {"symbol": sig["symbol"], "entry": sig["price"], "peak": sig["price"],
                                 "last": sig["price"], "opened": now or time.time(),
                                 "url": sig.get("url"), "hits": [], "stop_hit": False, "peak_after_stop": 0,
                                 "liq": sig.get("liquidity") or 0, "rug": False}

    def shadow_update(self, pairs_by_token, now=None):
        """pairs_by_token: {token: pair dict}. Missing 3 checks in a row counts as a rug (tracking ends).
        Returns milestone events [(token, symbol, kind, multiple, price, url)] for alerts, where kind is
        "2x"/"5x"/"10x"/"50x" the first time the price reaches it, "stop" the first time it falls to the
        stop level, or "rug" once when liquidity drops like the paper exit rule or the pair vanishes (only alerted for coins the paper account doesn't hold, in case you bought them by hand)."""
        now = now or time.time()
        events = []
        stop_mult = 1 - C.STOP_LOSS_PCT / 100
        for tok, sh in list(self.s["shadow"].items()):
            sh.setdefault("hits", [])
            sh.setdefault("stop_hit", False)
            sh.setdefault("peak_after_stop", 0)
            pair = pairs_by_token.get(tok)
            if pair is not None:
                px = float(pair.get("priceUsd") or 0)
                if px > 0 and not bad_tick(px, sh["entry"], sh["last"]):
                    sh["last"] = px
                    sh["peak"] = max(sh["peak"], px)
                    mult = px / sh["entry"]
                    for m in MILESTONES:
                        if mult >= m and f"{m}x" not in sh["hits"]:
                            sh["hits"].append(f"{m}x")
                            events.append((tok, sh["symbol"], f"{m}x", mult, px, sh.get("url")))
                    if sh["stop_hit"]:
                        sh["peak_after_stop"] = max(sh["peak_after_stop"], mult)
                    elif mult <= stop_mult and not sh["hits"]:
                        sh["stop_hit"] = True
                        events.append((tok, sh["symbol"], "stop", mult, px, sh.get("url")))
                liq = (pair.get("liquidity") or {}).get("usd")
                if (sh.get("liq") and not sh.get("rug") and liq is not None
                        and liq < sh["liq"] * (1 - C.LIQUIDITY_DROP_EXIT_PCT / 100)):
                    sh["rug"] = True
                    events.append((tok, sh["symbol"], "rug", sh["last"] / sh["entry"], sh["last"], sh.get("url")))
            sh["miss"] = sh.get("miss", 0) + 1 if pair is None else 0
            if sh["miss"] >= 3 and not sh.get("rug"):
                sh["rug"] = True
                events.append((tok, sh["symbol"], "rug", sh["last"] / sh["entry"], sh["last"], sh.get("url")))
            if sh["miss"] >= 3 or (now - sh["opened"]) / 3600 >= C.SHADOW_HOURS:
                self.s["shadow_done"][tok] = round(sh["peak"] / sh["entry"], 2)
                if sh["stop_hit"] and sh["peak_after_stop"] >= 2:
                    self.s.setdefault("stop_then_2x", []).append(tok)
                del self.s["shadow"][tok]
        return events

    # --- missed movers: coins a filter rejected, followed 24 h to see if the filter cost us a winner ---
    MISSED_HOURS, MISSED_CAP, MISSED_MULT = 24, 600, 5

    def missed_add(self, rejected, now=None):
        now = now or time.time()
        m = self.s.setdefault("missed", {})
        for tok, r in rejected.items():
            if tok not in m and tok not in self.s["shadow"] and tok not in self.s["traded"]:
                m[tok] = {"symbol": r["symbol"], "entry": r["price"], "peak": r["price"],
                          "reason": r["reason"], "opened": now, "url": r.get("url")}
        for tok in sorted(m, key=lambda t: m[t]["opened"])[:max(0, len(m) - self.MISSED_CAP)]:
            del m[tok]

    def missed_update(self, pairs_by_token, now=None):
        """Returns coins that just reached MISSED_MULT x their price when we rejected them."""
        now = now or time.time()
        m = self.s.setdefault("missed", {})
        log = self.s.setdefault("missed_big", [])
        out = []
        for tok, r in list(m.items()):
            pair = pairs_by_token.get(tok)
            px = float((pair or {}).get("priceUsd") or 0)
            liq = ((pair or {}).get("liquidity") or {}).get("usd")
            # ignore quotes from near-empty pools and jumps no real coin makes within a day (bad price data)
            if px > 0 and (liq is None or liq >= 5000) and not bad_tick(px, r["entry"], r["peak"]):
                r["peak"] = max(r["peak"], px)
            mult = r["peak"] / r["entry"]
            if mult >= self.MISSED_MULT and not r.get("logged"):
                r["logged"] = True
                item = {"symbol": r["symbol"], "token": tok, "mult": round(mult, 1), "reason": r["reason"],
                        "seen": utc_day(r["opened"])}
                log.append(item)
                out.append(item)
            elif r.get("logged"):
                for item in log:
                    if item["token"] == tok:
                        item["mult"] = round(mult, 1)
            if (now - r["opened"]) / 3600 >= self.MISSED_HOURS:
                del m[tok]
        del log[:-100]
        return out

    def missed_stats(self):
        log = self.s.get("missed_big", [])
        reasons = {}
        for item in log:
            key = item["reason"].split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        return {"count": len(log), "by_filter": reasons,
                "best": sorted(log, key=lambda i: -i["mult"])[:3]}

    def shadow_stats(self):
        mults = list(self.s["shadow_done"].values()) + [
            sh["peak"] / sh["entry"] for sh in self.s["shadow"].values()]
        live_stop_then_2x = [t for t, sh in self.s["shadow"].items()
                             if sh.get("stop_hit") and sh.get("peak_after_stop", 0) >= 2]
        return {"signals": len(mults),
                "peaked_at_least": {f"{m}x": sum(1 for x in mults if x >= m) for m in MILESTONES},
                "best": f"{max(mults):.1f}x" if mults else None,
                # how often the stop loss sold something that later doubled: the price of having a stop
                "stopped_then_2x": len(self.s.get("stop_then_2x", [])) + len(live_stop_then_2x)}

    def bust_restart(self, now=None):
        """MikeSz's rule: if the paper account is broke (nothing open, not enough for a ticket),
        close the round and start over with START_BALANCE_USD. Shadow/missed tracking carries on.
        Returns the finished round's record, or None."""
        if self.variant == "all" or self.s["positions"] or self.equity() >= C.KILL_SWITCH_EQUITY_USD:
            return None
        rounds = self.s.setdefault("rounds", [])
        rec = {"round": len(rounds) + 1, "ended": datetime.fromtimestamp(now or time.time(), timezone.utc)
               .isoformat(timespec="seconds"), "left_usd": round(self.equity(), 2),
               "tickets": self.s.get("round_tickets", 0)}
        rounds.append(rec)
        self.log(symbol="-", token="-", action="RESTART", reason=f"round {rec['round']} bust, new ${C.START_BALANCE_USD:.0f}",
                 price="", usd=C.START_BALANCE_USD, fee=0, pnl_usd="")
        self.s.update(cash=C.START_BALANCE_USD, halted=False, withdraw_alerted=False, round_tickets=0)
        self.save()
        return rec

    def trades_since(self, ts):
        """Journal rows newer than ts (unix seconds), for the morning summary."""
        if not os.path.exists(self.journal_file):
            return []
        with open(self.journal_file) as f:
            return [r for r in csv.DictReader(f)
                    if datetime.fromisoformat(r["time"]).timestamp() >= ts]

    def withdraw_due(self):
        """True once, when paper equity first reaches the take-your-stake-out level."""
        if not self.s["withdraw_alerted"] and self.equity() >= C.WITHDRAW_AT_EQUITY_USD:
            self.s["withdraw_alerted"] = True
            self.save()
            return True
        return False

    # --- report --------------------------------------------------------------
    def summary(self):
        rows = []
        if os.path.exists(self.journal_file):
            with open(self.journal_file) as f:
                rows = list(csv.DictReader(f))
        buys = sum(1 for r in rows if r["action"] == "BUY")
        closed = [float(r["pnl_usd"]) for r in rows if r["pnl_usd"]]
        fees = sum(float(r["fee"] or 0) for r in rows)
        peaks = list(self.s["peaks"].values()) + [
            p["peak"] / p["entry"] for p in self.s["positions"].values()]
        return {
            "mode": C.MODE,
            "equity": round(self.equity(), 2),
            "start": C.START_BALANCE_USD,
            "tickets": buys,
            "realised_pnl": round(sum(closed), 2),
            "total_fees": round(fees, 2),
            "peaked_at_least": {f"{m}x": sum(1 for x in peaks if x >= m) for m in MILESTONES},
            "open": {t: f"{p['symbol']} {p['last'] / p['entry']:.2f}x" for t, p in self.s["positions"].items()},
            "halted": self.s["halted"],
            "shadow": self.shadow_stats(),
        }
