"""Offline tests with synthetic data. Run: python -m unittest test_offline -v"""
import os
import tempfile
import time
import unittest

import config as C
import scanner
from paper import Paper

NOW = time.time() * 1000


def pair(**kw):
    p = {
        "pairAddress": "PAIR1", "url": "https://dexscreener.com/solana/pair1",
        "baseToken": {"address": "TOKEN1", "symbol": "TEST"},
        "priceUsd": "0.001", "pairCreatedAt": NOW - 6 * 3600_000,
        "liquidity": {"usd": 80_000}, "marketCap": 900_000, "fdv": 900_000,
        "volume": {"h1": 90_000, "h24": 600_000},
        "txns": {"h1": {"buys": 420, "sells": 260}},
        "priceChange": {"m5": 3.0, "h1": 35.0, "h24": 120.0},
    }
    p.update(kw)
    return p


def report(**kw):
    r = {
        "score_normalised": 12, "rugged": False, "lpLockedPct": 100, "risks": [],
        "token": {"mintAuthority": None, "freezeAuthority": None},
        "markets": [{"pubkey": "POOLVAULT" + "x" * 30}],
        "topHolders": [{"address": "POOLVAULT" + "x" * 30, "pct": 18.0}] +
                      [{"address": f"W{i}" + "y" * 32, "owner": f"O{i}", "pct": 2.0} for i in range(10)],
    }
    r.update(kw)
    return r


class Filters(unittest.TestCase):
    def test_good_pair_passes_universe(self):
        self.assertEqual(scanner.universe_reasons(pair(), NOW), [])

    def test_too_new_rejected(self):
        self.assertTrue(scanner.universe_reasons(pair(pairCreatedAt=NOW - 10 * 60_000), NOW))

    def test_thin_liquidity_rejected(self):
        self.assertTrue(scanner.universe_reasons(pair(liquidity={"usd": 8_000}), NOW))

    def test_good_momentum_passes(self):
        score, bad = scanner.momentum(pair())
        self.assertEqual(bad, [], bad)
        self.assertGreaterEqual(score, C.MIN_SCORE)

    def test_parabolic_rejected(self):
        _, bad = scanner.momentum(pair(priceChange={"m5": 10, "h1": 400, "h24": 900}))
        self.assertTrue(any("extended" in b for b in bad))

    def test_sell_pressure_rejected(self):
        _, bad = scanner.momentum(pair(txns={"h1": {"buys": 200, "sells": 300}}))
        self.assertTrue(any("buy/sell" in b for b in bad))

    def test_clean_report_passes_and_pool_not_counted_as_whale(self):
        self.assertEqual(scanner.rug_reasons(report()), [])

    def test_freeze_authority_is_honeypot(self):
        bad = scanner.rug_reasons(report(token={"mintAuthority": None, "freezeAuthority": "DEV"}))
        self.assertTrue(any("freeze" in b for b in bad))

    def test_mint_authority_rejected(self):
        bad = scanner.rug_reasons(report(token={"mintAuthority": "DEV", "freezeAuthority": None}))
        self.assertTrue(any("mint" in b for b in bad))

    def test_whale_rejected(self):
        holders = [{"address": "WHALE" + "z" * 30, "pct": 25.0}]
        self.assertTrue(scanner.rug_reasons(report(topHolders=holders)))

    def test_danger_risk_rejected(self):
        self.assertTrue(scanner.rug_reasons(report(risks=[{"name": "Copycat token", "level": "danger"}])))

    def test_unlocked_lp_rejected(self):
        self.assertTrue(scanner.rug_reasons(report(lpLockedPct=0)))

    def test_missing_report_rejected(self):
        self.assertEqual(scanner.rug_reasons(None), ["no rugcheck report"])


class PaperBook(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.book = Paper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"))
        self.sig = {"token": "TOKEN1", "symbol": "TEST", "pair": "PAIR1", "price": 0.001,
                    "score": 80, "liquidity": 80_000}

    def px(self, price, liq=80_000):
        return {"priceUsd": str(price), "liquidity": {"usd": liq}}

    def test_round_trip_flat_price_loses_to_costs(self):
        t = time.time()
        self.book.enter(self.sig, t)
        pnl = self.book.check_exits("TOKEN1", self.px(0.001), t + 5 * 3600)  # time stop
        # 2% fee each way + 2% slippage each way on a $10 trade
        self.assertLess(pnl, -0.7)
        self.assertGreater(pnl, -0.9)

    def test_stop_loss(self):
        self.book.enter(self.sig)
        pnl = self.book.check_exits("TOKEN1", self.px(0.0007))
        self.assertLess(pnl, -3)
        self.assertNotIn("TOKEN1", self.book.s["positions"])

    def test_take_profit_then_trail(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(0.0016))  # +56% after slippage -> TP1
        self.assertEqual(self.book.s["positions"]["TOKEN1"]["rungs_done"], 1)
        self.book.check_exits("TOKEN1", self.px(0.0025))  # new peak
        self.book.check_exits("TOKEN1", self.px(0.0019))  # -24% from peak -> trail out
        self.assertNotIn("TOKEN1", self.book.s["positions"])
        self.assertGreater(self.book.equity(), C.START_BALANCE_USD)

    def test_liquidity_pull_exits(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(0.001, liq=20_000))
        self.assertNotIn("TOKEN1", self.book.s["positions"])

    def test_vanished_pair_treated_as_rug(self):
        self.book.enter(self.sig)
        pnl = self.book.check_exits("TOKEN1", None)
        self.assertLess(pnl, -8)

    def test_limits(self):
        for i in range(3):
            s = dict(self.sig, token=f"T{i}", pair=f"P{i}")
            r = self.book.enter(s)
            if i < 2:
                self.assertIsNone(r)
            else:
                self.assertEqual(r, "max open positions")

    def test_no_reentry_same_token(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(0.0007))
        self.assertIn("already traded", self.book.enter(self.sig))

    def test_daily_loss_limit(self):
        for i in range(3):
            s = dict(self.sig, token=f"T{i}", pair=f"P{i}")
            self.book.enter(s)
            self.book.check_exits(f"T{i}", None)  # three -90% rugs
        self.assertIn(self.book.enter(dict(self.sig, token="T9", pair="P9")),
                      ("daily loss limit hit", "max trades today"))

    def test_fee_schedule(self):
        self.assertAlmostEqual(C.fomo_fee(4), 0.10)
        self.assertAlmostEqual(C.fomo_fee(10), 0.20)
        self.assertAlmostEqual(C.fomo_fee(100), 0.95)
        self.assertAlmostEqual(C.fomo_fee(1000), 5.0)


class Moonshot(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))
        C.apply_mode("moonshot")
        d = tempfile.mkdtemp()
        self.book = Paper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"))
        self.sig = {"token": "TOKEN1", "symbol": "MOON", "pair": "PAIR1", "price": 0.0001,
                    "score": 80, "liquidity": 20_000}

    def tearDown(self):
        vars(C).update(self.saved)

    def px(self, mult, liq=20_000):
        return {"priceUsd": str(0.0001 * 1.02 * mult), "liquidity": {"usd": liq}}

    def test_no_tight_stop(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(0.6))  # -40% would stop out momentum mode
        self.assertIn("TOKEN1", self.book.s["positions"])

    def test_dead_coin_salvage(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(0.1))
        self.assertNotIn("TOKEN1", self.book.s["positions"])

    def test_ladder_to_20x(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(2.1))   # rung 1: half off, stake back
        self.assertGreater(self.book.s["cash"], C.START_BALANCE_USD - 10 + 9)
        self.book.check_exits("TOKEN1", self.px(12))    # 5x and 10x rungs in one jump
        self.assertEqual(self.book.s["positions"]["TOKEN1"]["rungs_done"], 3)
        self.book.check_exits("TOKEN1", self.px(20))    # new peak
        self.book.check_exits("TOKEN1", self.px(10))    # -50% from peak -> trail out
        self.assertNotIn("TOKEN1", self.book.s["positions"])
        summ = self.book.summary()
        self.assertEqual(summ["peaked_at_least"]["10x"], 1)
        self.assertGreater(summ["equity"], 100)         # one 20x ticket more than doubles $50

    def test_gecko_parse_skips_sol(self):
        data = {"data": [{"relationships": {"base_token": {"data": {"id": "solana_MINTabc"}}}},
                         {"relationships": {"base_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}}}}]}
        self.assertEqual(scanner.gecko_base_tokens(data), ["MINTabc"])

    def test_universe_targets_small_caps(self):
        p = pair(marketCap=5_000_000, fdv=5_000_000)
        self.assertTrue(any("mcap" in r for r in scanner.universe_reasons(p, NOW)))


if __name__ == "__main__":
    unittest.main()
