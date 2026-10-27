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
        ws.buf, ws.frag = bytes([0x81, len(body)]) + body, b""   # unmasked server frame
        self.assertEqual(ws.recv(), body.decode())

    def test_half_frame_is_kept_until_the_rest_arrives(self):
        import socket
        chunks = []

        class Sock:
            def recv(self, n):
                if not chunks:
                    raise socket.timeout()
                return chunks.pop(0)
        ws = SP.WS.__new__(SP.WS)
        ws.sock, ws.frag = Sock(), b""
        body = b'{"a": 1}' * 40                      # 320 bytes: 2-byte extended length
        frame = bytes([0x81, 126]) + len(body).to_bytes(2, "big") + body
        ws.buf = frame[:50]
        self.assertIsNone(ws.recv())                # timeout mid-frame: nothing lost
        chunks.append(frame[50:])
        self.assertEqual(ws.recv(), body.decode())


def b58decode(s):
    n = 0
    for ch in s:
        n = n * 58 + SP.B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


def trade_log(mint, user, is_buy, sol_lamports, tok_raw, vsol, vtok):
    import base64, struct
    raw = (SP.TRADE_DISC + b58decode(mint) + struct.pack("<QQ", sol_lamports, tok_raw) + bytes([is_buy])
           + b58decode(user) + struct.pack("<qQQQQ", 1761580000, vsol, vtok, 0, 0) + b"more fields")
    return "Program data: " + base64.b64encode(raw).decode()


class SolanaLogs(unittest.TestCase):
    MINT, USER = "7c95d1uqguq9RoFvSzydCXzufrUVi1cy4gQduzg8pump", SP.PUMP_PROGRAM

    def test_base58_roundtrip(self):
        for k in (SP.PUMP_PROGRAM, self.MINT, "11111111111111111111111111111111"):
            self.assertEqual(SP.b58(b58decode(k)), k)

    def test_trade_event_decoded_like_pumpportal(self):
        logs = ["Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                "Program log: Instruction: Buy",
                trade_log(self.MINT, self.USER, 1, 500_000_000, 12_345_000_000, 35_000_000_000, 900_000_000_000_000),
                "Program data: G3KpTd7rY3YAAAAA"]   # a CreateEvent: ignored
        [ev] = SP.trades_from_logs(logs)
        self.assertEqual((ev["txType"], ev["mint"], ev["traderPublicKey"]), ("buy", self.MINT, self.USER))
        self.assertAlmostEqual(ev["solAmount"], 0.5)
        self.assertAlmostEqual(SP.curve_price(ev), 35.0 / 900_000_000.0)

    def test_feed_maps_subscriptions_and_notifications(self):
        sent = []
        feed = SP.TradeFeed.__new__(SP.TradeFeed)
        feed.next_id, feed.asked, feed.sub_of, feed.mint_of, feed.errors, feed.notes = 1, {}, {}, {}, 0, 0
        replies = []
        feed.ws = type("W", (), {"send": lambda self, o: sent.append(o),
                                 "recv": lambda self: replies.pop(0) if replies else None})()
        feed.subscribe([self.MINT, self.MINT])
        self.assertEqual(len(sent), 1)
        replies.append('{"jsonrpc":"2.0","result":77,"id":1}')
        self.assertEqual(feed.poll(), [])
        self.assertEqual(feed.sub_of[self.MINT], 77)
        note = {"jsonrpc": "2.0", "method": "logsNotification", "params": {"subscription": 77, "result": {
            "value": {"err": None, "logs": [trade_log(self.MINT, self.USER, 0, 1, 1, 30_000_000_000, 10**15)]}}}}
        replies.append(__import__("json").dumps(note))
        self.assertEqual(feed.poll()[0]["txType"], "sell")
        self.assertIsNone(feed.poll())
        feed.unsubscribe([self.MINT])
        self.assertEqual(sent[-1]["method"], "logsUnsubscribe")
        self.assertEqual(sent[-1]["params"], [77])


if __name__ == "__main__":
    unittest.main()
