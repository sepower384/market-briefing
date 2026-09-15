# -*- coding: utf-8 -*-
"""
말투(합니다체)·용어 풀이 테스트.
가짜 데이터로 브리핑/알림의 모든 분기(과열·과매도·크로스·거래량·도미넌스 증감·장상태 등)를 태워서
'해요/이에요/어요' 체 어미가 하나도 남지 않았는지, 용어 설명이 한 번만 붙는지 확인한다.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import briefing as bf  # noqa: E402
import telegram_sender as tg  # noqa: E402
from glossary import Explainer, GLOSSARY, josa  # noqa: E402

CFG = {"briefing": {"timezone_offset_hours": 9, "show_ta": True}}

# 해요체 어미: 문장/절 끝(공백·구두점·마크업·줄끝 앞)의 ~요
HAEYO_RE = re.compile(r"(해요|이에요|예요|에요|어요|아요|워요|와요|돼요|세요|네요|게요|래요|줘요|봐요|져요|나요|까요|죠|군요|거든요|는데요|걸요)"
                      r"(?=[\s.,!?~)\]*_'\"]|$)")
YO_END_RE = re.compile(r"([가-힣])요(?=[\s.,!?~)\]*_'\"]|$)")
YO_WHITELIST = {"주요", "필요", "중요", "수요", "요요", "개요", "강요", "월요", "화요", "수요", "목요", "금요", "토요", "일요"}


def haeyo_left(text):
    bad = [m.group(0) for m in HAEYO_RE.finditer(text)]
    for m in YO_END_RE.finditer(text):
        word = text[max(0, m.start() - 1):m.end()]
        if word not in YO_WHITELIST:
            bad.append(word)
    return bad


def ta(rsi=50, above=True, cross=None, vol=1.0, price=100.0):
    ma = price * (0.9 if above else 1.1)
    macd = {"hist": 1, "prev_hist": 1}
    if cross == "golden":
        macd = {"hist": 1, "prev_hist": -1}
    elif cross == "dead":
        macd = {"hist": -1, "prev_hist": 1}
    return {"rsi_1d": rsi, "ma50": ma, "ma200": ma, "macd_1d": macd, "bb_1h": {"pct_b": 50},
            "vol_ratio_1h": vol, "support": price * 0.95, "resistance": price * 1.05}


def coin(name, sym, ch24, **kw):
    price = kw.pop("price", 100.0)
    return {"name": name, "symbol": sym, "price": price, "change_24h": ch24, "change_1h": 0.3,
            "change_7d": 2.0, "ta": ta(price=price, **kw)}


def stock(name, ticker, ch, rsi=50, above=True):
    return {"name": name, "ticker": ticker, "price": 100.0, "change_day": ch, "currency": "USD",
            "ta": {"rsi_1d": rsi, "ma200": 90.0 if above else 110.0}}


def fake_data(variant):
    if variant == 1:
        # 설명 힌트는 코인당 2개까지라, 분기마다 코인을 나눠 모든 문장을 한 번씩 태운다
        coins = [coin("비트코인", "BTCUSDT", 1.2, rsi=50, above=True, cross="golden", vol=2.5, price=115000),
                 coin("이더리움", "ETHUSDT", -3.0, rsi=25, above=False, cross="dead"),
                 coin("솔라나", "SOLUSDT", 6.0, rsi=75, above=True),
                 coin("도지", "DOGEUSDT", -1.0, rsi=50, above=True, cross="dead"),
                 dict(coin("에이다", "ADAUSDT", 0.02, rsi=50, vol=3.0),
                      ta=dict(ta(rsi=50, vol=3.0), ma200=None, macd_1d=None)),  # 거래량 문장 + 제자리 분기
                 coin("리플", "XRPUSDT", 4.0, above=False),  # 오르는데 큰 흐름은 약세 → against
                 {"name": "지캐시", "symbol": "ZECUSDT", "error": "시세 조회 실패"}]
        dom = {"btc": 58.1, "eth": 13.0, "usdt": 6.5, "total_mcap_usd": 3.9e12, "total_mcap_change_24h": 1.1}
        us = [stock("S&P500", "^GSPC", 0.8), stock("나스닥", "^IXIC", 1.1),
              stock("엔비디아", "NVDA", 3.5, rsi=75), stock("테슬라", "TSLA", -2.0, rsi=25),
              stock("애플", "AAPL", -0.5, above=False)]
        kr = [stock("코스피", "^KS11", 0.1), stock("삼성전자", "005930.KS", -1.0, above=False)]
        return {"coins": coins, "dom": dom, "us": us, "kr": kr, "errors": ["지캐시: 시세 조회 실패 http_err"]}
    coins = [coin("비트코인", "BTCUSDT", -2.0, rsi=50, above=True),  # 내리는데 큰 흐름은 강세 → against
             coin("이더리움", "ETHUSDT", 1.0), coin("리플", "XRPUSDT", 2.0)]
    dom = {"btc": 55.0, "eth": 14.0, "usdt": 4.0, "total_mcap_usd": 3.5e12, "total_mcap_change_24h": -0.4}
    us = [stock("S&P500", "^GSPC", -1.2)]
    kr = [stock("코스피", "^KS11", 0.9)]
    return {"coins": coins, "dom": dom, "us": us, "kr": kr, "errors": []}


FIRED = [
    {"kind": "coin", "name": "리플", "symbol": "XRPUSDT", "hit": "급등", "icon": "🚀", "price": "$3.12",
     "ch1": 3.4, "ch24": 8.1, "vol_ratio": 2.3, "verdict": "약강세", "support": 2.9, "resistance": 3.3},
    {"kind": "coin", "name": "지캐시", "symbol": "ZECUSDT", "hit": "급락", "icon": "🚨", "price": "$40.00",
     "ch1": None, "ch24": -9.0, "vol_ratio": None, "verdict": "판단보류", "support": None, "resistance": None},
    {"kind": "dominance", "icon": "🟢", "btc": 57.2, "dd": -0.62, "total": 3.8e12},
] + [{"kind": "stock", "name": "종목%s" % s, "ticker": "T%s" % s, "hit": "급락", "icon": "🚨", "price": "$10.00",
      "ch": -4.2, "market_state": s} for s in ("REGULAR", "CLOSED", "PRE", "POST", "PREPRE", "POSTPOST", "")]


def all_messages():
    out = []
    for v in (1, 2):
        for dd in (-0.8, 0.8, 0.1, None):
            out.append(bf.build_briefing(CFG, fake_data(v), dd)[0])
    out.append(bf.build_alert(CFG, FIRED)[0])
    out.append(bf.build_alert(CFG, FIRED[:1], note="미리보기 샘플: 지금은 기준을 넘은 종목이 없어 가장 크게 움직인 코인으로 만들었습니다.")[0])
    return out


class ToneTest(unittest.TestCase):
    def test_detector_catches_haeyo(self):
        for s in ("올랐어요.", "뚜렷해요", "보합이에요", "제자리예요.", "확인해보세요.", "알려드려요"):
            self.assertTrue(haeyo_left(s), s)
        for s in ("올랐습니다.", "주요 지표입니다", "필요 없습니다", "중요."):
            self.assertFalse(haeyo_left(s), s)

    def test_no_haeyo_in_messages(self):
        for text in all_messages():
            self.assertEqual(haeyo_left(text), [], text)
            self.assertEqual(haeyo_left(tg.slack_to_html(text)), [])

    def test_static_phrases(self):
        for d in (bf.VERDICT_KR, bf.MARKET_STATE_KR):
            for v in d.values():
                self.assertEqual(haeyo_left(v), [], v)
                self.assertTrue(v.endswith("니다"), v)

    def test_sentences_end_formally(self):
        """'👉' 설명 줄과 요약 줄은 합니다체(…니다.)로 끝나야 한다."""
        for text in all_messages():
            for line in text.split("\n"):
                s = line.strip()
                if s.startswith("👉") or (s.startswith("•") and "*" not in s[:3] and ":" not in s):
                    self.assertRegex(re.sub(r"[_*.!]+$", "", s), r"니다$", s)

    def test_no_slack_emoji_codes_in_telegram(self):
        for text in all_messages():
            h = tg.slack_to_html(text)
            self.assertIsNone(re.search(r":[a-z0-9_+\-]*[a-z][a-z0-9_+\-]*:", h), h)
            self.assertNotIn("*", re.sub(r"<[^>]+>", "", h).replace("×", ""))  # 굵게 표시 누락 없음
            for c in tg.split_html(h):
                self.assertLessEqual(tg.tg_len(c), tg.TEXT_LIMIT)


class GlossaryTest(unittest.TestCase):
    def test_explain_once(self):
        ex = Explainer()
        self.assertEqual(ex.t("도미넌스"), "도미넌스(%s)" % GLOSSARY["도미넌스"])
        self.assertEqual(ex.t("도미넌스"), "도미넌스")
        self.assertEqual(ex.t("%p", "0.52%p"), "0.52%%p(%s)" % GLOSSARY["%p"])
        self.assertEqual(ex.t("없는용어"), "없는용어")

    def test_each_term_explained_once_per_message(self):
        for text in all_messages():
            for term, desc in GLOSSARY.items():
                self.assertLessEqual(text.count("(%s)" % desc), 1, "%s 중복 설명" % term)

    def test_key_terms_explained(self):
        text = bf.build_briefing(CFG, fake_data(1), -0.8)[0]
        for term in ("도미넌스", "RSI", "200일선", "알트코인", "스테이블코인", "시가총액", "지지선", "저항선", "골든크로스"):
            self.assertIn("%s(%s)" % (term, GLOSSARY[term]), text)
        alert = bf.build_alert(CFG, FIRED)[0]
        for term in ("거래량", "도미넌스", "정규장", "프리마켓", "애프터마켓"):
            self.assertIn("(%s)" % GLOSSARY[term], alert)

    def test_josa(self):
        self.assertEqual(josa("비트코인", "은/는"), "비트코인은")
        self.assertEqual(josa("리플", "은/는"), "리플은")
        self.assertEqual(josa("이더리움", "이/가"), "이더리움이")
        self.assertEqual(josa("나스닥", "은/는"), "나스닥은")
        self.assertEqual(josa("코스피", "은/는"), "코스피는")
        self.assertEqual(josa("S&P500", "은/는"), "S&P500은")


class HeaderTest(unittest.TestCase):
    def test_titles_and_bot(self):
        b = bf.build_briefing(CFG, fake_data(1), None)[0]
        self.assertTrue(b.startswith("📊 *세력의 시장 보고서*"))
        self.assertIn("세력의 비서실장", b)
        for sec in ("*📌 한눈에 보기*", "*━━ 🪙 코인 ━━*", "*━━ 🧲 돈이 어디로 몰리나 ━━*", "*━━ 🇺🇸 미국 증시 ━━*"):
            self.assertIn(sec, b)
        a = bf.build_alert(CFG, FIRED)[0]
        self.assertTrue(a.startswith("⚡ *세력의 레이더망*"))


if __name__ == "__main__":
    unittest.main()
