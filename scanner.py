"""Find Solana memecoins with momentum that pass rug filters.

Data: DexScreener public API (no key) for discovery and pair stats,
RugCheck public API (no key) for contract / holder risk.
This module only reads public data. It never touches a wallet.
"""
import csv
import gzip
import json
import os
import subprocess
import time

import config as C

UA = {"User-Agent": "mmfx-memecoin-scanner/0.1", "Accept": "application/json"}


GECKO_CACHE_S = 55  # several modes running side by side share GeckoTerminal answers (~10 calls/min free)


def _cache_path(url):
    import hashlib
    import tempfile
    return os.path.join(tempfile.gettempdir(), "mmfx_" + hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def get_json(url, retries=3):
    """Fetch through the system curl: on macOS it has working certificates even
    when Python's urllib does not. GeckoTerminal answers are cached on disk for GECKO_CACHE_S."""
    host = url.split("/")[2]
    cache = _cache_path(url) if "geckoterminal" in host else None
    if cache and os.path.exists(cache) and time.time() - os.path.getmtime(cache) < GECKO_CACHE_S:
        try:
            with open(cache) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    data = _fetch(url, host, retries)
    if cache and data is not None:
        tmp = cache + f".{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, cache)
    return data


def _fetch(url, host, retries):
    for attempt in range(retries):
        try:
            out = subprocess.run(
                ["curl", "-sS", "-m", "20", "-A", UA["User-Agent"], "-H", "Accept: application/json",
                 "-w", "\n%{http_code}", url],
                capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            print(f"  timeout ({host})")
            continue
        body, _, code = out.stdout.rpartition("\n")
        if out.returncode != 0:
            print(f"  network error ({host}): {out.stderr.strip()}")
            time.sleep(2 * (attempt + 1))
            continue
        if code == "429":
            print(f"  rate limited by {host}, waiting")
            time.sleep(5 * (attempt + 1))
            continue
        if code == "404":
            return None
        if code != "200":
            print(f"  HTTP {code} from {host}")
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            print(f"  bad JSON from {host}")
            return None
    return None


# --- Discovery ---------------------------------------------------------------

def discover_candidates():
    """Token addresses that are currently getting attention on DexScreener.

    Boosts and profiles are paid placements, so they are a list of *who is
    marketing right now*, not a quality signal. The filters below do the work.
    """
    seen = []
    for path in (("/token-boosts/latest/v1", "/token-boosts/top/v1", "/token-profiles/latest/v1")
                 if C.USE_DEX_BOOSTS else ()):
        data = get_json(C.DEXSCREENER + path) or []
        for item in data:
            if item.get("chainId") == C.CHAIN:
                addr = item.get("tokenAddress")
                if addr and addr not in seen:
                    seen.append(addr)
    if C.USE_GECKO:
        # Organic flow: trending and freshly created pools (per mode, see config.GECKO_PATHS).
        # Nobody pays to be on these lists. Free tier is ~10 calls/min.
        for path in C.GECKO_PATHS:
            for addr in gecko_base_tokens(get_json(f"{C.GECKO}/networks/{C.CHAIN}{path}")):
                if addr not in seen:
                    seen.append(addr)
    return seen


def gecko_base_tokens(data):
    """Pull base-token mint addresses out of a GeckoTerminal pools response."""
    out = []
    for pool in (data or {}).get("data") or []:
        tid = (((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        mint = tid.split("_", 1)[1] if "_" in tid else ""
        if mint and mint not in WRAPPED and mint not in out:
            out.append(mint)
    return out


# SOL, USDC, USDT: GeckoTerminal sometimes lists them as the base token of a pool.
WRAPPED = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


SNAP_FIELDS = ["t", "mode", "token", "symbol", "pair", "dex", "created_ms", "price", "mcap", "liq",
               "vol_m5", "vol_h1", "vol_h24", "buys_m5", "sells_m5", "buys_h1", "sells_h1",
               "chg_m5", "chg_h1", "chg_h6", "chg_h24"]


def record(pairs, now=None):
    """Append one row per pair to data/YYYY-MM-DD/HHMMSS_<mode>.csv.gz (a new file per run keeps
    git from re-storing a growing file on every commit)."""
    if not C.RECORD_SNAPSHOTS or not pairs:
        return
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    path = os.path.join("data", day, time.strftime("%H%M%S", time.gmtime(_RUN_START)) + f"_{C.MODE}.csv.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new = not os.path.exists(path)
    with gzip.open(path, "at", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(SNAP_FIELDS)
        for addr, p in pairs.items():
            t5 = (p.get("txns") or {}).get("m5") or {}
            t1 = (p.get("txns") or {}).get("h1") or {}
            v, ch = p.get("volume") or {}, p.get("priceChange") or {}
            w.writerow([int(now), C.MODE, addr, p.get("baseToken", {}).get("symbol"), p.get("pairAddress"),
                        p.get("dexId"), p.get("pairCreatedAt"), p.get("priceUsd"),
                        p.get("marketCap") or p.get("fdv"), (p.get("liquidity") or {}).get("usd"),
                        v.get("m5"), v.get("h1"), v.get("h24"), t5.get("buys"), t5.get("sells"),
                        t1.get("buys"), t1.get("sells"), ch.get("m5"), ch.get("h1"), ch.get("h6"), ch.get("h24")])


_RUN_START = time.time()


def fetch_pairs(token_addresses):
    """Best (most liquid) pair per token. DexScreener takes up to 30 per call."""
    best = {}
    for i in range(0, len(token_addresses), 30):
        chunk = ",".join(token_addresses[i:i + 30])
        pairs = get_json(f"{C.DEXSCREENER}/tokens/v1/{C.CHAIN}/{chunk}") or []
        for p in pairs:
            addr = p.get("baseToken", {}).get("address")
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if addr and liq > ((best.get(addr) or {}).get("liquidity") or {}).get("usd", -1):
                best[addr] = p
    record(best)
    return best


def fetch_pair(pair_address):
    data = get_json(f"{C.DEXSCREENER}/latest/dex/pairs/{C.CHAIN}/{pair_address}") or {}
    pairs = data.get("pairs") or ([data["pair"]] if data.get("pair") else [])
    return pairs[0] if pairs else None


def fetch_rugcheck(mint):
    return get_json(f"{C.RUGCHECK}/tokens/{mint}/report")


# --- Filters (pure functions: pair dict / report dict in, reasons out) -------

def universe_reasons(pair, now_ms=None):
    """Empty list means the pair is in the tradable universe."""
    now_ms = now_ms or time.time() * 1000
    r = []
    created = pair.get("pairCreatedAt")
    if not created:
        r.append("no creation time")
    else:
        age_min = (now_ms - created) / 60_000
        if age_min < C.MIN_PAIR_AGE_MIN:
            r.append(f"too new ({age_min:.0f}m)")
        if age_min > C.MAX_PAIR_AGE_H * 60:
            r.append(f"too old ({age_min / 60:.0f}h)")
    liq = (pair.get("liquidity") or {}).get("usd") or 0
    if liq < C.MIN_LIQUIDITY_USD:
        r.append(f"liquidity ${liq:,.0f}")
    mcap = pair.get("marketCap") or pair.get("fdv") or 0
    if not C.MIN_MCAP_USD <= mcap <= C.MAX_MCAP_USD:
        r.append(f"mcap ${mcap:,.0f}")
    vol24 = (pair.get("volume") or {}).get("h24") or 0
    if vol24 < C.MIN_VOLUME_24H_USD:
        r.append(f"24h vol ${vol24:,.0f}")
    t1 = (pair.get("txns") or {}).get("h1") or {}
    if (t1.get("buys", 0) + t1.get("sells", 0)) < C.MIN_TXNS_1H:
        r.append("thin 1h txns")
    return r


def _pool_accounts(report):
    """Every account string that appears under markets[], so pool vaults are not counted as whales."""
    out = set()

    def walk(x):
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and len(x) >= 32:
            out.add(x)

    walk(report.get("markets") or [])
    return out


def rug_reasons(report):
    """Empty list means RugCheck found nothing disqualifying. No report = reject."""
    if not report:
        return ["no rugcheck report"]
    r = []
    if report.get("rugged"):
        r.append("flagged rugged")
    score = report.get("score_normalised")
    if score is None or score > C.MAX_RUGCHECK_SCORE_NORM:
        r.append(f"rugcheck score {score}")
    risks = report.get("risks") or []
    if C.REJECT_ON_ANY_DANGER:
        for risk in risks:
            if risk.get("level") == "danger":
                r.append(f"danger: {risk.get('name')}")
    token = report.get("token") or {}
    if C.REQUIRE_NO_MINT_AUTHORITY and token.get("mintAuthority"):
        r.append("mint authority live (dev can print supply)")
    if C.REQUIRE_NO_FREEZE_AUTHORITY and token.get("freezeAuthority"):
        r.append("freeze authority live (possible honeypot)")
    lp = report.get("lpLockedPct")
    if lp is None:
        markets = report.get("markets") or []
        lp = max(((m.get("lp") or {}).get("lpLockedPct") or 0 for m in markets), default=0)
    if lp < C.MIN_LP_LOCKED_PCT:
        r.append(f"LP locked {lp:.0f}%")
    insiders = report.get("graphInsidersDetected") or 0
    if insiders > C.MAX_GRAPH_INSIDERS:
        r.append(f"insider network: {insiders} linked wallets")
    pools = _pool_accounts(report)
    holders = [h for h in (report.get("topHolders") or [])
               if h.get("address") not in pools and h.get("owner") not in pools]
    top10 = sum(h.get("pct", 0) for h in holders[:10])
    if top10 > C.MAX_TOP10_HOLDERS_PCT:
        r.append(f"top10 hold {top10:.0f}%")
    if holders and holders[0].get("pct", 0) > C.MAX_SINGLE_HOLDER_PCT:
        r.append(f"one wallet holds {holders[0]['pct']:.0f}%")
    return r


def momentum(pair):
    """(score 0-100, reasons-for-rejection). Score only matters if reasons is empty."""
    pc = pair.get("priceChange") or {}
    m5, h1, h24 = pc.get("m5", 0) or 0, pc.get("h1", 0) or 0, pc.get("h24", 0) or 0
    t1 = (pair.get("txns") or {}).get("h1") or {}
    buys, sells = t1.get("buys", 0), t1.get("sells", 0)
    ratio = buys / max(sells, 1)
    liq = (pair.get("liquidity") or {}).get("usd") or 1
    vol_h1 = (pair.get("volume") or {}).get("h1") or 0
    turnover = vol_h1 / liq

    r = []
    if m5 < C.MIN_CHANGE_M5_PCT:
        r.append(f"5m {m5:+.1f}%")
    if h1 < C.MIN_CHANGE_H1_PCT:
        r.append(f"1h {h1:+.1f}% too weak")
    if h1 > C.MAX_CHANGE_H1_PCT:
        r.append(f"1h {h1:+.0f}% too extended")
    if h24 > C.MAX_CHANGE_H24_PCT:
        r.append(f"24h {h24:+.0f}% too extended")
    if ratio < C.MIN_BUY_SELL_RATIO_H1:
        r.append(f"buy/sell {ratio:.2f}")
    if turnover < C.MIN_VOL_H1_TO_LIQ:
        r.append(f"1h turnover {turnover:.2f}x")

    # Composite: each component capped, sweet spot for h1 is +20..+80%.
    s_h1 = 30 * min(max(h1, 0) / 40, 1) - (10 if h1 > 80 else 0)
    s_ratio = 25 * min(max(ratio - 1, 0) / 1.0, 1)
    s_turn = 25 * min(turnover / 2.0, 1)
    s_m5 = 20 * min(max(m5, 0) / 5, 1)
    score = max(0, min(100, s_h1 + s_ratio + s_turn + s_m5))
    if score < C.MIN_SCORE:
        r.append(f"score {score:.0f}")
    return round(score, 1), r


def _reject(rejected, addr, p, sym, reason):
    """Remember a rejected coin and its price now, to check later whether the filter cost us a winner."""
    price = float(p.get("priceUsd") or 0)
    liq = (p.get("liquidity") or {}).get("usd")
    # a pool with almost no liquidity quotes a meaningless price, and "since we rejected it" becomes 10,000x
    if rejected is not None and price > 0 and (liq is None or liq >= 1000):
        rejected[addr] = {"symbol": sym, "price": price, "reason": reason[:80], "url": p.get("url")}


def scan(log=print, skip=(), rejected=None):
    """Return list of signal dicts that pass every gate, best first.
    Tokens in `skip` (already signalled) are not re-checked, which saves RugCheck calls.
    If `rejected` is a dict, coins that failed a filter are added to it (see _reject); coins that are
    simply too old or too big are left out, since they are not what this scanner is hunting."""
    log("fetching candidates from DexScreener" + (" + GeckoTerminal..." if C.USE_GECKO else "..."))
    addrs = discover_candidates()
    log(f"  {len(addrs)} candidates, fetching pair stats...")
    pairs = fetch_pairs(addrs)
    signals = []
    out_of_universe = 0
    for addr, p in pairs.items():
        sym = p.get("baseToken", {}).get("symbol", "?")
        if addr in skip:
            continue
        bad = universe_reasons(p)
        if bad:
            out_of_universe += 1
            if not any(b.startswith("too old") or (b.startswith("mcap") and
                       (p.get("marketCap") or p.get("fdv") or 0) > C.MAX_MCAP_USD) for b in bad):
                _reject(rejected, addr, p, sym, "size/age: " + ", ".join(bad))
            continue
        score, bad = momentum(p)
        if bad:
            log(f"  - {sym}: momentum {', '.join(bad)}")
            _reject(rejected, addr, p, sym, "momentum: " + ", ".join(bad))
            continue
        log(f"  ? {sym}: momentum OK (score {score}), checking RugCheck...")
        report = fetch_rugcheck(addr)  # only for survivors, to stay under rate limits
        bad = rug_reasons(report)
        if bad:
            log(f"  x {sym}: RUG FILTER {', '.join(bad)}")
            _reject(rejected, addr, p, sym, "rug filter: " + ", ".join(bad))
            continue
        signals.append({
            "token": addr,
            "symbol": sym,
            "pair": p.get("pairAddress"),
            "url": p.get("url"),
            "price": float(p.get("priceUsd") or 0),
            "liquidity": (p.get("liquidity") or {}).get("usd") or 0,
            "mcap": p.get("marketCap") or p.get("fdv") or 0,
            "score": score,
            "h1": (p.get("priceChange") or {}).get("h1"),
        })
        time.sleep(1)
    log(f"scanned {len(addrs)} tokens, {len(pairs)} pairs, {out_of_universe} outside size/age limits, "
        f"{len(signals)} signals [{C.MODE}]")
    return sorted(signals, key=lambda s: -s["score"])
