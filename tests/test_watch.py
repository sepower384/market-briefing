# -*- coding: utf-8 -*-
"""레이더망(급등락 감시) — 같은 움직임 반복 알림 차단 + '왜 움직였나' 뉴스 테스트. 네트워크 없음."""
import datetime as dt
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import briefing as bf  # noqa: E402
import news_lookup as nl  # noqa: E402

CFG = {
    "briefing": {"timezone_offset_hours": 9},
    "alerts": {"coin_1h_pct": 3.0, "coin_24h_pct": 7.0, "stock_day_pct": 4.0, "index_day_pct": 2.0,
               "dominance_drop_24h_pp": 0.5, "cooldown_minutes": 90},
    "coins": [{"symbol": "BTCUSDT", "name": "비트코인"}, {"symbol": "ZECUSDT", "name": "지캐시"}],
    "us_stocks": [{"ticker": "COIN", "name": "코인베이스"}],
    "kr_stocks": [],
}
T0 = dt.datetime(2026, 9, 17, 0, 0)


def snap(ch24, ch1=0.2, price=100.0):
    return {"price": price, "change_24h": ch24, "change_1h": ch1, "error": None,
            "ta": {"support": 80.0, "resistance": 110.0, "vol_ratio_1h": 1.0}}


class Market(object):
    """시간대별로 바뀌는 가짜 시세."""

    def __init__(self):
        self.coins = {"BTCUSDT": snap(0.5), "ZECUSDT": snap(0.0)}
        self.stock = None

    def patch(self):
        return [
            mock.patch.object(bf.mk, "coin_snapshot", side_effect=lambda s, with_ta=True: dict(self.coins[s])),
            mock.patch.object(bf.mk, "dominance", side_effect=RuntimeError("off")),
            mock.patch.object(bf.mk, "stock_quick", side_effect=lambda t: self.stock),
            mock.patch.object(bf.mk, "read_signal", return_value={"verdict": "중립", "score": 0}),
        ]


def run(market, state, minutes):
    ps = market.patch()
    for p in ps:
        p.start()
    try:
        return bf.check_alerts(CFG, state, now=T0 + dt.timedelta(minutes=minutes))
    finally:
        for p in ps:
            p.stop()


class NoRepeatTest(unittest.TestCase):
    def test_sustained_24h_move_alerts_once_then_on_new_step(self):
        m, st = Market(), {"alerts": {}}
        m.coins["ZECUSDT"] = snap(12.9)
        sent = []
        for i in range(12):                       # 15분마다 3시간 동안 +12.9% 유지
            sent += run(m, st, i * 15)
        self.assertEqual([f["level"] for f in sent], [10])
        m.coins["ZECUSDT"] = snap(16.0)           # 15% 계단을 새로 밟으면 한 번 더
        self.assertEqual([f["level"] for f in run(m, st, 200)], [15])
        m.coins["ZECUSDT"] = snap(14.0)           # 조금 식은 건 조용히
        self.assertEqual(run(m, st, 215), [])

    def test_resets_after_cooling_down(self):
        m, st = Market(), {"alerts": {}}
        m.coins["ZECUSDT"] = snap(8.0)
        self.assertEqual(len(run(m, st, 0)), 1)
        m.coins["ZECUSDT"] = snap(2.0)            # 기준의 60% 아래로 식음 → 초기화
        self.assertEqual(run(m, st, 15), [])
        m.coins["ZECUSDT"] = snap(7.5)            # 새 움직임은 다시 알림
        self.assertEqual(len(run(m, st, 30)), 1)

    def test_direction_flip_is_new_move(self):
        m, st = Market(), {"alerts": {}}
        m.coins["ZECUSDT"] = snap(9.0)
        self.assertEqual(run(m, st, 0)[0]["hit"], "급등")
        m.coins["ZECUSDT"] = snap(-9.0)
        self.assertEqual(run(m, st, 15)[0]["hit"], "급락")

    def test_1h_spike_uses_cooldown(self):
        m, st = Market(), {"alerts": {}}
        m.coins["ZECUSDT"] = snap(2.0, ch1=4.0)
        self.assertTrue(run(m, st, 0)[0]["fast"])
        self.assertEqual(run(m, st, 30), [])      # 90분 안에는 다시 안 울림
        self.assertEqual(len(run(m, st, 95)), 1)

    def test_btc_relative(self):
        m, st = Market(), {"alerts": {}}
        m.coins["ZECUSDT"] = snap(12.0)
        f = run(m, st, 0)[0]
        self.assertEqual(f["btc_ch24"], 0.5)
        self.assertIn("지캐시만 따로", bf.relative_line(f))
        f["btc_ch24"] = 9.0
        self.assertIn("시장 전체", bf.relative_line(f))

    def test_stock_only_in_regular_session_once_per_day(self):
        m, st = Market(), {"alerts": {}}
        base = {"price": 164.5, "change_day": -4.4, "currency": "USD", "chart_1d": None}
        m.stock = dict(base, market_state="CLOSED", session_date="2026-09-16")
        self.assertEqual(run(m, st, 0), [])       # 장 마감 후 지난 거래일 숫자는 무시
        m.stock = dict(base, market_state="REGULAR", session_date="2026-09-17")
        self.assertEqual(len(run(m, st, 15)), 1)
        self.assertEqual(run(m, st, 30), [])
        self.assertEqual(run(m, st, 120), [])
        m.stock = dict(base, change_day=-7.2, market_state="REGULAR", session_date="2026-09-17")
        self.assertEqual(run(m, st, 135)[0]["level"], 7)
        m.stock = dict(base, market_state="REGULAR", session_date="2026-09-18")
        self.assertEqual(len(run(m, st, 60 * 24)), 1)  # 다음 거래일은 새로


class AlertTextTest(unittest.TestCase):
    def fired(self, news):
        f = {"kind": "coin", "name": "지캐시", "symbol": "ZECUSDT", "hit": "급등", "icon": "🚀",
             "price": "$105.00", "price_raw": 105.0, "ch1": 0.4, "ch24": 12.9, "fast": False, "level": 10,
             "btc_ch24": 0.8, "vol_ratio": 1.0, "verdict": "강세", "support": 80.0, "resistance": 110.0}
        if news is not False:
            f["news"] = news
        return [f]

    def test_news_line_and_link(self):
        n = {"title": "지캐시 23% 껑충", "url": "https://n.example/a", "source": "데일리안", "age_h": 2.4}
        text = bf.build_alert(CFG, self.fired(n))[0]
        self.assertIn("<https://n.example/a|지캐시 23% 껑충>", text)
        self.assertIn("2시간 전", text)
        self.assertIn("10% 선을 새로 넘었습니다", text)
        self.assertIn("저항선", text)
        self.assertIn("4.8% 남았습니다", text)

    def test_no_news_warns(self):
        self.assertIn("뉴스는 보이지 않습니다", bf.build_alert(CFG, self.fired(None))[0])
        self.assertNotIn("📰", bf.build_alert(CFG, self.fired(False))[0])

    def test_attach_news_survives_errors(self):
        f = self.fired(False)
        bf.attach_news(f, lookup=mock.Mock(side_effect=RuntimeError("x")))
        self.assertIsNone(f[0]["news"])


class LookupTest(unittest.TestCase):
    def test_pick_prefers_reason_titles_and_skips_noise(self):
        items = [("지캐시 가격 전망: 2천달러 간다", "u1", "A", 0.5),
                 ("비트코인 숨고르기", "u2", "B", 0.2),
                 ("지캐시 블록 시간 단축 투표", "u3", "C", 1.0),
                 ("지캐시 23% 껑충…업그레이드 기대 때문", "u4", "D", 3.0),
                 ("지캐시 급등 이유", "u5", "E", 30.0)]
        self.assertEqual(nl.pick(items, "지캐시")["url"], "u4")
        self.assertIsNone(nl.pick(items[:2], "지캐시"))

    def test_headline_swallows_errors(self):
        self.assertIsNone(nl.headline("지캐시", get=mock.Mock(side_effect=OSError("down"))))


if __name__ == "__main__":
    unittest.main()
