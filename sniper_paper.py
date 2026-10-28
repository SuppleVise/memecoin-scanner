"""Paper sniper for brand-new pump.fun coins. No wallet, no keys, no orders.

Listens to PumpPortal's free feed of every pump.fun launch, follows each coin's trades straight
from Solana (logsSubscribe on a public RPC; PumpPortal's own trade stream costs SOL), watches each
new coin for its first minute, and buys on paper only when the early order flow looks like
real buyers rather than the creator and their bundle wallets:

    python3 sniper_paper.py --label mac --quiet   # run (Ctrl+C or kill stops it, state is saved)
    python3 sniper_paper.py --label mac --summary-now   # push + print the summary of the book

Fills are simulated honestly: the buy fills at the first trade seen LATENCY_S after the
decision (that is how late a real bot would land), every side pays pump.fun's fee, the
trading API's fee and a priority fee. Prices come from the bonding curve in each event
(vSolInBondingCurve / vTokensInBondingCurve), so no DexScreener lag.

Exits: creator sells (instant), -45% stop, profit ladder (30% at 3x, 40% at 6x, 15% at 11x, 15% at 21x), 45% trail once the first
rung is hit, 10-minute time stop, or no trades for IDLE_EXIT_S (sold at the last price
minus a haircut, because a dead coin has nobody to sell to).
"""
import base64
import csv
import gzip
import json
import os
import signal
import socket
import ssl
import struct
import subprocess
import sys
import time

FEED_HOST, FEED_PATH = "pumpportal.fun", "/api/data"
FOLLOW_MIN_BUYERS, FOLLOW_S = 3, 600   # skipped coins with 3+ buyers are recorded for 10 min
DROP_FIELDS = ("signature", "bondingCurveKey", "pool")   # not needed for replays, saves disk (uri kept: metadata links test)

S = {
    # account (same shape as gamble: $50 rounds, $10 tickets, restart when bust)
    "START_USD": 50.0, "TICKET_USD": 10.0, "MAX_OPEN": 10,
    # what we watch: a coin's first WATCH_S seconds; entry decided between MIN_AGE_S and MAX_AGE_S
    "WATCH_S": 75, "MIN_AGE_S": 8, "MAX_AGE_S": 60,
    # entry filter on the early order flow (creator's own trades excluded)
    "MIN_BUYERS": 6,            # distinct wallets that bought
    "MIN_NET_SOL": 1.5,         # SOL bought minus SOL sold by others
    "MIN_BUY_SELL_RATIO": 2.0,  # buy trades per sell trade
    "MAX_TOP_BUYER_SHARE": 0.5, # one wallet bringing over half the inflow looks like a bundle
    "MAX_DEV_BUY_SOL": 3.0,     # creator buying a big bag at launch tends to dump it on you
    "MAX_ENTRY_MULT": 3.0,      # skip if price already 3x the launch price
    "MIN_ENTRY_MULT": 0.0,
    "MAX_FIRST_JUMP": 0.0,      # skip coins whose first trade already lifted price this much (bundled launch); 0 = off      # require the price to be at least this multiple of launch (0 = off)
    "COPYCAT_H": 1.0,           # same name/symbol bought in the last hour: skip
    "SERIAL_CREATOR_H": 1.0,    # creator launched another coin in the last hour: skip
    # execution model
    "LATENCY_S": 1.5, "SLIPPAGE_PCT": 2.0,
    "PUMP_FEE_PCT": 1.25, "API_FEE_PCT": 1.0, "PRIORITY_FEE_USD": 0.05,
    # exits (gamble's ladder in price multiples)
    "STOP_PCT": 45.0, "TRAIL_PCT": 45.0, "TIME_STOP_S": 600,
    # MikeSz 2026-10-27 ("2x 30%, 5x 40%" in profit terms): 30% at +200% (3x price), 40% at +500% (6x);
    # last 30% split at +1000% (11x) and +2000% (21x), so the data shows which of those is reachable
    "LADDER": [(3.0, 0.3), (6.0, 0.4), (11.0, 0.15), (21.0, 0.15)],
    # a coin still on pump.fun's curve can always be sold back to the curve, so no haircut beyond slippage
    "IDLE_EXIT_S": 120, "IDLE_HAIRCUT_PCT": 0.0,
}

JOURNAL_FIELDS = ["time", "symbol", "mint", "action", "reason", "price_sol", "usd", "fee",
                  "pnl_usd", "peak_mult", "cash"]


def arg(name, default=None):
    if name in sys.argv:
        i = sys.argv.index(name)
        return sys.argv[i + 1] if i + 1 < len(sys.argv) else default
    return default


LABEL = arg("--label", "")
SFX = f"sniper_{LABEL}" if LABEL else "sniper"
STATE_FILE, JOURNAL_FILE = f"paper_state_{SFX}.json", f"paper_journal_{SFX}.csv"
# Extra books fed by the same launches and trades, each with its own rules and $50 rounds.
# "momo" (2026-10-28 replay idea, 3 trades of evidence, so a forward test, not a finding):
# buy only coins already 2x from launch with 15+ buyers after 20 s.
VARIANTS = {"momo": {"MIN_AGE_S": 20, "MIN_ENTRY_MULT": 2.0, "MIN_BUYERS": 15, "MIN_NET_SOL": 0.0,
                     "MIN_BUY_SELL_RATIO": 1.0}}


def books():
    out = {"sniper": Sniper()}
    for name, params in VARIANTS.items():
        sfx = f"{SFX}_{name}"
        out[f"sniper {name}"] = Sniper(f"paper_state_{sfx}.json", f"paper_journal_{sfx}.csv", params=params)
    return out
QUIET = "--quiet" in sys.argv


def curve_price(ev):
    try:
        return float(ev["vSolInBondingCurve"]) / float(ev["vTokensInBondingCurve"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None


class Sniper:
    """All decisions live here and take `now` explicitly, so tests can replay a fake feed.
    on_event / tick return (subscribe, unsubscribe) mint lists for the socket loop to send."""

    def __init__(self, state_file=STATE_FILE, journal_file=JOURNAL_FILE, sol_usd=150.0, params=None):
        self.S = dict(S, **(params or {}))   # each book can run its own rules on the same feed
        self.state_file, self.journal_file, self.sol_usd = state_file, journal_file, sol_usd
        self.s = {"cash": self.S["START_USD"], "positions": {}, "rounds": 1, "banked": 0.0,
                  "seen": 0, "passed": 0, "dev_dumped_early": 0, "names": {}, "creators": {},
                  "reasons": {}, "started": time.time()}
        if os.path.exists(state_file):
            with open(state_file) as f:
                self.s.update(json.load(f))
        for p in self.s["positions"].values():
            p["last_t"] = time.time()   # don't call a coin dead just because the bot was off
        self.watch = {}     # mint -> early-flow stats while we decide (not saved: stale after a restart)
        self.pending = {}   # mint -> decision time; filled on the first trade after LATENCY_S
        self.follow = {}    # mint -> skipped coin kept 10 min only to record how it ended (data for tuning
                            # the filter; never traded). Dropped after WATCH_S if it has under 3 buyers.

    def fee(self, usd):
        return usd * (self.S["PUMP_FEE_PCT"] + self.S["API_FEE_PCT"]) / 100 + self.S["PRIORITY_FEE_USD"]

    def needs(self, mint):
        """Still interested in this coin's trades?"""
        return mint in self.watch or mint in self.pending or mint in self.s["positions"] or mint in self.follow

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
            w.writerow({k: row.get(k, "") for k in JOURNAL_FIELDS})

    def equity(self):
        return self.s["cash"] + sum(p["qty"] * p["last"] * self.sol_usd for p in self.s["positions"].values())

    def total_pnl(self):
        """Across all rounds: money banked from finished rounds plus this round, minus the stakes."""
        return self.s["banked"] + self.equity() - self.S["START_USD"] * self.s["rounds"]

    def _count(self, reason):
        self.s["reasons"][reason] = self.s["reasons"].get(reason, 0) + 1

    # --- feed ----------------------------------------------------------------
    def on_event(self, ev, now):
        if ev.get("txType") == "create":
            return self._on_create(ev, now)
        if ev.get("txType") in ("buy", "sell") and ev.get("mint"):
            return self._on_trade(ev, now)
        return [], []

    def _on_create(self, ev, now):
        mint, price = ev.get("mint"), curve_price(ev)
        if not mint or not price:
            return [], []
        self.s["seen"] += 1
        creator = ev.get("traderPublicKey", "")
        name = f"{ev.get('symbol', '')}|{ev.get('name', '')}".upper()
        serial = now - self.s["creators"].get(creator, -1e12) < self.S["SERIAL_CREATOR_H"] * 3600
        self.s["creators"][creator] = now
        if serial:
            self._count("serial creator")
            return [], []
        self.watch[mint] = {"t0": now, "creator": creator, "symbol": ev.get("symbol", "?"),
                            "name": name, "p0": price, "last": price,
                            "dev_buy": float(ev.get("solAmount") or 0), "dev_sold": False,
                            "buyers": {}, "buys": 0, "sells": 0, "sold_sol": 0.0}
        return [mint], []

    def _on_trade(self, ev, now):
        mint, price = ev["mint"], curve_price(ev)
        self.s["trades_seen"] = self.s.get("trades_seen", 0) + 1
        if not price:
            return [], []
        sub, unsub = [], []
        f = self.follow.get(mint)
        if f and ev["txType"] == "buy":
            f["buyers"].add(ev.get("traderPublicKey", ""))
        w = self.watch.get(mint)
        if w:
            w.setdefault("first", price)
            w["last"] = price
            sol, who = float(ev.get("solAmount") or 0), ev.get("traderPublicKey", "")
            if who == w["creator"]:
                if ev["txType"] == "sell":
                    w["dev_sold"] = True
            elif ev["txType"] == "buy":
                w["buys"] += 1
                w["buyers"][who] = w["buyers"].get(who, 0.0) + sol
            else:
                w["sells"] += 1
                w["sold_sol"] += sol
            if w["dev_sold"]:
                if now - w["t0"] <= self.S["WATCH_S"]:
                    self.s["dev_dumped_early"] += 1
                self._count("creator sold while watching")
                self._retire(mint, w)
                del self.watch[mint]
                if mint not in self.s["positions"] and mint not in self.pending:
                    unsub.append(mint)
            elif mint not in self.pending and mint not in self.s["positions"]:
                verdict = self._verdict(w, now)
                if verdict == "buy":
                    self.pending[mint] = now
                    self.s["passed"] += 1
                elif verdict:
                    self._count(verdict)
                    self._retire(mint, w)
                    del self.watch[mint]
                    unsub.append(mint)
        if mint in self.pending and now - self.pending[mint] >= self.S["LATENCY_S"]:
            self._fill_buy(mint, price, now)
        p = self.s["positions"].get(mint)
        if p:
            p["last"], p["last_t"] = price, now
            if ev.get("traderPublicKey") == p["creator"] and ev["txType"] == "sell":
                self._exit(mint, price, now, "creator sold")
            else:
                self._check_exits(mint, price, now)
        if mint not in self.watch and mint not in self.pending and mint not in self.s["positions"]:
            unsub.append(mint)   # done with this coin, whatever the reason
        return sub, self._keep(unsub)

    def _retire(self, mint, w):
        self.follow[mint] = {"t0": w["t0"], "buyers": set(w["buyers"])}

    def _keep(self, unsub):
        return [m for m in dict.fromkeys(unsub) if m not in self.follow]

    def _verdict(self, w, now):
        """'buy', a rejection reason, or None (keep watching)."""
        age = now - w["t0"]
        if age < self.S["MIN_AGE_S"]:
            return None
        if w["dev_buy"] > self.S["MAX_DEV_BUY_SOL"]:
            return "creator bought a big bag"
        if self.S["MAX_FIRST_JUMP"] and w.get("first", w["last"]) / w["p0"] > self.S["MAX_FIRST_JUMP"]:
            return "jump at launch (bundle)"
        if w["last"] / w["p0"] > self.S["MAX_ENTRY_MULT"]:
            return "already pumped before entry"
        if now - self.s["names"].get(w["name"], -1e12) < self.S["COPYCAT_H"] * 3600:
            return "copycat name"
        inflow = sum(w["buyers"].values())
        ok = (len(w["buyers"]) >= self.S["MIN_BUYERS"]
              and w["last"] / w["p0"] >= self.S["MIN_ENTRY_MULT"]
              and inflow - w["sold_sol"] >= self.S["MIN_NET_SOL"]
              and w["buys"] >= self.S["MIN_BUY_SELL_RATIO"] * max(1, w["sells"])
              and max(w["buyers"].values()) <= self.S["MAX_TOP_BUYER_SHARE"] * inflow)
        if ok:
            return "buy" if self._can_afford() else "no cash (round full)"
        return "too little real buying" if age >= self.S["MAX_AGE_S"] else None

    def _can_afford(self):
        return len(self.s["positions"]) < self.S["MAX_OPEN"] and self.s["cash"] >= self.S["TICKET_USD"]

    # --- paper orders ----------------------------------------------------------
    def _fill_buy(self, mint, price, now):
        del self.pending[mint]
        w = self.watch.pop(mint, None)
        if not w or not self._can_afford():
            return
        usd, fee = self.S["TICKET_USD"], self.fee(self.S["TICKET_USD"])
        fill = price * (1 + self.S["SLIPPAGE_PCT"] / 100)
        qty = (usd - fee) / (fill * self.sol_usd)
        self.s["cash"] -= usd
        self.s["names"][w["name"]] = now
        self.s["positions"][mint] = {"symbol": w["symbol"], "creator": w["creator"], "qty": qty,
                                     "qty0": qty, "entry": fill, "last": price, "peak": price,
                                     "t_in": now, "last_t": now, "rung": 0, "cost": usd}
        print(f"  [sniper] PAPER BUY {w['symbol']} $10 ({len(w['buyers'])} buyers in {now - w['t0']:.0f}s)  {mint}",
              flush=True)
        self.log(time=int(now), symbol=w["symbol"], mint=mint, action="BUY", reason="early flow",
                 price_sol=f"{fill:.3e}", usd=f"{usd:.2f}", fee=f"{fee:.2f}", cash=f"{self.s['cash']:.2f}")
        self.save()

    def _sell(self, mint, qty, price, now, reason):
        p = self.s["positions"][mint]
        fill = price * (1 - self.S["SLIPPAGE_PCT"] / 100)
        gross = qty * fill * self.sol_usd
        fee = min(self.fee(gross), gross)
        self.s["cash"] += gross - fee
        p["qty"] -= qty
        p["proceeds"] = p.get("proceeds", 0.0) + gross - fee
        self.log(time=int(now), symbol=p["symbol"], mint=mint, action="SELL", reason=reason,
                 price_sol=f"{fill:.3e}", usd=f"{gross:.2f}", fee=f"{fee:.2f}",
                 peak_mult=f"{p['peak'] / p['entry']:.2f}", cash=f"{self.s['cash']:.2f}")

    def _exit(self, mint, price, now, reason):
        p = self.s["positions"][mint]
        self._sell(mint, p["qty"], price, now, reason)
        pnl = p["proceeds"] - p["cost"]
        self.s.setdefault("closed", []).append({"symbol": p["symbol"], "pnl": round(pnl, 2),
                                               "peak": round(p["peak"] / p["entry"], 2), "reason": reason})
        self._count("exit: " + reason)
        print(f"  [sniper] SOLD {p['symbol']} {reason}: ${pnl:+.2f} (peak {p['peak'] / p['entry']:.1f}x)", flush=True)
        del self.s["positions"][mint]
        self._maybe_restart()
        self.save()

    def _check_exits(self, mint, price, now):
        """True when the position is fully closed."""
        p = self.s["positions"][mint]
        p["peak"] = max(p["peak"], price)
        mult = price / p["entry"]
        while p["rung"] < len(self.S["LADDER"]) and mult >= self.S["LADDER"][p["rung"]][0]:
            at, frac = self.S["LADDER"][p["rung"]]
            self._sell(mint, min(p["qty"], p["qty0"] * frac), price, now, f"ladder {at:g}x")
            p["rung"] += 1
            self.save()
        if mult <= 1 - self.S["STOP_PCT"] / 100:
            self._exit(mint, price, now, f"stop -{S['STOP_PCT']:g}%")
        elif p["rung"] and price <= p["peak"] * (1 - self.S["TRAIL_PCT"] / 100):
            self._exit(mint, price, now, "trailing stop")
        elif now - p["t_in"] >= self.S["TIME_STOP_S"]:
            self._exit(mint, price, now, "10-min time stop")
        else:
            return False
        return True

    def tick(self, now):
        """Housekeeping without a trade: drop stale watches, close idle or overdue positions."""
        unsub = []
        for mint, w in list(self.watch.items()):
            if now - w["t0"] > self.S["WATCH_S"] and mint not in self.pending:
                self._count("too little real buying")
                self._retire(mint, w)
                del self.watch[mint]
                unsub.append(mint)
        for mint, f in list(self.follow.items()):
            age = now - f["t0"]
            if age > FOLLOW_S or (age > self.S["WATCH_S"] and len(f["buyers"]) < FOLLOW_MIN_BUYERS):
                del self.follow[mint]
                unsub.append(mint)
        for mint, t in list(self.pending.items()):
            if now - t > 30:   # nobody traded after our decision: no fill
                del self.pending[mint]
                self.watch.pop(mint, None)
                unsub.append(mint)
        for mint, p in list(self.s["positions"].items()):
            if now - p["last_t"] >= self.S["IDLE_EXIT_S"]:
                self._exit(mint, p["last"] * (1 - self.S["IDLE_HAIRCUT_PCT"] / 100), now, "no trades (dead coin)")
                unsub.append(mint)
            elif now - p["t_in"] >= self.S["TIME_STOP_S"]:
                self._exit(mint, p["last"], now, "10-min time stop")
                unsub.append(mint)
        if now - self.s.get("pruned", 0) > 600:   # forget names/creators older than the rule windows
            for key, hours in (("names", self.S["COPYCAT_H"]), ("creators", self.S["SERIAL_CREATOR_H"])):
                self.s[key] = {k: t for k, t in self.s[key].items() if now - t < hours * 3600}
            self.s["pruned"] = now
        return [], self._keep(unsub)

    def close_all(self, now, reason="bot stopped"):
        """On stop, sell what's open at the last price, so the next run starts clean."""
        for mint, p in list(self.s["positions"].items()):
            self._exit(mint, p["last"], now, reason)

    def _maybe_restart(self):
        """Same as gamble: a round that can't afford a ticket and has nothing open starts over at $50."""
        if self.s["positions"] or self.s["cash"] >= self.S["TICKET_USD"]:
            return
        self.s["banked"] += self.s["cash"]
        self.s["cash"] = self.S["START_USD"]
        self.s["rounds"] += 1

    # --- report ----------------------------------------------------------------
    def summary(self, now=None):
        closed = self.s.get("closed", [])
        wins = [c for c in closed if c["pnl"] > 0]
        best = max(closed, key=lambda c: c["pnl"], default=None)
        hours = max(0.01, ((now or time.time()) - self.s["started"]) / 3600)
        top = sorted(self.s["reasons"].items(), key=lambda kv: -kv[1])[:6]
        lines = [
            f"Equity this round ${self.equity():.2f} (round {self.s['rounds']}, {len(self.s['positions'])} open)",
            f"Total P&L all rounds ${self.total_pnl():+.2f} on ${S['START_USD'] * self.s['rounds']:.0f} staked",
            f"Coins seen {self.s['seen']}, passed filter {self.s['passed']}, trades closed {len(closed)}, "
            f"wins {len(wins)}",
            f"Trades received {self.s.get('trades_seen', 0)} (0 = trade feed not arriving)\n"
            f"Creator dumped in first {S['WATCH_S']}s: {self.s['dev_dumped_early']} of {self.s['seen']}",
            f"Running {hours:.1f} h",
        ]
        if best:
            lines.append(f"Best: {best['symbol']} ${best['pnl']:+.2f} (peak {best['peak']}x)")
        lines.append("Why: " + ", ".join(f"{k} {v}" for k, v in top))
        return "\n".join(lines)


# --- network (stdlib only: no pip needed on the Mac) ---------------------------
def tls_context():
    ctx = ssl.create_default_context()
    if ctx.cert_store_stats().get("x509_ca"):
        return ctx
    # python.org Python on a Mac ships without CA certificates: borrow the system's
    pem = os.path.abspath("mac_roots.pem")
    if not os.path.exists(pem) and sys.platform == "darwin":
        out = subprocess.run(["security", "find-certificate", "-a", "-p",
                              "/System/Library/Keychains/SystemRootCertificates.keychain"],
                             capture_output=True, text=True)
        with open(pem, "w") as f:
            f.write(out.stdout)
    if os.path.exists(pem):
        ctx.load_verify_locations(pem)
    return ctx


class WS:
    """Minimal websocket client: text frames, ping/pong, close. Enough for a JSON feed.
    recv() never loses a half-received frame: it parses from its buffer and only consumes whole frames."""

    def __init__(self, host, path, timeout=1.0):
        raw = socket.create_connection((host, 443), timeout=15)
        self.sock = tls_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("feed closed during handshake")
            head += chunk
        status, _, self.buf = head.partition(b"\r\n\r\n")
        if b" 101 " not in status.split(b"\r\n")[0]:
            raise ConnectionError(status.split(b"\r\n")[0].decode(errors="replace"))
        self.frag = b""
        self.sock.settimeout(timeout)

    def send(self, obj, opcode=1):
        data = json.dumps(obj).encode() if opcode == 1 else obj
        mask = os.urandom(4)
        n = len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _frame(self):
        """(fin, opcode, payload) if a whole frame is buffered, else None."""
        buf = self.buf
        if len(buf) < 2:
            return None
        n, at = buf[1] & 0x7F, 2
        if n == 126:
            if len(buf) < 4:
                return None
            n, at = struct.unpack(">H", buf[2:4])[0], 4
        elif n == 127:
            if len(buf) < 10:
                return None
            n, at = struct.unpack(">Q", buf[2:10])[0], 10
        if len(buf) < at + n:
            return None
        self.buf = buf[at + n:]
        return buf[0] & 0x80, buf[0] & 0x0F, buf[at:at + n]

    def recv(self):
        """Next text message, or None if nothing complete arrived within the timeout."""
        while True:
            f = self._frame()
            if f is None:
                try:
                    chunk = self.sock.recv(65536)
                except socket.timeout:
                    return None
                if not chunk:
                    raise ConnectionError("feed closed")
                self.buf += chunk
                continue
            fin, op, payload = f
            if op == 9:
                self.send(payload, opcode=10)
            elif op == 8:
                raise ConnectionError("feed sent close")
            elif op in (0, 1, 2):
                self.frag += payload
                if fin:
                    msg, self.frag = self.frag, b""
                    return msg.decode(errors="replace")


# --- pump.fun trades straight from Solana (PumpPortal's trade stream is paid) -----------------------
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
TRADE_DISC = bytes([189, 219, 127, 211, 78, 230, 97, 238])   # anchor "event:TradeEvent", from pump's IDL
PUBLIC_RPC = "wss://api.mainnet-beta.solana.com/"
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(raw):
    n, out = int.from_bytes(raw, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def trades_from_logs(logs):
    """pump.fun TradeEvents in a transaction's logs, in PumpPortal's event shape.
    Layout (pump IDL): disc 8 | mint 32 | sol u64 | tokens u64 | is_buy u8 | user 32 | ts i64 |
    virtual_sol u64 | virtual_tokens u64 | ...more fields we don't need."""
    out = []
    for line in logs:
        if not line.startswith("Program data: "):
            continue
        try:
            raw = base64.b64decode(line[14:])
        except ValueError:
            continue
        if len(raw) < 113 or raw[:8] != TRADE_DISC:
            continue
        sol, tok = struct.unpack_from("<QQ", raw, 40)
        vsol, vtok = struct.unpack_from("<QQ", raw, 97)
        out.append({"txType": "buy" if raw[56] else "sell", "mint": b58(raw[8:40]),
                    "traderPublicKey": b58(raw[57:89]), "solAmount": sol / 1e9, "tokenAmount": tok / 1e6,
                    "vSolInBondingCurve": vsol / 1e9, "vTokensInBondingCurve": vtok / 1e6})
    return out


def rpc_url():
    """Public Solana RPC by default. If it throttles, put a Helius (free) websocket URL in rpc_url.txt."""
    url = PUBLIC_RPC
    if os.path.exists("rpc_url.txt"):
        with open("rpc_url.txt") as f:
            url = f.read().strip() or url
    rest = url.split("://", 1)[-1]
    host, _, path = rest.partition("/")
    return host, "/" + path


class TradeFeed:
    """One logsSubscribe per coin we care about, on a Solana RPC websocket."""

    def __init__(self):
        host, path = rpc_url()
        self.host = host
        self.ws = WS(host, path, timeout=0.1)
        self.next_id, self.asked, self.sub_of, self.mint_of, self.errors = 1, {}, {}, {}, 0
        self.notes = 0   # successful transactions seen; many notes but 0 trades = events not in the logs
        self.failed = self.truncated = 0   # failed txs; txs whose logs Solana cut short (trade may be missed)

    def _call(self, method, params):
        i, self.next_id = self.next_id, self.next_id + 1
        self.ws.send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        return i

    def subscribe(self, mints):
        waiting = set(self.asked.values())
        for m in mints:
            if m not in self.sub_of and m not in waiting:
                self.asked[self._call("logsSubscribe", [{"mentions": [m]}, {"commitment": "processed"}])] = m
                waiting.add(m)

    def unsubscribe(self, mints):
        for m in mints:
            sid = self.sub_of.pop(m, None)
            if sid is not None:
                self.mint_of.pop(sid, None)
                self._call("logsUnsubscribe", [sid])
            else:
                for i, mm in self.asked.items():
                    if mm == m:
                        self.asked[i] = None   # drop it as soon as the subscription is confirmed

    def poll(self):
        """None when nothing arrived; otherwise a (possibly empty) list of trade events."""
        raw = self.ws.recv()
        if raw is None:
            return None
        try:
            msg = json.loads(raw)
        except ValueError:
            return []
        if msg.get("id") in self.asked:
            m, sid = self.asked.pop(msg["id"]), msg.get("result")
            if "error" in msg:
                self.errors += 1
            elif m is None and sid is not None:
                self._call("logsUnsubscribe", [sid])
            elif sid is not None:
                self.sub_of[m], self.mint_of[sid] = sid, m
            return []
        if msg.get("method") == "logsNotification":
            v = msg.get("params", {}).get("result", {}).get("value", {})
            if v.get("err") is not None:
                self.failed += 1   # failed transactions (bots spam these) move no price
            else:
                self.notes += 1
                self.truncated += any("Log truncated" in line for line in v.get("logs") or [])
                return trades_from_logs(v.get("logs") or [])
        return []


def sol_usd(default=150.0):
    try:
        out = subprocess.run(["curl", "-sS", "-m", "10",
                              "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"],
                             capture_output=True, text=True)
        return float(json.loads(out.stdout)["solana"]["usd"])
    except Exception:
        return default


def ntfy_topic():
    if os.environ.get("NTFY_TOPIC"):
        return os.environ["NTFY_TOPIC"].strip()
    if os.path.exists("ntfy_topic.txt"):
        with open("ntfy_topic.txt") as f:
            return f.read().strip()
    return None


def push(title, text):
    topic = ntfy_topic()
    if topic:
        subprocess.run(["curl", "-sS", "-m", "10", "-H", f"Title: {title}", "-H", "Tags: dart",
                        "-d", text, f"https://ntfy.sh/{topic}"], capture_output=True)


def run():
    all_books = books()
    bot = all_books["sniper"]   # the main book: its numbers go in the heartbeat line
    price = sol_usd()
    for b in all_books.values():
        b.sol_usd = price
    stop = {"now": False}

    def on_signal(*_):
        stop["now"] = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    print(f"PAPER MODE [sniper] pump.fun launches, SOL ${bot.sol_usd:.0f}, book {STATE_FILE}", flush=True)
    last_sol, last_save, last_beat, backoff = time.time(), time.time(), time.time(), 5
    os.makedirs("feed", exist_ok=True)
    # raw feed recording, one gzip file per UTC day: lets the filter be tuned on replays, not guesses
    rec = gzip.open(time.strftime("feed/pump_%Y-%m-%d.jsonl.gz", time.gmtime()), "at")

    def release(feed, mints):   # a coin's trades are dropped only when no book wants them any more
        feed.unsubscribe([m for m in mints if not any(b.needs(m) for b in all_books.values())])

    def handle(ev, now, feed):
        sub, unsub = [], []
        for b in all_books.values():
            more_sub, more_unsub = b.on_event(ev, now)
            sub += more_sub
            unsub += more_unsub
        rec.write(json.dumps({"t": round(now, 2), **{k: v for k, v in ev.items() if k not in DROP_FIELDS}}) + "\n")
        feed.subscribe(list(dict.fromkeys(sub)))
        release(feed, list(dict.fromkeys(unsub)))

    while not stop["now"]:
        try:
            launches = WS(FEED_HOST, FEED_PATH, timeout=0.1)   # PumpPortal: new coins (free)
            launches.send({"method": "subscribeNewToken"})
            feed = TradeFeed()                                  # Solana RPC: their trades (free)
            print(f"[sniper] connected: launches from {FEED_HOST}, trades from {feed.host}", flush=True)
            # after a reconnect: coins we hold or watch need their trades again
            feed.subscribe(list(dict.fromkeys(m for b in all_books.values()
                                              for m in list(b.s["positions"]) + list(b.watch) + list(b.follow))))
            backoff = 5
            while not stop["now"]:
                raw = launches.recv()
                while raw is not None:
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        ev = None
                    if isinstance(ev, dict) and ev.get("txType") == "create":
                        handle(ev, time.time(), feed)
                    raw = launches.recv()
                for _ in range(500):   # drain the trade stream, but come back for launches
                    trades = feed.poll()
                    if trades is None:
                        break
                    for ev in trades:
                        handle(ev, time.time(), feed)
                now = time.time()
                release(feed, [m for b in all_books.values() for m in b.tick(now)[1]])
                if now - last_sol > 300:
                    price, last_sol = sol_usd(bot.sol_usd), now
                    for b in all_books.values():
                        b.sol_usd = price
                if now - last_save > 30:
                    for b in all_books.values():
                        b.save()
                    last_save = now
                if now - last_beat > 300:   # proof of life in the Terminal: is the feed really flowing?
                    print(f"{time.strftime('%H:%M:%S')} [sniper] launches seen {bot.s['seen']}, "
                          f"trades seen {bot.s.get('trades_seen', 0)} (from {feed.notes} ok txs, {feed.failed} failed, "
                          f"{feed.truncated} cut short), watching {len(bot.watch)}, "
                          f"bought {bot.s['passed']}, open {len(bot.s['positions'])}, "
                          f"round {bot.s['rounds']} cash ${bot.s['cash']:.2f}"
                          + "".join(f" | {n}: bought {b.s['passed']}, P&L ${b.total_pnl():+.2f}"
                                    for n, b in all_books.items() if b is not bot)
                          + (f", RPC errors {feed.errors}" if feed.errors else ""), flush=True)
                    last_beat = now
        except (OSError, ConnectionError, ssl.SSLError) as e:
            if stop["now"]:
                break
            print(f"[sniper] feed dropped ({e}), reconnecting in {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
    rec.close()
    for b in all_books.values():
        b.close_all(time.time())
        b.save()


def main():
    if "--summary-now" in sys.argv:
        for name, b in books().items():
            text = b.summary()
            print(f"[{name}]\n{text}")
            push(f"[{LABEL + ' ' if LABEL else ''}{name}] Summary", text)
        return
    run()


if __name__ == "__main__":
    main()
