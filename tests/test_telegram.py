# -*- coding: utf-8 -*-
"""텔레그램 변환(이모지·이스케이프·마크업)·분할·전송(429/사진 실패/설정 없음)·토큰 선택 테스트."""
import os
import re
import sys
import unittest
from html.parser import HTMLParser
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import telegram_sender as tg  # noqa: E402
import charts  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SLACK_CODE_RE = re.compile(r":[a-z0-9_+\-]*[a-z][a-z0-9_+\-]*:")


class TagBalance(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.stack, self.ok, self.text = [], True, []

    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            self.ok = False

    def handle_data(self, data):
        self.text.append(data)


def balanced(s):
    p = TagBalance()
    p.feed(s)
    p.close()
    return p.ok and not p.stack


def plain(s):
    p = TagBalance()
    p.feed(s)
    p.close()
    return "".join(p.text)


class FakeResp(object):
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, str(body)

    def json(self):
        return self._body


class ConvertTest(unittest.TestCase):
    def test_markup(self):
        out = tg.slack_to_html("*굵게* _기울임_ `코드` ~취소~ <https://a.com/x?y=1&z=2|링크>")
        self.assertEqual(out, '<b>굵게</b> <i>기울임</i> <code>코드</code> <s>취소</s> '
                              '<a href="https://a.com/x?y=1&amp;z=2">링크</a>')

    def test_bold_followed_by_korean_particle(self):
        self.assertEqual(tg.slack_to_html("*비트코인*은 *58.1%*입니다."),
                         "<b>비트코인</b>은 <b>58.1%</b>입니다.")

    def test_escape(self):
        out = tg.slack_to_html("S&P500 < 5000 > 4000 <b>")
        self.assertEqual(out, "S&amp;P500 &lt; 5000 &gt; 4000 &lt;b&gt;")

    def test_no_markup_inside_code_and_snake_case(self):
        self.assertEqual(tg.slack_to_html("`a*b*c` api_v3_klines 2*3*4"),
                         "<code>a*b*c</code> api_v3_klines 2*3*4")

    def test_emoji_map(self):
        out = tg.slack_to_html(":rocket: :point_right: :small_red_triangle: :small_red_triangle_down: 12:30:45")
        self.assertEqual(out, "🚀 👉 🔺 🔻 12:30:45")

    def test_every_slack_code_in_codebase_is_mapped(self):
        """저장소 파이썬/설정 파일에 등장하는 :code: 는 전부 매핑돼 있어야 한다."""
        found = set()
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "outbox", "tests")]
            for fn in files:
                if fn.endswith((".py", ".json", ".yml", ".ps1")):
                    with open(os.path.join(dirpath, fn), encoding="utf-8", errors="ignore") as f:
                        for m in SLACK_CODE_RE.finditer(f.read()):
                            code = m.group(0).strip(":")
                            if not re.fullmatch(r"[\d:]+", code):
                                found.add(code)
        found -= {"//127.0.0.1", "flag-us", "flag-kr"}  # 매핑 표 자체
        missing = [c for c in found if c not in tg.SLACK_EMOJI and not c.startswith("//")]
        self.assertEqual(missing, [], "매핑 누락: %s" % missing)


class SplitTest(unittest.TestCase):
    def test_short_stays_one(self):
        self.assertEqual(tg.split_html("<b>안녕</b>\n\n본문"), ["<b>안녕</b>\n\n본문"])

    def test_sections_split_at_blank_lines(self):
        sec = "<b>━━ 섹션 ━━</b>\n" + "\n".join("🔺 <b>코인%d</b> $1,234 &amp; 설명 문장입니다." % i for i in range(40))
        text = "\n\n".join([sec] * 8)
        chunks = tg.split_html(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(tg.tg_len(c), tg.TEXT_LIMIT)
            self.assertTrue(balanced(c), c[:80])
            self.assertTrue(c.startswith("<b>━━ 섹션 ━━</b>"))  # 섹션 경계에서 잘림
        self.assertEqual(re.sub(r"\s", "", "".join(plain(c) for c in chunks)),
                         re.sub(r"\s", "", plain(text)))

    def test_single_huge_line_keeps_tags_and_entities(self):
        line = "<b>" + ("가&amp;나 " * 3000) + "</b>"
        chunks = tg.split_html(line, limit=1000)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(tg.tg_len(c), 1000)
            self.assertTrue(balanced(c))
            self.assertNotRegex(c, r"&[a-z]*$")  # 엔티티 중간에서 안 잘림
            self.assertTrue(c.startswith("<b>") and c.endswith("</b>"))
        self.assertEqual("".join(plain(c) for c in chunks).replace(" ", ""),
                         plain(line).replace(" ", ""))

    def test_caption_clip(self):
        self.assertLessEqual(tg.tg_len(tg.clip_caption("가" * 3000)), tg.CAPTION_LIMIT)


class ConfigTest(unittest.TestCase):
    def test_missing_skips(self):
        self.assertIsNone(tg.stream_config("brief", {}))
        self.assertIsNone(tg.stream_config("brief", {"TELEGRAM_BOT_TOKEN": "t"}))
        ok, msg = tg.deliver("brief", "x", env={})
        self.assertIsNone(ok)

    def test_token_selection_per_stream(self):
        env = {"TELEGRAM_BOT_TOKEN": "shared", "TELEGRAM_BOT_TOKEN_BRIEF": "brief-bot",
               "TELEGRAM_CHAT_ID": "-1001", "TELEGRAM_TOPIC_BRIEF": "11", "TELEGRAM_TOPIC_WATCH": "22"}
        b, w = tg.stream_config("brief", env), tg.stream_config("watch", env)
        self.assertEqual(b["token"], "brief-bot")   # 스트림 전용 토큰 우선
        self.assertEqual(w["token"], "shared")      # 없으면 공용 토큰
        self.assertEqual((b["thread_id"], w["thread_id"]), (11, 22))
        env2 = {"TELEGRAM_BOT_TOKEN_WATCH": "watch-bot", "TELEGRAM_CHAT_ID": "-1001", "TELEGRAM_BOT_TOKEN_BRIEF": " "}
        self.assertEqual(tg.stream_config("watch", env2)["token"], "watch-bot")  # 공용 없이도 동작
        self.assertIsNone(tg.stream_config("brief", env2))                      # 공백 토큰 = 없음
        env3 = {"TELEGRAM_BOT_TOKEN": "shared", "TELEGRAM_CHAT_ID": "-1001"}
        self.assertIsNone(tg.stream_config("brief", env3)["thread_id"])        # 토픽 없으면 기본 스레드


class DeliverTest(unittest.TestCase):
    ENV = {"TELEGRAM_BOT_TOKEN": "shared", "TELEGRAM_BOT_TOKEN_WATCH": "watch-bot",
           "TELEGRAM_CHAT_ID": "-100123", "TELEGRAM_TOPIC_WATCH": "7"}

    def test_photo_then_chunks_with_payload(self):
        calls, sleeps = [], []

        def post(url, json=None, timeout=None):
            calls.append((url, json))
            return FakeResp(200, {"ok": True})

        text = "\n\n".join(["<b>섹션</b>\n" + "가" * 3000] * 2)
        ok, msg = tg.deliver("watch", text, "https://quickchart.io/chart?c=1", "⚡ <b>제목</b>",
                             env=self.ENV, post=post, sleep=sleeps.append)
        self.assertTrue(ok)
        self.assertTrue(calls[0][0].endswith("/botwatch-bot/sendPhoto"))
        self.assertEqual(calls[0][1]["message_thread_id"], 7)
        msgs = [c for c in calls if c[0].endswith("sendMessage")]
        self.assertEqual(len(msgs), 2)
        for _, p in msgs:
            self.assertEqual(p["parse_mode"], "HTML")
            self.assertIs(p["disable_web_page_preview"], True)
            self.assertEqual(p["message_thread_id"], 7)
            self.assertEqual(p["chat_id"], "-100123")
        self.assertIn(1, sleeps)  # 조각 사이 1초

    def test_429_retry_once(self):
        seq = [FakeResp(429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}),
               FakeResp(200, {"ok": True})]
        sleeps = []
        ok, _ = tg.deliver("watch", "본문", env=self.ENV, post=lambda *a, **k: seq.pop(0), sleep=sleeps.append)
        self.assertTrue(ok)
        self.assertEqual(sleeps, [3.0])

    def test_429_twice_fails_without_exception(self):
        r = FakeResp(429, {"ok": False, "parameters": {"retry_after": 1}})
        ok, msg = tg.deliver("watch", "본문", env=self.ENV, post=lambda *a, **k: r, sleep=lambda s: None)
        self.assertFalse(ok)

    def test_photo_failure_does_not_block_text(self):
        def post(url, json=None, timeout=None):
            if url.endswith("sendPhoto"):
                raise TimeoutError("photo timeout")
            return FakeResp(200, {"ok": True})
        ok, msg = tg.deliver("watch", "본문", "https://x/y.png", "cap", env=self.ENV, post=post, sleep=lambda s: None)
        self.assertTrue(ok)
        self.assertIn("사진 생략", msg)


class ChartTest(unittest.TestCase):
    def test_url_short_and_points_capped(self):
        closes = [115000 + i * 37.5 for i in range(168)]
        times = [1757900000000 + i * 3600000 for i in range(168)]
        url = charts.line_chart_url(closes, times, title="BTC/USDT 7D", label_every=7)
        self.assertTrue(url.startswith("https://quickchart.io/chart?w=800&h=400"))
        self.assertLessEqual(len(url), 2000)
        import json
        cfg = json.loads(unquote(url.split("&c=", 1)[1]))
        self.assertLessEqual(len(cfg["data"]["datasets"][0]["data"]), 48)
        self.assertEqual(cfg["data"]["datasets"][0]["data"][-1], round(closes[-1]))
        ticks = cfg["options"]["scales"]["yAxes"][0]["ticks"]  # y축이 0부터 시작하지 않아야 흐름이 보인다
        self.assertGreater(ticks["min"], 100000)
        self.assertLessEqual(ticks["min"], min(closes))
        self.assertGreaterEqual(ticks["max"], max(closes))

    def test_too_little_data(self):
        self.assertIsNone(charts.line_chart_url([1.0]))


if __name__ == "__main__":
    unittest.main()
