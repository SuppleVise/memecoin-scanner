# Memecoin scanner (paper mode)

A Solana memecoin scanner with rug filters and a simulated $50 account. It finds tokens that are moving, rejects the ones that look like rugs or honeypots, and paper-trades them with hard loss limits. It never connects to a wallet and never sends an order.

## Read this first

- **The odds.** Research on pump.fun found about 97% of its tokens lost more than half their value and about 81% are dead ([cryptonews](https://cryptonews.com/reports/research-97-of-pump-fun-meme-coins-lost-more-than-half-value-and-81-are-dead/)). Most pumps you can see on a screener are insiders, bundlers and bots selling to the people who arrive late. A scanner reading public data is, by construction, late.
- **Fees on $50 are brutal.** FOMO reportedly charges 2% on Solana trades between $5 and $47.50 ([PolyMart review](https://polymart.app/blog/fomo-app)). Add about 2% slippage each way and a $10 trade has to gain about **+8.5% just to break even**. The tests check this: a flat round trip loses about $0.80 of $10.
- **FOMO has no trading API.** It is a mobile app with a Privy embedded wallet ([madeonsol](https://madeonsol.com/tools/fomo), [Bitquery](https://docs.bitquery.io/docs/blockchain/Solana/fomo-api/)). The bot cannot trade on FOMO. The workflow is: the scanner flags a token, you decide, you tap in FOMO by hand. Fully automated trading would mean a separate wallet and a Jupiter swap bot, which is out of scope until the paper results earn it.
- **Do not run random "sniper bot" repos with your keys.** A lot of open-source Solana bots are wallet drainers. None of the repos listed below have been audited by me.

## Moonshot mode (default): $10 tickets aiming for 5x–50x

**The honest maths.** A 5x–50x target means most tickets go to zero or close to it. The plan only works if the few winners are big enough to pay for all the losers. With $10 tickets: one 20x pays for about 19 dead tickets. Five tickets ($50) is almost certainly too few to catch one. The paper run is there to measure the real hit rate before any money goes in.

**Why it targets small coins.** 50x from a $10M market cap means a $500M coin, which almost never happens. 50x from $200k means $10M, which happens rarely but regularly. Smaller and earlier means more rugs, so the rug filters are stricter here.

| | Moonshot | Momentum |
|---|---|---|
| Coin age | 1 h to 24 h | 30 min to 72 h |
| Market cap | $40k to $1M | $100k to $10M |
| Liquidity | ≥ $15k | ≥ $30k |
| 1 h move | +15% to +300% | +10% to +150% |
| Buy/sell ratio, 1 h | ≥ 1.3 | ≥ 1.2 |
| Top 10 holders | ≤ 25% | ≤ 30% |
| Ticket size | $10 | $10 |
| Open at once | 5 | 2 |
| New tickets per day | 5 | 3 |
| Stop loss | −45% from the buy price (your rule) | −25% |
| Take profits | ½ at +100% (2x price, stake back), 20% at +200% (3x), 20% at +500% (6x), last 10% only at +3000% (31x) | ½ at 1.5x |
| Trailing stop on the rest | 45% below peak | 20% below peak |
| Time stop | 72 h if still below entry | 4 h |
| Rug alarm | liquidity −50% or pair vanishes: out at once | same, −40% |
| Stop everything | paper equity below $10 | below $30 |

The ladder means: once a coin hits 2x you have your $10 back and the rest is a free ride. A 50x needs no extra decision; the last 15% simply rides until the 45% trail hits.

### Early mode (runs alongside moonshot)

Same money rules and exits as moonshot, for coins **10 min to 2 h old**, market cap $25k–$400k, from GeckoTerminal's new pools only (no paid promotion lists). Stricter holder filters: top 10 ≤ 20%, one wallet ≤ 6%, RugCheck score ≤ 30. This is where the biggest multiples and the most rugs are. Its results are kept in a separate paper book (`--mode early`) so you can compare it with moonshot.

### Shadow tickets

Every signal is followed for 72 h as a "shadow ticket", even if the paper account had no cash or had hit its daily limit. That measures the hit rate (how many signals ever reach 2x/5x/10x/50x) much faster than $50 of tickets can. The daily summary also counts how often a coin hit the −45% stop and later doubled anyway, so we can see what the stop costs. A shadow ticket whose coin disappears from DexScreener for 3 checks in a row is counted as a rug.

### Alerts on your iPhone

A BUY alert looks like this:

```
[early] BUY signal: ASSETS
mcap $237k | score 90/100 | 1h +347%
BUY at $0.0002370  (mcap $237k)
SELL 50% at 2x: $0.0004740  (mcap $474k)
SELL 20% at 5x: $0.001185  (mcap $1.2M)
SELL 15% at 10x: $0.002370  (mcap $2.4M)
Rest: sell if it falls 45% from its top
Sell all if it drops to $0.00003555 (-85%) or liquidity is pulled
Paper: bought $10
Address: 45rV...pump          [Copy address]  [Chart]
```

**score** is the scanner's own 0-100 momentum rating (1h move, buy/sell ratio, volume vs liquidity, 5 min move). FOMO has no public link that opens a coin inside the app, so: tap **Copy address**, open FOMO, paste into search. Long-press or pull down the notification if the buttons don't show.

- **Signal**: a coin passed every filter (bought on paper, or "not bought" if the paper account was at a limit)
- **SELL / STOP LOSS**: a take-profit step (+100%, +200%, +500%, +3000%), the trailing stop, the −45% stop, a rug exit or a time stop on a paper ticket
- **Milestones for coins the paper account did not buy** (in case you bought them in FOMO yourself): "hit +100% / +200% / +500% / +1000% / +3000%" and "STOP: down 45%"
- **Daily summary** around 20:00 Warsaw time, one per mode: paper balance, tickets, and how many of all signals reached 2x/3x/6x/11x/31x price
- **Take your $50 out**: once, when a paper account first reaches $100. Your rule: from then on only risk profits.
- **Paper account empty**: the $50 is spent; signals keep being tracked

`python3 run.py --report` shows **peaked_at_least**: how many tickets ever reached 2x, 3x, 6x, 11x, 31x price (= +100% … +3000%). That number is the one that decides if this is worth real money.

**Sources.** DexScreener boosted/profile lists (paid promotion) plus GeckoTerminal trending pools over 1 h and 6 h (organic, nobody pays to be there).

**Per signal** it prints the token address to paste into FOMO's search, the chart link, and what market cap 5x and 50x would need. On a Mac it also pops a notification.

## Momentum mode rules (details)

### What gets watched (universe)
| Rule | Value | Why |
|---|---|---|
| Chain | Solana | Where FOMO and the memecoin volume are |
| Pair age | 30 min to 72 h | The first 30 min is sniper and bundler territory |
| Liquidity | ≥ $30k | Below that, a rug is cheap and your order moves price |
| Market cap | $100k to $10M | |
| 24h volume | ≥ $100k | |
| 1h transactions | ≥ 200 | Real activity, not three wallets trading with each other |

### Rug and honeypot filters (RugCheck). Any one fails, skip.
- RugCheck risk score above 40 of 100, or any risk marked "danger", or flagged rugged
- Mint authority still live (dev can print supply)
- Freeze authority still live (dev can block your sell, which is the classic honeypot)
- Less than 90% of LP locked or burned
- Top 10 holders above 30%, or any single wallet above 10% (pool accounts excluded)

### Entry signal (all must hold)
- 5 min change ≥ 0% (still moving now)
- 1 h change between +10% and +150% (above +150% you are the exit liquidity)
- 24 h change ≤ +1000%
- 1 h buys/sells ≥ 1.2
- 1 h volume ≥ 0.5x pool liquidity
- Composite score ≥ 60

### Money management (hard limits)
| Rule | Value |
|---|---|
| Starting balance | $50 |
| Size per trade | $10, fixed |
| Open positions | max 2 |
| Trades per day | max 3 |
| Daily loss limit | −$10, then no entries until next UTC day |
| Kill switch | equity below $30 (−40%): stop everything and review |
| Same token twice | never (no revenge trades) |

### Exits
| Rule | Value |
|---|---|
| Stop loss | −25% from entry |
| Take profit 1 | sell half at +50% |
| Trailing stop | after TP1, the rest exits 20% below the peak |
| Time stop | 4 h |
| Liquidity pulled | pool liquidity down 40% from entry: exit at once |
| Pair disappears | treated as a rug at −90% |

All numbers live in `config.py`.

## Run it

Needs Python 3.9+ and `curl` (both come with macOS). No packages to install.

```bash
cd memecoin-scanner
python3 -m unittest test_offline     # 38 offline tests
python3 run.py --once                # one scan, prints signals
caffeinate -i python3 run.py         # moonshot paper trading, polls every 60 s, Mac stays awake
python3 run.py --mode momentum       # the scalping mode instead
python3 run.py --report              # results so far (add --mode momentum for that book)
```

Files it writes, per mode: `signals_<mode>.csv` (every signal), `paper_journal_<mode>.csv` (every simulated buy/sell with fees and peak multiple), `paper_state_<mode>.json`.

## iPhone alerts

1. Install **ntfy** from the App Store (free, no account).
2. Run `python3 run.py --test-push`. It prints your topic name (e.g. `mmfx-…`) and sends a test alert.
3. In the ntfy app tap **+**, enter that topic name, subscribe. You should see the test alert.

Every signal then arrives on your phone with the token address for FOMO's search; tapping it opens the chart. The topic name works like a password: anyone who knows it can read your alerts, so don't share `ntfy_topic.txt`.

## Missed movers (are the filters too strict?)

Every coin a filter rejects (except ones simply too old or too big) is followed for 24 h. If it
later reaches 5x its price at rejection, it is logged with the filter that blocked it. The daily
summary shows the count per filter; `python3 run.py --missed --mode moonshot` lists them. If one
filter keeps blocking 5x coins that did not rug, that filter is the one to loosen.

## Rug alerts

A paper position whose pool loses half its liquidity (or whose pair vanishes) is sold at once and
sends an urgent `RUG ...` alert. Signals the paper account did not buy get the same `RUG` alert.
Be clear about what it can do: a rug drains the pool in one transaction, so by the time any alert
arrives the coin is usually worth ~0. The alert tells you to sell the dust; the real protection is
the RugCheck filter and the $10 ticket size. FOMO has no stop-loss orders on memecoins, so the
`STOP LOSS` alert is your stop: you tap Sell yourself.

## Mac vs GitHub test

    git clone https://github.com/SuppleVise/memecoin-scanner ~/memecoin-scanner-mac
    cp ~/Downloads/memecoin-scanner/ntfy_topic.txt ~/memecoin-scanner-mac/
    cd ~/memecoin-scanner-mac && ./mac_test.sh

Both modes scan every 60 s into their own books (`paper_*_mac.*`, never committed). Alerts start
with `[mac early]` / `[mac moonshot]`; GitHub's say `[early]` / `[moonshot]`. Afterwards:
`python3 run.py --report --mode early --label mac` (and `--mode moonshot`).

## Going from paper to $50 real

**Momentum mode:** at least 30 closed paper trades over 2+ weeks, average P&L positive after fees, kill switch never tripped, and still positive with the best trade removed.

**Moonshot mode:** at least 40 paper tickets. Look at `peaked_at_least`. If fewer than 1 in 20 tickets reached 10x, the strategy loses money and real tickets would too. Here one huge winner *is* the strategy, so don't remove the best trade; instead ask whether the hit rate would pay for 20 dead tickets.

Then: put $50 in FOMO, follow the same signals by hand, same size and stops, and log each fill next to the paper journal. If real fills are much worse than paper (they usually are on exits), stop.

## Data sources

| Source | Use | Key needed |
|---|---|---|
| [DexScreener API](https://docs.dexscreener.com/api/reference) | discovery (boosts, profiles), pair price, volume, txns, liquidity | no |
| [GeckoTerminal API](https://www.geckoterminal.com/dex-api) | trending Solana pools (1h, 6h), organic discovery | no (about 10 calls/min) |
| RugCheck API (`api.rugcheck.xyz/v1/tokens/{mint}/report`) | mint/freeze authority, LP lock, holders, risk flags | no |
| [Bitquery FOMO API](https://docs.bitquery.io/docs/blockchain/Solana/fomo-api/) | what FOMO users are buying right now (possible extra signal) | yes |
| Birdeye, GMGN | richer holder and smart-wallet data | Birdeye paid key; GMGN has no official public API |

## Reference repos (not audited, read before running anything)

- [Frenzy911/solana-memecoin-bot](https://github.com/Frenzy911/solana-memecoin-bot): scanner + paper trading with rug checks and Jupiter quotes
- [m3n4ce-dev/meme_traderv1](https://github.com/m3n4ce-dev/meme_traderv1): paper-first pump.fun bot with an edge-vs-luck check
- [enscap/solana-memecoin-sniper](https://github.com/enscap/solana-memecoin-sniper): sniper + paper engine (pump.fun, Raydium, RugCheck)
- [Spleps/solana-memecoin-tracker](https://github.com/Spleps/solana-memecoin-tracker): Telegram alerts + rugcheck
- [kukapay/rug-check-mcp](https://github.com/kukapay/rug-check-mcp): rug check as an MCP tool (Solsniffer, key needed)

## Known limits

- DexScreener discovery is confirmed working live (first run 2026-10-25). The RugCheck and GeckoTerminal paths are built from their docs and tested on synthetic data only, until a live scan reaches them.
- Paper fills use the DexScreener price plus fixed slippage. Real exits in a dump fill worse.
- Polling every 60 s cannot catch a rug that happens in one block.
