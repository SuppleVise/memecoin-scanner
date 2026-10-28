"""Offline tests with synthetic data. Run: python -m unittest test_offline -v"""
import os
import tempfile
import time
import unittest

import config as C
C.NO_TICKET_LIMITS = False   # test each mode's own limits; the switch has its own test
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

    def test_missing_liquidity_is_not_a_rug(self):
        self.book.enter(self.sig)
        self.assertIsNone(self.book.check_exits("TOKEN1", {"priceUsd": "0.001"}))   # DexScreener left it out
        self.assertIn("TOKEN1", self.book.s["positions"])

    def test_vanished_pair_treated_as_rug(self):
        self.book.enter(self.sig)
        pnl = self.book.check_exits("TOKEN1", None)
        self.assertLess(pnl, -8)

    def test_limits(self):
        for i in range(3):
            s = dict(self.sig, token=f"T{i}", pair=f"P{i}", symbol=f"S{i}")
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
            s = dict(self.sig, token=f"T{i}", pair=f"P{i}", symbol=f"S{i}")
            self.book.enter(s)
            self.book.check_exits(f"T{i}", None)  # three -90% rugs
        self.assertIn(self.book.enter(dict(self.sig, token="T9", pair="P9", symbol="S9")),
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

    def test_ladder_to_31x(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(2.1))   # rung 1 (+100%): half off, stake back
        self.assertGreater(self.book.s["cash"], C.START_BALANCE_USD - 10 + 9)
        self.book.check_exits("TOKEN1", self.px(7))     # 3x and 6x rungs in one jump
        self.assertEqual(self.book.s["positions"]["TOKEN1"]["rungs_done"], 3)
        self.book.check_exits("TOKEN1", self.px(20))    # new peak
        self.book.check_exits("TOKEN1", self.px(8))     # -60% from peak: last 10% has no trail, holds
        self.assertIn("TOKEN1", self.book.s["positions"])
        self.book.check_exits("TOKEN1", self.px(32))    # 31x: sell the last 10%
        self.assertNotIn("TOKEN1", self.book.s["positions"])
        summ = self.book.summary()
        self.assertEqual(summ["peaked_at_least"]["31x"], 1)
        self.assertGreater(summ["equity"], 100)         # one 31x ticket more than doubles $50

    def test_trail_still_applies_before_last_slice(self):
        self.book.enter(self.sig)
        self.book.check_exits("TOKEN1", self.px(2.5))   # rung 1 done
        self.book.check_exits("TOKEN1", self.px(1.3))   # -48% from peak, before 3x -> trail out
        self.assertNotIn("TOKEN1", self.book.s["positions"])

    def test_gecko_parse_skips_sol(self):
        data = {"data": [{"relationships": {"base_token": {"data": {"id": "solana_MINTabc"}}}},
                         {"relationships": {"base_token": {"data": {"id": "solana_So11111111111111111111111111111111111111112"}}}}]}
        self.assertEqual(scanner.gecko_base_tokens(data), ["MINTabc"])

    def test_universe_targets_small_caps(self):
        p = pair(marketCap=5_000_000, fdv=5_000_000)
        self.assertTrue(any("mcap" in r for r in scanner.universe_reasons(p, NOW)))


class Switches(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))

    def tearDown(self):
        vars(C).update(self.saved)

    def test_no_ticket_limits_switch(self):
        C.NO_TICKET_LIMITS = True
        C.apply_mode("moonshot")
        self.assertGreaterEqual(C.MAX_TRADES_PER_DAY, 1000)
        C.NO_TICKET_LIMITS = False
        C.apply_mode("moonshot")
        self.assertEqual(C.MAX_TRADES_PER_DAY, 5)

    def test_insider_network_rejected(self):
        rep = {"score_normalised": 1, "lpLockedPct": 100, "token": {}, "risks": [],
               "topHolders": [{"address": "a", "pct": 2}], "graphInsidersDetected": 7}
        C.apply_mode("turbo")
        self.assertTrue(any("insider" in r for r in scanner.rug_reasons(rep)))
        rep["graphInsidersDetected"] = 0
        self.assertEqual(scanner.rug_reasons(rep), [])

    def test_early_and_turbo_sell_after_30_minutes(self):
        for m in ("early", "turbo"):
            C.apply_mode(m)
            self.assertEqual((C.TIME_STOP_H, C.TIME_STOP_MIN_MULT), (0.5, float("inf")))


class Gamble(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))
        C.apply_mode("gamble")
        d = tempfile.mkdtemp()
        self.book = Paper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"))

    def tearDown(self):
        vars(C).update(self.saved)

    def test_one_to_three_minute_old_coins_from_1k(self):
        ok = pair(pairCreatedAt=NOW - 2 * 60_000, marketCap=3_000, liquidity={"usd": 2_000},
                  volume={"h24": 1_000, "h1": 1_000}, txns={"h1": {"buys": 20, "sells": 5}})
        self.assertEqual(scanner.universe_reasons(ok, NOW), [])
        self.assertTrue(scanner.universe_reasons(dict(ok, pairCreatedAt=NOW - 30_000), NOW))
        self.assertTrue(scanner.universe_reasons(dict(ok, pairCreatedAt=NOW - 6 * 60_000), NOW))

    def test_ladder_in_profit_terms_and_10_minute_exit_even_in_profit(self):
        self.assertEqual(C.LADDER, [(3.0, 0.4), (6.0, 0.2), (11.0, 0.1), (31.0, 0.1), (51.0, 0.1)])
        t = time.time()
        self.book.enter({"token": "G", "symbol": "G", "pair": "P", "price": 0.001, "liquidity": 5_000, "score": 50}, t)
        self.assertIsNone(self.book.check_exits("G", {"priceUsd": "0.0015", "liquidity": {"usd": 5_000}}, t + 300))
        self.book.check_exits("G", {"priceUsd": "0.0015", "liquidity": {"usd": 5_000}}, t + 601)
        self.assertNotIn("G", self.book.s["positions"])

    def test_other_modes_do_not_inherit_gamble_settings(self):
        C.apply_mode("moonshot")
        self.assertEqual(C.POLL_SECONDS, 60)
        self.assertNotEqual(C.TIME_STOP_MIN_MULT, float("inf"))


class Turbo(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))
        C.apply_mode("turbo")

    def tearDown(self):
        vars(C).update(self.saved)

    def test_only_5_to_10_minute_old_small_caps(self):
        young = pair(pairCreatedAt=NOW - 7 * 60_000, marketCap=40_000, liquidity={"usd": 12_000})
        self.assertEqual(scanner.universe_reasons(young, NOW), [])
        self.assertTrue(scanner.universe_reasons(pair(pairCreatedAt=NOW - 15 * 60_000, marketCap=40_000,
                                                      liquidity={"usd": 12_000}), NOW))
        self.assertTrue(scanner.universe_reasons(pair(pairCreatedAt=NOW - 7 * 60_000, marketCap=15_000,
                                                      liquidity={"usd": 12_000}), NOW))

    def test_unlocked_lp_ok_but_mint_authority_still_rejected(self):
        rep = {"score_normalised": 20, "lpLockedPct": 0, "token": {},
               "risks": [{"name": "Large Amount of LP Unlocked", "level": "danger"}],
               "topHolders": [{"address": "a", "pct": 5}]}
        self.assertEqual(scanner.rug_reasons(rep), [])
        rep["token"] = {"mintAuthority": "dev"}
        self.assertTrue(scanner.rug_reasons(rep))

    def test_ladder_profit_2_5_10_30x(self):
        self.assertEqual([m for m, _ in C.LADDER], [3.0, 6.0, 11.0, 31.0])
        self.assertAlmostEqual(sum(f for _, f in C.LADDER), 1.0)
        self.assertEqual(C.MAX_TRADES_PER_DAY, 20)


class ShadowAndAlerts(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))
        C.apply_mode("moonshot")
        d = tempfile.mkdtemp()
        self.book = Paper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"))

    def tearDown(self):
        vars(C).update(self.saved)

    def sig(self, i, price=0.001):
        return {"token": f"T{i}", "symbol": f"S{i}", "pair": f"P{i}", "price": price,
                "score": 80, "liquidity": 20_000}

    def test_shadow_tracks_signals_beyond_cash(self):
        t = time.time()
        for i in range(12):                     # more signals than $50 can buy
            self.book.shadow_add(self.sig(i), t)
            self.book.enter(self.sig(i), t)
        self.assertEqual(len(self.book.s["positions"]), 5)
        self.assertEqual(self.book.shadow_stats()["signals"], 12)
        # one of them runs to 12x
        self.book.shadow_update({f"T{i}": {"priceUsd": "0.012" if i == 3 else "0.0005"} for i in range(12)}, t + 60)
        st = self.book.shadow_stats()
        self.assertEqual(st["peaked_at_least"]["11x"], 1)
        self.assertEqual(st["best"], "12.0x")

    def test_shadow_rug_needs_three_misses(self):
        t = time.time()
        self.book.shadow_add(self.sig(1), t)
        self.book.shadow_update({}, t + 60)
        self.book.shadow_update({}, t + 120)
        self.assertIn("T1", self.book.s["shadow"])
        self.book.shadow_update({}, t + 180)
        self.assertNotIn("T1", self.book.s["shadow"])
        self.assertEqual(self.book.s["shadow_done"]["T1"], 1.0)

    def test_shadow_expires(self):
        t = time.time()
        self.book.shadow_add(self.sig(1), t)
        self.book.shadow_update({"T1": {"priceUsd": "0.003"}}, t + C.SHADOW_HOURS * 3600 + 1)
        self.assertEqual(self.book.s["shadow_done"]["T1"], 3.0)

    def test_withdraw_alert_once(self):
        self.book.s["cash"] = 101
        self.assertTrue(self.book.withdraw_due())
        self.assertFalse(self.book.withdraw_due())

    def test_trade_limit_raised(self):
        self.assertEqual(C.MAX_TRADES_PER_DAY, 5)


class EarlyMode(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))
        C.apply_mode("early")

    def tearDown(self):
        vars(C).update(self.saved)

    def test_young_coin_in_universe(self):
        p = pair(pairCreatedAt=NOW - 40 * 60_000, marketCap=150_000, fdv=150_000,
                 liquidity={"usd": 25_000}, volume={"h1": 40_000, "h24": 40_000})
        self.assertEqual(scanner.universe_reasons(p, NOW), [])

    def test_six_hour_old_coin_out(self):
        p = pair(marketCap=150_000, fdv=150_000)
        self.assertTrue(any("too old" in r for r in scanner.universe_reasons(p, NOW)))

    def test_no_paid_boost_lists(self):
        self.assertFalse(C.USE_DEX_BOOSTS)


class StopAndMilestones(unittest.TestCase):
    def setUp(self):
        self.saved = dict(vars(C))
        C.apply_mode("moonshot")
        d = tempfile.mkdtemp()
        self.book = Paper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"))
        self.sig = {"token": "T1", "symbol": "S1", "pair": "P1", "price": 0.001, "score": 80,
                    "liquidity": 20_000, "url": "u"}

    def tearDown(self):
        vars(C).update(self.saved)

    def test_stop_at_minus_45(self):
        self.book.enter(self.sig)
        fill = self.book.s["positions"]["T1"]["entry"]
        self.book.check_exits("T1", {"priceUsd": str(fill * 0.6), "liquidity": {"usd": 20_000}})
        self.assertIn("T1", self.book.s["positions"])           # -40%: hold
        self.book.check_exits("T1", {"priceUsd": str(fill * 0.54), "liquidity": {"usd": 20_000}})
        self.assertNotIn("T1", self.book.s["positions"])        # -46%: stopped
        self.assertIn("stop loss", self.book.journal_tail())

    def test_shadow_rug_on_liquidity_pull_fires_once(self):
        t = time.time()
        self.book.shadow_add(self.sig, t)
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.0009", "liquidity": {"usd": 15_000}}}, t + 60)
        self.assertEqual(ev, [])                                    # -25% liquidity: not a rug
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.00001", "liquidity": {"usd": 900}}}, t + 120)
        self.assertEqual([e[2] for e in ev], ["stop", "rug"])
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.00001", "liquidity": {"usd": 900}}}, t + 180)
        self.assertEqual(ev, [])

    def test_shadow_rug_when_pair_disappears(self):
        t = time.time()
        self.book.shadow_add(self.sig, t)
        ev = []
        for i in range(3):
            ev += self.book.shadow_update({"OTHER": {"priceUsd": "1"}}, t + 60 * (i + 1))
        self.assertEqual([e[2] for e in ev], ["rug"])
        self.assertNotIn("T1", self.book.s["shadow"])

    def test_missed_mover_logged_once_with_reason(self):
        t = time.time()
        self.book.missed_add({"M1": {"symbol": "MOON", "price": 0.001, "reason": "rug filter: LP locked 0%"}}, t)
        self.assertEqual(self.book.missed_update({"M1": {"priceUsd": "0.003"}}, t + 60), [])
        hit = self.book.missed_update({"M1": {"priceUsd": "0.006"}}, t + 120)
        self.assertEqual([(i["symbol"], i["mult"]) for i in hit], [("MOON", 6.0)])
        self.assertEqual(self.book.missed_update({"M1": {"priceUsd": "0.009"}}, t + 180), [])
        st = self.book.missed_stats()
        self.assertEqual((st["count"], st["by_filter"], st["best"][0]["mult"]), (1, {"rug filter": 1}, 9.0))
        self.book.missed_update({}, t + 25 * 3600)
        self.assertEqual(self.book.s["missed"], {})              # stops following after 24 h

    def test_restart_after_halt_even_if_coins_recovered_and_daily_loss_reset(self):
        self.book.s.update(cash=14.0, halted=True, day_pnl=-60.0)
        self.assertIsNotNone(self.book.bust_restart())
        self.assertEqual((self.book.s["cash"], self.book.s["halted"], self.book.s["day_pnl"]),
                         (C.START_BALANCE_USD, False, 0.0))
        self.assertIsNone(self.book.can_enter("NEW", symbol="NEW"))

    def test_copycat_same_name_blocked_for_an_hour(self):
        t = time.time()
        sig = lambda tok: {"token": tok, "symbol": "Ginnan", "pair": "P" + tok, "price": 0.001,
                           "liquidity": 20_000, "score": 80}
        self.assertIsNone(self.book.enter(sig("A"), t))
        self.assertIn("copycat", self.book.enter(dict(sig("B"), symbol="GINNAN "), t + 60))
        self.assertIsNone(self.book.enter(sig("C"), t + 3601))

    def test_bad_price_tick_does_not_sell_or_count(self):
        t = time.time()
        self.book.enter({"token": "G1", "symbol": "GLITCH", "pair": "P1", "price": 0.001, "liquidity": 30_000, "score": 80}, t)
        self.assertIsNone(self.book.check_exits("G1", {"priceUsd": "500", "liquidity": {"usd": 30_000}}, t + 60))
        self.assertIn("G1", self.book.s["positions"])
        self.assertLess(self.book.s["positions"]["G1"]["peak"], 0.002)
        self.book.shadow_add({"token": "G2", "symbol": "GL2", "price": 0.001, "liquidity": 30_000}, t)
        self.book.shadow_update({"G2": {"priceUsd": "500", "liquidity": {"usd": 30_000}}}, t + 60)
        self.assertEqual(self.book.s["shadow"]["G2"]["peak"], 0.001)

    def test_rug_exit_pays_only_what_liquidity_is_left(self):
        t = time.time()
        self.book.enter({"token": "R1", "symbol": "RUG", "pair": "P1", "price": 0.001, "liquidity": 30_000, "score": 80}, t)
        cash = self.book.s["cash"]
        self.book.check_exits("R1", {"priceUsd": "0.003", "liquidity": {"usd": 0}}, t + 60)
        self.assertNotIn("R1", self.book.s["positions"])
        self.assertLess(self.book.s["cash"] - cash, 0.01)      # $0 pool pays $0, not 3x

    def test_position_follows_coin_to_its_new_pool(self):
        import run, scanner
        pos = {"pair": "OLD", "symbol": "GRAD", "entry_liq": 30_000}
        new = {"pairAddress": "NEW", "dexId": "raydium", "liquidity": {"usd": 80_000}, "priceUsd": "0.004"}
        orig = scanner.fetch_pairs
        scanner.fetch_pairs = lambda toks: {"T": new}
        try:
            got = run.follow_migration(pos, {"liquidity": {"usd": 0}, "priceUsd": "0.004"}, "T", log=lambda *a: None)
        finally:
            scanner.fetch_pairs = orig
        self.assertEqual((got["pairAddress"], pos["pair"]), ("NEW", "NEW"))

    def test_missed_mover_ignores_empty_pools_and_absurd_jumps(self):
        t = time.time()
        self.book.missed_add({"M2": {"symbol": "JUNK", "price": 1e-9, "reason": "size/age: liquidity $0"}}, t)
        self.assertEqual(self.book.missed_update({"M2": {"priceUsd": "0.01", "liquidity": {"usd": 20_000}}}, t + 60), [])
        self.assertEqual(self.book.missed_update({"M2": {"priceUsd": "1e-8", "liquidity": {"usd": 900}}}, t + 120), [])
        self.assertEqual(self.book.s["missed_big"], [])

    def test_scan_records_rejects_but_not_old_or_big_coins(self):
        import scanner
        now = time.time() * 1000
        def pair(addr, age_h, mcap, h1):
            return {"baseToken": {"address": addr, "symbol": addr}, "pairCreatedAt": now - age_h * 3600e3,
                    "liquidity": {"usd": 30_000}, "marketCap": mcap, "priceUsd": "0.001",
                    "volume": {"h24": 1e6, "h1": 50_000}, "txns": {"h1": {"buys": 300, "sells": 100}},
                    "priceChange": {"m5": 2, "h1": h1, "h24": 50}}
        pairs = {"OLD": pair("OLD", 200, 100_000, 50), "BIG": pair("BIG", 5, 9e6, 50),
                 "WEAK": pair("WEAK", 5, 100_000, -10), "NEW": pair("NEW", 0.2, 100_000, 50)}
        orig = scanner.discover_candidates, scanner.fetch_pairs
        scanner.discover_candidates, scanner.fetch_pairs = (lambda: list(pairs)), (lambda a: pairs)
        try:
            rej = {}
            scanner.scan(log=lambda *_: None, rejected=rej)
        finally:
            scanner.discover_candidates, scanner.fetch_pairs = orig
        self.assertEqual(sorted(rej), ["NEW", "WEAK"])
        self.assertTrue(rej["WEAK"]["reason"].startswith("momentum"))

    def test_record_writes_gzip_snapshot(self):
        import gzip, scanner
        cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        try:
            scanner.record({"M1": {"baseToken": {"symbol": "S"}, "priceUsd": "0.001", "liquidity": {"usd": 5}}})
            files = [os.path.join(d, f) for d, _, fs in os.walk("data") for f in fs]
            with gzip.open(files[0], "rt") as f:
                rows = f.read().splitlines()
        finally:
            os.chdir(cwd)
        self.assertEqual(rows[0].split(",")[:3], ["t", "mode", "token"])
        self.assertIn("M1,S", rows[1])

    def test_bust_restarts_with_fresh_50(self):
        self.book.enter(self.sig)
        self.assertIsNone(self.book.bust_restart())               # holding: not broke
        fill = self.book.s["positions"]["T1"]["entry"]
        self.book.s["cash"] = 3.0
        self.book.check_exits("T1", {"priceUsd": str(fill * 0.5), "liquidity": {"usd": 1_000}})  # rug
        rec = self.book.bust_restart()
        self.assertEqual((rec["round"], rec["tickets"]), (1, 1))
        self.assertEqual(self.book.s["cash"], C.START_BALANCE_USD)
        self.assertFalse(self.book.s["halted"])
        self.assertIsNone(self.book.bust_restart())               # fresh $50: fine
        acts = [r["action"] for r in self.book.trades_since(0)]
        self.assertEqual(acts, ["BUY", "SELL", "RESTART"])

    def test_variant_all_ignores_limits_and_tracks_extra_cash(self):
        d = tempfile.mkdtemp()
        a = Paper(os.path.join(d, "a.json"), os.path.join(d, "a.csv"), variant="all")
        for i in range(8):
            self.assertIsNone(a.enter({**self.sig, "token": f"T{i}", "pair": f"P{i}"}))
        self.assertEqual(len(a.s["positions"]), 8)                 # real rules stop at 5
        self.assertAlmostEqual(a.s["min_cash"], C.START_BALANCE_USD - 80)
        self.assertIsNotNone(a.enter({**self.sig, "token": "T1"}))  # still no re-entries
        self.assertIsNone(a.bust_restart())

    def test_variant_pct10_sizes_from_equity_with_10_floor(self):
        d = tempfile.mkdtemp()
        b = Paper(os.path.join(d, "b.json"), os.path.join(d, "b.csv"), variant="pct10")
        self.assertEqual(b.ticket_usd(), C.POSITION_SIZE_USD)       # $50 account: still $10
        b.s["cash"] = 300.0
        self.assertEqual(b.ticket_usd(), 30.0)
        b.enter(self.sig)
        self.assertEqual(b.s["positions"]["T1"]["cost"], 30.0)

    def test_label_gives_separate_files(self):
        C.apply_mode("early", "mac")
        self.assertEqual(C.STATE_FILE, "paper_state_early_mac.json")
        self.assertEqual(C.SIGNALS_FILE, "signals_early_mac.csv")

    def test_shadow_milestones_fire_once(self):
        t = time.time()
        self.book.shadow_add(self.sig, t)
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.0021"}}, t + 60)
        self.assertEqual([e[2] for e in ev], ["2x"])
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.0022"}}, t + 120)
        self.assertEqual(ev, [])
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.011"}}, t + 180)
        self.assertEqual([e[2] for e in ev], ["3x", "6x", "11x"])

    def test_shadow_stop_then_recovery_counted(self):
        t = time.time()
        self.book.shadow_add(self.sig, t)
        ev = self.book.shadow_update({"T1": {"priceUsd": "0.0005"}}, t + 60)
        self.assertEqual([e[2] for e in ev], ["stop"])
        self.book.shadow_update({"T1": {"priceUsd": "0.0025"}}, t + 120)   # later 2.5x
        self.assertEqual(self.book.shadow_stats()["stopped_then_2x"], 1)


if __name__ == "__main__":
    unittest.main()
