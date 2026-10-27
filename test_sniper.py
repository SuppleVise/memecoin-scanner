"""Offline tests for the paper sniper: a fake pump.fun feed, no network."""
import os
import tempfile
import unittest

import sniper_paper as SP

DEV, P0_SOL, P0_TOK = "DEVwallet", 30.0, 1_000_000_000.0


def create(mint="M1", symbol="CAT", dev_buy=0.5, creator=DEV):
    return {"txType": "create", "mint": mint, "traderPublicKey": creator, "symbol": symbol, "name": symbol,
            "solAmount": dev_buy, "vSolInBondingCurve": P0_SOL, "vTokensInBondingCurve": P0_TOK}


def trade(mint, who, side, sol, mult):
    """A trade that leaves the curve price at `mult` times launch."""
    return {"txType": side, "mint": mint, "traderPublicKey": who, "solAmount": sol,
            "vSolInBondingCurve": P0_SOL * mult, "vTokensInBondingCurve": P0_TOK}


class Base(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.bot = SP.Sniper(os.path.join(d, "s.json"), os.path.join(d, "j.csv"), sol_usd=150.0)

    def crowd(self, mint="M1", t=0.0, n=8, sol=0.4, mult=1.2):
        """n different wallets buy in the first 10 seconds."""
        for i in range(n):
            self.bot.on_event(trade(mint, f"buyer{i}", "buy", sol, mult), t + 1 + i)

    def bought(self, mint="M1"):
        return mint in self.bot.s["positions"]


class Entry(Base):
    def test_real_crowd_gets_bought_after_latency(self):
        sub, _ = self.bot.on_event(create(), 0)
        self.assertEqual(sub, ["M1"])
        self.crowd()
        self.assertIn("M1", self.bot.pending)
        self.assertFalse(self.bought())
        self.bot.on_event(trade("M1", "late", "buy", 0.2, 1.3), 11)   # first trade after the delay fills
        self.assertTrue(self.bought())
        p = self.bot.s["positions"]["M1"]
        self.assertAlmostEqual(p["entry"], P0_SOL * 1.3 / P0_TOK * 1.02)
        self.assertEqual(self.bot.s["cash"], 40.0)

    def test_one_whale_is_not_a_crowd(self):
        self.bot.on_event(create(), 0)
        self.bot.on_event(trade("M1", "whale", "buy", 5.0, 1.5), 1)
        self.crowd(sol=0.05)
        self.bot.on_event(trade("M1", "x", "buy", 0.1, 1.5), 61)
        self.assertNotIn("M1", self.bot.pending)
        self.assertFalse(self.bought())

    def test_big_dev_bag_rejected(self):
        self.bot.on_event(create(dev_buy=5.0), 0)
        self.crowd()
        self.assertFalse(self.bought())
        self.assertNotIn("M1", self.bot.pending)

    def test_creator_selling_while_watched_kills_it(self):
        self.bot.on_event(create(), 0)
        self.bot.on_event(trade("M1", DEV, "sell", 0.5, 0.9), 2)
        _, unsub = self.bot.on_event(trade("M1", "b", "buy", 0.4, 0.9), 3)
        self.crowd()
        self.assertFalse(self.bought())
        self.assertEqual(self.bot.s["dev_dumped_early"], 1)

    def test_serial_creator_and_copycat_skipped(self):
        self.bot.on_event(create("M1"), 0)
        sub, _ = self.bot.on_event(create("M2", symbol="DOG"), 5)   # same creator again
        self.assertEqual(sub, [])
        self.crowd("M1")
        self.bot.on_event(trade("M1", "z", "buy", 0.1, 1.3), 11)
        self.assertTrue(self.bought("M1"))
        self.bot.on_event(create("M3", symbol="CAT", creator="other"), 20)   # same name as a coin we hold
        self.crowd("M3", t=20)
        self.assertFalse(self.bought("M3"))

    def test_already_pumped_skipped(self):
        self.bot.on_event(create(), 0)
        self.crowd(mult=4.0)
        self.assertFalse(self.bought())


class Follow(Base):
    def test_skipped_coin_with_buyers_is_recorded_10_min(self):
        self.bot.on_event(create(dev_buy=5.0), 0)          # rejected: big dev bag
        _, unsub = self.bot.on_event(trade("M1", "a", "buy", 0.1, 1.1), 9)
        self.assertEqual(unsub, [])
        self.crowd(t=9)
        self.assertIn("M1", self.bot.follow)
        self.assertFalse(self.bought())
        self.assertEqual(self.bot.tick(300)[1], [])
        self.assertEqual(self.bot.tick(601)[1], ["M1"])

    def test_dead_launch_is_dropped_after_the_watch(self):
        self.bot.on_event(create(), 0)
        self.assertEqual(self.bot.tick(80)[1], ["M1"])   # nobody bought: not worth recording
        self.assertNotIn("M1", self.bot.follow)


class Exits(Base):
    def open(self):
        self.bot.on_event(create(), 0)
        self.crowd()
        self.bot.on_event(trade("M1", "z", "buy", 0.1, 1.2), 11)
        self.assertTrue(self.bought())
        return self.bot.s["positions"]["M1"]["entry"] / (P0_SOL / P0_TOK)   # entry as launch multiple

    def test_creator_dump_exits_at_once(self):
        self.open()
        _, unsub = self.bot.on_event(trade("M1", DEV, "sell", 3.0, 0.8), 20)
        self.assertFalse(self.bought())
        self.assertEqual(unsub, ["M1"])
        self.assertLess(self.bot.s["cash"], 50)

    def test_stop_loss(self):
        e = self.open()
        self.bot.on_event(trade("M1", "y", "sell", 1.0, e * 0.5), 30)
        self.assertFalse(self.bought())
        self.assertEqual(self.bot.s["closed"][-1]["reason"], "stop -45%")

    def test_ladder_then_trail(self):
        e = self.open()
        self.bot.on_event(trade("M1", "y", "buy", 1.0, e * 3.1), 30)
        p = self.bot.s["positions"]["M1"]
        self.assertEqual(p["rung"], 1)
        self.assertAlmostEqual(p["qty"], p["qty0"] * 0.7)
        self.bot.on_event(trade("M1", "y", "sell", 1.0, e * 1.6), 40)   # -48% from the 3.1x peak
        self.assertFalse(self.bought())
        self.assertEqual(self.bot.s["closed"][-1]["reason"], "trailing stop")
        self.assertGreater(self.bot.s["closed"][-1]["pnl"], 0)

    def test_time_stop_and_idle(self):
        self.open()
        self.bot.tick(11 + 119)
        self.assertTrue(self.bought())
        self.bot.tick(11 + 121)
        self.assertEqual(self.bot.s["closed"][-1]["reason"], "no trades (dead coin)")

    def test_fees_make_a_flat_trade_lose(self):
        e = self.open()
        self.bot.on_event(trade("M1", "y", "buy", 0.1, e), 612)   # 10 minutes later, price unchanged
        self.assertFalse(self.bought())
        self.assertLess(self.bot.s["closed"][-1]["pnl"], -0.5)

    def test_bust_round_restarts(self):
        self.bot.s["cash"] = 10.0
        self.open()
        self.bot.on_event(trade("M1", DEV, "sell", 3.0, 0.3), 20)
        self.assertEqual(self.bot.s["rounds"], 2)
        self.assertEqual(self.bot.s["cash"], 50.0)
        self.assertLess(self.bot.total_pnl(), -40)

    def test_state_survives_restart_and_summary(self):
        self.open()
        self.bot.save()
        again = SP.Sniper(self.bot.state_file, self.bot.journal_file)
        self.assertIn("M1", again.s["positions"])
        again.close_all(100)
        self.assertIn("Total P&L", again.summary())


class Frames(unittest.TestCase):
    def test_masked_frame_roundtrip(self):
        ws = SP.WS.__new__(SP.WS)
        sent = []
        ws.sock = type("S", (), {"sendall": lambda self, b: sent.append(b)})()
        ws.send({"method": "subscribeNewToken"})
        f = sent[0]
        n, mask = f[1] & 0x7F, f[2:6]
        body = bytes(b ^ mask[i % 4] for i, b in enumerate(f[6:6 + n]))
        self.assertEqual(body, b'{"method": "subscribeNewToken"}')
        ws.buf = bytes([0x81, len(body)]) + body   # unmasked server frame
        self.assertEqual(ws.recv(), body.decode())


if __name__ == "__main__":
    unittest.main()
