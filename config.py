"""All tunable rules in one place. Change numbers here, not in the code."""

CHAIN = "solana"

# --- Universe: what is even allowed onto the watchlist -----------------------
MIN_PAIR_AGE_MIN = 30          # skip the first 30 min: that is where snipers and bundlers exit
MAX_PAIR_AGE_H = 72            # older than 3 days is not a "pump" play any more
MIN_LIQUIDITY_USD = 30_000     # below this, a $10 order moves price and rugs are cheap
MIN_MCAP_USD = 100_000
MAX_MCAP_USD = 10_000_000
MIN_VOLUME_24H_USD = 100_000
MIN_TXNS_1H = 200              # real activity, not three wallets ping-ponging

# --- Rug / honeypot filters (RugCheck full report) ---------------------------
MAX_RUGCHECK_SCORE_NORM = 40   # 0-100, higher is riskier
REJECT_ON_ANY_DANGER = True    # any RugCheck risk with level "danger" -> skip
REQUIRE_NO_MINT_AUTHORITY = True
REQUIRE_NO_FREEZE_AUTHORITY = True   # freeze authority = dev can block your sell (honeypot)
MIN_LP_LOCKED_PCT = 90
MAX_TOP10_HOLDERS_PCT = 30     # excluding pool accounts
MAX_SINGLE_HOLDER_PCT = 10

# --- Momentum entry signal ---------------------------------------------------
MIN_CHANGE_M5_PCT = 0.0        # still moving up right now
MIN_CHANGE_H1_PCT = 10.0
MAX_CHANGE_H1_PCT = 150.0      # above this you are the exit liquidity
MAX_CHANGE_H24_PCT = 1000.0
MIN_BUY_SELL_RATIO_H1 = 1.2    # buys / sells by count
MIN_VOL_H1_TO_LIQ = 0.5        # 1h volume vs pool depth: is there real flow
MIN_SCORE = 60                 # composite momentum score 0-100

# --- Money management (the part that actually keeps you alive) ---------------
START_BALANCE_USD = 50.0
POSITION_SIZE_USD = 10.0       # 20% of the starting stake per trade, never more
MAX_OPEN_POSITIONS = 2
MAX_TRADES_PER_DAY = 3
DAILY_LOSS_LIMIT_USD = 10.0    # hit it -> no new entries until tomorrow (UTC)
KILL_SWITCH_EQUITY_USD = 30.0  # equity below $30 (-40%) -> stop everything and review

# --- Exits -------------------------------------------------------------------
STOP_LOSS_PCT = 25.0           # hard stop from entry
TP1_FRACTION = 0.5             # momentum mode: sell half at +50% (see LADDER below)
TRAIL_PCT = 20.0               # after the first take-profit, trail the rest this far below the peak
TIME_STOP_H = 4.0              # nothing happened in 4h -> out
LIQUIDITY_DROP_EXIT_PCT = 40.0 # pool lost 40% of liquidity since entry -> rug in progress, out

# --- Cost model: FOMO app Solana fee schedule (third-party reported, 2026) ---
# under $5: flat $0.10 | $5-47.50: 2% | $47.50-190: flat $0.95 | $190+: 0.5%
SLIPPAGE_PCT = 2.0             # assumed per side on a $30k+ pool; real fills can be worse


def fomo_fee(notional_usd: float) -> float:
    if notional_usd < 5:
        return 0.10
    if notional_usd < 47.5:
        return notional_usd * 0.02
    if notional_usd < 190:
        return 0.95
    return notional_usd * 0.005


# --- Plumbing ----------------------------------------------------------------
POLL_SECONDS = 60
DEXSCREENER = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz/v1"
STATE_FILE = "paper_state.json"
JOURNAL_FILE = "paper_journal.csv"
SIGNALS_FILE = "signals.csv"


# --- Strategy modes ----------------------------------------------------------
# Exits are a ladder of (multiple of entry price, fraction of the ORIGINAL position to sell).
# After the first rung, the rest trails TRAIL_PCT below the peak.
LADDER = [(1.5, TP1_FRACTION)]
STOP_LOSS_PCT_ACTIVE = True
MOONBAG_NO_TRAIL = False       # moonshot/early: the final rung's slice ignores the trailing stop
DEAD_EXIT_MULT = None          # moonshot: salvage what's left if price falls to this multiple
TIME_STOP_MIN_MULT = 99.0      # time stop only fires while below this multiple
GECKO = "https://api.geckoterminal.com/api/v2"
USE_GECKO = True
# Every pair snapshot the scanner fetches is saved to data/ (one small gzip per run) so new
# strategies can be replayed on the exact same coins later, dead ones included.
RECORD_SNAPSHOTS = True
GECKO_PATHS = ["/trending_pools?duration=1h", "/trending_pools?duration=6h"]
USE_DEX_BOOSTS = True
# Every signal is also followed as a "shadow ticket" for this long, whether or not the
# paper account had cash for it. That measures the hit rate (how many reach 5x/10x/50x)
# much faster than $50 of tickets can.
SHADOW_HOURS = 72
WITHDRAW_AT_EQUITY_USD = 100.0  # MikeSz's rule: once the $50 has doubled, take the $50 out
DAILY_SUMMARY_UTC_HOUR = 8      # 9:00 Warsaw in winter, 10:00 in summer (MikeSz: morning summary)
# GitHub runs the 5-minute schedule only every ~30-45 min, so the summary is built by the first run in
# the hour before and handed to ntfy with a delivery time of exactly HH:00 (ntfy's scheduled delivery).
DAILY_SUMMARY_LEAD_MIN = 60

MODES = {
    # Short scalps on established movers. Small, frequent targets. Fees hurt most here.
    "momentum": {},
    # $10 lottery tickets aiming for 5x-50x. Earlier and smaller coins, because
    # 50x from a $10M cap means a $500M coin, which almost never happens.
    # Wide stop (-45%): a -25% stop gets hit by normal memecoin noise before any 10x.
    # Shadow tickets count how often the stop sells something that later doubles.
    "moonshot": {
        "MIN_PAIR_AGE_MIN": 60,
        "MAX_PAIR_AGE_H": 24,
        "MIN_LIQUIDITY_USD": 15_000,
        "MIN_MCAP_USD": 40_000,
        "MAX_MCAP_USD": 1_000_000,
        "MIN_VOLUME_24H_USD": 50_000,
        "MIN_TXNS_1H": 150,
        "MAX_TOP10_HOLDERS_PCT": 25,
        "MAX_SINGLE_HOLDER_PCT": 8,
        "MAX_RUGCHECK_SCORE_NORM": 35,
        "REJECT_ON_ANY_DANGER": True,
        "MIN_LP_LOCKED_PCT": 90,
        "MIN_CHANGE_M5_PCT": -3.0,
        "MIN_CHANGE_H1_PCT": 15.0,
        "MAX_CHANGE_H1_PCT": 300.0,
        "MAX_CHANGE_H24_PCT": 2000.0,
        "MIN_BUY_SELL_RATIO_H1": 1.3,
        "MIN_VOL_H1_TO_LIQ": 0.8,
        "MIN_SCORE": 65,
        "POSITION_SIZE_USD": 10.0,
        "MAX_OPEN_POSITIONS": 5,
        "MAX_TRADES_PER_DAY": 5,
        "DAILY_LOSS_LIMIT_USD": 20.0,
        "KILL_SWITCH_EQUITY_USD": 10.0,   # i.e. the whole $50 is the budget; stop when it's gone
        # MikeSz's rule (2026-10-25): close the whole ticket at -45% from the buy price.
        "STOP_LOSS_PCT_ACTIVE": True,
        "STOP_LOSS_PCT": 45.0,
        # MikeSz's ladder (2026-10-25), in price multiples: 2x (+100%) sell half = stake back,
        # 3x (+200%) 20%, 6x (+500%) 20%, last 10% only at 31x (+3000%). Trail 45% until the last 10%.
        "LADDER": [(2.0, 0.5), (3.0, 0.2), (6.0, 0.2), (31.0, 0.1)],
        "MOONBAG_NO_TRAIL": True,         # last 10% has no trailing stop: 31x, rug or dead coin only
        "TRAIL_PCT": 45.0,
        "DEAD_EXIT_MULT": 0.15,           # down 85%: sell the scraps
        "TIME_STOP_H": 72.0,
        "TIME_STOP_MIN_MULT": 1.0,        # after 3 days still under entry: out
        "LIQUIDITY_DROP_EXIT_PCT": 50.0,
    },
    # Fresh launches, 10 min to 2 h old: the biggest multiples and the most rugs.
    # Same money rules and exits as moonshot, stricter holder filters, organic sources only.
    "early": {
        "MIN_PAIR_AGE_MIN": 10,
        "MAX_PAIR_AGE_H": 2,
        "MIN_LIQUIDITY_USD": 10_000,
        "MIN_MCAP_USD": 25_000,
        "MAX_MCAP_USD": 400_000,
        "MIN_VOLUME_24H_USD": 20_000,
        "MIN_TXNS_1H": 100,
        "MAX_TOP10_HOLDERS_PCT": 20,
        "MAX_SINGLE_HOLDER_PCT": 6,
        "MAX_RUGCHECK_SCORE_NORM": 30,
        "REJECT_ON_ANY_DANGER": True,
        "MIN_LP_LOCKED_PCT": 90,
        "MIN_CHANGE_M5_PCT": -5.0,
        "MIN_CHANGE_H1_PCT": 20.0,
        "MAX_CHANGE_H1_PCT": 500.0,
        "MAX_CHANGE_H24_PCT": 5000.0,
        "MIN_BUY_SELL_RATIO_H1": 1.3,
        "MIN_VOL_H1_TO_LIQ": 1.0,
        "MIN_SCORE": 65,
        "POSITION_SIZE_USD": 10.0,
        "MAX_OPEN_POSITIONS": 5,
        "MAX_TRADES_PER_DAY": 5,
        "DAILY_LOSS_LIMIT_USD": 20.0,
        "KILL_SWITCH_EQUITY_USD": 10.0,
        "STOP_LOSS_PCT_ACTIVE": True,
        "STOP_LOSS_PCT": 45.0,
        "LADDER": [(2.0, 0.5), (3.0, 0.2), (6.0, 0.2), (31.0, 0.1)],
        "MOONBAG_NO_TRAIL": True,         # last 10% has no trailing stop: 31x, rug or dead coin only
        "TRAIL_PCT": 45.0,
        "DEAD_EXIT_MULT": 0.15,
        "TIME_STOP_H": 48.0,
        "TIME_STOP_MIN_MULT": 1.0,
        "LIQUIDITY_DROP_EXIT_PCT": 50.0,
        "GECKO_PATHS": ["/new_pools", "/new_pools?page=2", "/trending_pools?duration=5m"],
        "USE_DEX_BOOSTS": False,   # paid promotion is noise at this age
    },
    # MikeSz's "turbo" (2026-10-26): high risk, high reward. Coins 5-10 minutes old from $20k mcap,
    # $10 tickets, 10 a day. LP-lock rule and the blanket "any danger" reject are off (brand-new
    # pump.fun coins have no locked LP); mint/freeze authority and holder limits still apply.
    "turbo": {
        "MIN_PAIR_AGE_MIN": 5,
        "MAX_PAIR_AGE_H": 10 / 60,
        "MIN_LIQUIDITY_USD": 5_000,
        "MIN_MCAP_USD": 20_000,
        "MAX_MCAP_USD": 150_000,          # "buy low"
        "MIN_VOLUME_24H_USD": 5_000,
        "MIN_TXNS_1H": 30,
        "MAX_TOP10_HOLDERS_PCT": 30,
        "MAX_SINGLE_HOLDER_PCT": 10,
        "MAX_RUGCHECK_SCORE_NORM": 60,
        "REJECT_ON_ANY_DANGER": False,
        "MIN_LP_LOCKED_PCT": 0,
        "MIN_CHANGE_M5_PCT": -10.0,
        "MIN_CHANGE_H1_PCT": 0.0,         # at 5-10 min old, "1h change" is the move since launch
        "MAX_CHANGE_H1_PCT": 2000.0,
        "MAX_CHANGE_H24_PCT": 100000.0,
        "MIN_BUY_SELL_RATIO_H1": 1.2,
        "MIN_VOL_H1_TO_LIQ": 0.5,
        "MIN_SCORE": 50,
        "POSITION_SIZE_USD": 10.0,
        "MAX_OPEN_POSITIONS": 10,
        "MAX_TRADES_PER_DAY": 20,
        "DAILY_LOSS_LIMIT_USD": 50.0,     # no extra daily brake: the $50 is the whole budget
        "KILL_SWITCH_EQUITY_USD": 10.0,
        "STOP_LOSS_PCT_ACTIVE": True,
        "STOP_LOSS_PCT": 45.0,
        "LADDER": [(3.0, 0.5), (6.0, 0.2), (11.0, 0.2), (31.0, 0.1)],  # profit +200/+500/+1000/+3000%
        "MOONBAG_NO_TRAIL": True,
        "TRAIL_PCT": 45.0,
        "DEAD_EXIT_MULT": 0.15,
        "TIME_STOP_H": 24.0,
        "TIME_STOP_MIN_MULT": 1.0,
        "LIQUIDITY_DROP_EXIT_PCT": 50.0,
        "GECKO_PATHS": ["/new_pools", "/new_pools?page=2", "/new_pools?page=3"],
        "USE_DEX_BOOSTS": False,
    },
    # MikeSz's "gamble" (2026-10-26): coins 1-2 minutes old from $1k mcap, $10 tickets, 20 a day,
    # out within 10 minutes. Ladder in profit terms: 40% at +200%, 20% at +500%, 10% at +1000%,
    # 10% at +3000%, 10% at +5000%; the last 10% and anything unsold goes at the 10-minute mark.
    # Mac only: GitHub checks every 40+ minutes, so it would never see a 2-minute-old coin.
    "gamble": {
        "MIN_PAIR_AGE_MIN": 1,
        "MAX_PAIR_AGE_H": 3 / 60,         # 1-2 min plus up to one scan of lag
        "MIN_LIQUIDITY_USD": 1_000,
        "MIN_MCAP_USD": 1_000,
        "MAX_MCAP_USD": 150_000,
        "MIN_VOLUME_24H_USD": 500,
        "MIN_TXNS_1H": 10,
        "MAX_TOP10_HOLDERS_PCT": 50,      # at 2 minutes old a few early buyers always hold a lot
        "MAX_SINGLE_HOLDER_PCT": 15,
        "MAX_RUGCHECK_SCORE_NORM": 80,
        "REJECT_ON_ANY_DANGER": False,    # mint/freeze authority are still rejected
        "MIN_LP_LOCKED_PCT": 0,
        "MIN_CHANGE_M5_PCT": 0.0,         # still going up, not already dumping
        "MIN_CHANGE_H1_PCT": -100.0,
        "MAX_CHANGE_H1_PCT": 100000.0,
        "MAX_CHANGE_H24_PCT": 100000.0,
        "MIN_BUY_SELL_RATIO_H1": 1.0,
        "MIN_VOL_H1_TO_LIQ": 0.0,
        "MIN_SCORE": 30,
        "POSITION_SIZE_USD": 10.0,
        "MAX_OPEN_POSITIONS": 10,
        "MAX_TRADES_PER_DAY": 20,
        "DAILY_LOSS_LIMIT_USD": 50.0,
        "KILL_SWITCH_EQUITY_USD": 10.0,
        "STOP_LOSS_PCT_ACTIVE": True,
        "STOP_LOSS_PCT": 45.0,
        "LADDER": [(3.0, 0.4), (6.0, 0.2), (11.0, 0.1), (31.0, 0.1), (51.0, 0.1)],
        "MOONBAG_NO_TRAIL": True,
        "TRAIL_PCT": 45.0,
        "DEAD_EXIT_MULT": 0.15,
        "TIME_STOP_H": 10 / 60,
        "TIME_STOP_MIN_MULT": float("inf"),   # at 10 minutes sell whatever is left, win or lose
        "LIQUIDITY_DROP_EXIT_PCT": 50.0,
        "GECKO_PATHS": ["/new_pools"],
        "USE_DEX_BOOSTS": False,
        "POLL_SECONDS": 30,               # a 10-minute trade needs more than ten looks
    },
}
MODE = "moonshot"
_BASE = {k: globals()[k] for m in MODES.values() for k in m if k in globals()}


def apply_mode(name, label=""):
    """label (e.g. "mac") gives a second, separate paper book for the same mode."""
    global MODE, STATE_FILE, JOURNAL_FILE, SIGNALS_FILE
    if name not in MODES:
        raise SystemExit(f"unknown mode {name}, choose from {list(MODES)}")
    g = globals()
    # start from the top-of-file values so one mode's settings never leak into the next
    for k, v in _BASE.items():
        g[k] = v
    for k, v in MODES[name].items():
        g[k] = v
    MODE = name
    sfx = f"{name}_{label}" if label else name
    STATE_FILE = f"paper_state_{sfx}.json"
    JOURNAL_FILE = f"paper_journal_{sfx}.csv"
    SIGNALS_FILE = f"signals_{sfx}.csv"
