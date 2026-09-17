# -*- coding: utf-8 -*-
"""
briefing.py - 3시간마다 정기 브리핑 + 급등/급락 즉시 알림 (슬랙 + 텔레그램 동시 발송).

사용법
  python briefing.py brief                    정기 브리핑 1회 발송 (슬랙 + 텔레그램)
  python briefing.py watch                    급등/급락 감시 1회 실행 (걸릴 때만 발송)
  python briefing.py test                     슬랙/텔레그램 연결만 테스트
  python briefing.py preview                  슬랙 안 보내고 화면에만 출력
  python briefing.py preview-telegram brief   실제 데이터로 텔레그램/슬랙 미리보기 파일 생성 (전송 X)
  python briefing.py preview-telegram watch   급등락 알림 미리보기 (걸린 게 없으면 샘플로 생성)
  python briefing.py status                   슬랙/텔레그램 설정·이력·로그 상태 점검
"""
import copy
import html
import io
import json
import os
import sys
import traceback
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market as mk
import slack_sender
import telegram_sender as tg
import charts
from glossary import Explainer, josa

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
LOG_PATH = os.path.join(BASE, "briefing.log")
OUTBOX_DIR = os.path.join(BASE, "outbox")

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]
BOT_NAME = "세력의 비서실장"
STREAM_TITLE = {"brief": "📊 세력의 시장 보고서", "watch": "⚡ 세력의 레이더망"}


# ------------------------------------------------------------------ 유틸
def log(msg):
    line = "[%s] %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        with io.open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def load_config():
    with io.open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    if os.path.exists(STATE_PATH):
        try:
            with io.open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"alerts": {}, "dominance_history": []}


def save_state(st):
    with io.open(STATE_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(st, ensure_ascii=False, indent=2))


def fmt_price(v, currency="USD"):
    if v is None:
        return "-"
    if currency == "KRW":
        return "{:,.0f}원".format(v)
    if v >= 1000:
        return "${:,.0f}".format(v)
    if v >= 1:
        return "${:,.2f}".format(v)
    return "${:,.4f}".format(v)


def fmt_pct(v):
    if v is None:
        return "-"
    return "{}{:.2f}%".format("+" if v >= 0 else "", v)


def arrow(v, big=5.0):
    if v is None:
        return "•"
    if v >= big:
        return "🚀"
    if v > 0:
        return "🔺"
    if v <= -big:
        return "💥"
    if v < 0:
        return "🔻"
    return "▪️"


def particle(word, pair):
    """단어 뒤에 붙일 조사만. particle('비트코인', '은/는') -> '은'"""
    return josa(word, pair)[len(word):]


# ------------------------------------------------------------------ 수집
def collect(cfg, with_ta=True):
    data = {"coins": [], "dom": None, "us": [], "kr": [], "errors": []}

    for c in cfg["coins"]:
        try:
            s = mk.coin_snapshot(c["symbol"], with_ta=with_ta)
            s["name"] = c["name"]
            s["emoji"] = c.get("emoji", "")
            data["coins"].append(s)
        except Exception as e:
            data["errors"].append("%s: %s" % (c["name"], e))

    try:
        data["dom"] = mk.dominance()
    except Exception as e:
        data["errors"].append("도미넌스: %s" % e)

    for key, lst in (("us", cfg["us_stocks"]), ("kr", cfg["kr_stocks"])):
        for s in lst:
            try:
                snap = mk.stock_snapshot(s["ticker"], with_ta=with_ta)
                snap["name"] = s["name"]
                data[key].append(snap)
            except Exception as e:
                data["errors"].append("%s: %s" % (s["name"], e))
    return data


# ------------------------------------------------------------------ 도미넌스 추적
def dominance_delta(state, dom, hours=24):
    """저장해 둔 이력과 비교해 BTC 도미넌스 변화(%p)를 구한다."""
    if not dom or dom.get("btc") is None:
        return None
    now = dt.datetime.utcnow()
    hist = state.setdefault("dominance_history", [])
    hist.append({"t": now.isoformat(), "btc": dom["btc"]})
    cutoff = now - dt.timedelta(days=8)
    state["dominance_history"] = [h for h in hist
                                  if dt.datetime.fromisoformat(h["t"]) > cutoff][-800:]

    target = now - dt.timedelta(hours=hours)
    past = None
    for h in state["dominance_history"]:
        t = dt.datetime.fromisoformat(h["t"])
        if t <= target:
            past = h
    if past is None:
        return None
    return dom["btc"] - past["btc"]


# ------------------------------------------------------------------ 사람 말로 풀기 (합니다체)
VERDICT_KR = {
    "강세": "상승 쪽이 뚜렷합니다",
    "약강세": "살짝 오르는 쪽으로 기울어 있습니다",
    "중립": "아직 방향이 정해지지 않았습니다",
    "약약세": "살짝 내리는 쪽으로 기울어 있습니다",
    "약세": "하락 쪽이 뚜렷합니다",
    "판단보류": "지표가 부족해 아직 판단하기 어렵습니다",
}

MARKET_STATE_KR = {
    "REGULAR": "지금은 {정규장}이 진행 중입니다",
    "CLOSED": "지금은 장이 마감된 상태입니다",
    "PRE": "지금은 {프리마켓} 시간입니다",
    "PREPRE": "지금은 {프리마켓} 시간입니다",
    "POST": "지금은 {애프터마켓} 시간입니다",
    "POSTPOST": "지금은 {애프터마켓} 시간입니다",
}


def verdict_kr(v):
    return VERDICT_KR.get(v, v)


def move_clause(v, unit="하루"):
    """문장 중간용: '하루 동안 1.2% 올랐고'"""
    if v is None:
        return "%s 변동은 확인되지 않았고" % unit
    if abs(v) < 0.05:
        return "%s 동안 거의 제자리였고" % unit
    return "%s 동안 %.1f%% %s" % (unit, abs(v), "올랐고" if v > 0 else "내렸고")


def coin_story(c, ex):
    """코인 한 개를 2~3줄 설명으로."""
    ta = c.get("ta") or {}
    price = c.get("price")
    sig = mk.read_signal(c)
    ch = c.get("change_24h") or 0
    # 오늘 움직임과 큰 흐름이 반대면 '잠깐'이라고 짚어 줘야 헷갈리지 않는다
    against = (ch < 0 and sig["score"] > 0) or (ch > 0 and sig["score"] < 0)
    verdict = verdict_kr(sig["verdict"])
    if against:
        first = "하루 동안 %.1f%% %s, 몇 주 단위 큰 흐름은 *%s*." % (
            abs(ch), "빠졌지만" if ch < 0 else "올랐지만", verdict)
    else:
        first = "%s, 큰 흐름은 *%s*." % (move_clause(c.get("change_24h")), verdict)
    out = ["     👉 " + first]

    hints = []
    if ta.get("ma200") and price:
        if price >= ta["ma200"]:
            hints.append("%s보다 위에 있어 큰 추세는 아직 살아 있는 것으로 보입니다." % ex.t("200일선"))
        else:
            hints.append("%s보다 아래에 있어 큰 추세는 약한 편입니다." % ex.t("200일선"))
    r = ta.get("rsi_1d")
    if r is not None and r >= 70:
        hints.append("%s가 %.0f로 70을 넘어 *과열* 구간으로 보입니다. 잠시 쉬어 갈 수 있습니다." % (ex.t("RSI"), r))
    elif r is not None and r <= 30:
        hints.append("%s가 %.0f로 30 아래라 *%s* 구간입니다. 반등이 나올 수 있습니다." % (
            ex.t("RSI"), r, ex.t("과매도")))
    macd = ta.get("macd_1d")
    if macd and macd["hist"] > 0 and macd["prev_hist"] <= 0:
        hints.append("%s가 막 나왔습니다." % ex.t("골든크로스"))
    elif macd and macd["hist"] < 0 and macd["prev_hist"] >= 0:
        hints.append("%s가 막 나왔습니다." % ex.t("데드크로스"))
    if ta.get("vol_ratio_1h") and ta["vol_ratio_1h"] >= 2:
        hints.append("최근 1시간 %s이 평소의 %.1f배로, 사람들이 몰렸습니다." % (ex.t("거래량"), ta["vol_ratio_1h"]))
    if hints:
        out.append("     👉 " + " ".join(hints[:2]))
    if ta.get("support") and ta.get("resistance"):
        out.append("     👉 %s은 %s, %s은 %s입니다." % (
            ex.t("지지선"), fmt_price(ta["support"]), ex.t("저항선"), fmt_price(ta["resistance"])))
    return out


def summary_lines(data, dom_delta, ex):
    """맨 위 '한눈에 보기' 3~4줄."""
    out = []
    btc = next((c for c in data["coins"] if not c.get("error") and "BTC" in str(c.get("symbol", "")).upper()), None)
    btc = btc or next((c for c in data["coins"] if not c.get("error")), None)
    if btc:
        sig = mk.read_signal(btc)
        out.append("• *%s*%s 지금 %s입니다. %s, 몇 주 단위 큰 흐름은 %s." % (
            btc["name"], particle(btc["name"], "은/는"), fmt_price(btc.get("price")),
            move_clause(btc.get("change_24h")), verdict_kr(sig["verdict"])))
    alts = [c for c in data["coins"] if not c.get("error") and c is not btc and c.get("change_24h") is not None]
    if alts:
        up = sum(1 for c in alts if c["change_24h"] > 0)
        best = max(alts, key=lambda c: c["change_24h"])
        worst = min(alts, key=lambda c: c["change_24h"])
        head = ("다른 코인 %d개가 모두 내렸습니다" % len(alts) if up == 0 else
                "다른 코인 %d개가 모두 올랐습니다" % len(alts) if up == len(alts) else
                "다른 코인 %d개 중 %d개가 올랐습니다" % (len(alts), up))
        out.append("• %s. 가장 선방한 코인은 %s(%s), 가장 약한 코인은 %s(%s)입니다." % (
            head, best["name"], fmt_pct(best["change_24h"]), worst["name"], fmt_pct(worst["change_24h"])))
    if dom_delta is not None:
        if dom_delta <= -0.5:
            out.append("• 비트코인 쏠림이 줄고 있습니다. %s으로 돈이 옮겨갈 수 있다는 신호로 보입니다."
                       % ex.t("알트코인"))
        elif dom_delta >= 0.5:
            out.append("• 돈이 비트코인으로 쏠리고 있습니다. 이럴 때는 보통 %s이 더 약한 편입니다."
                       % ex.t("알트코인"))
    for key, title in (("us", "미국 증시"), ("kr", "한국 증시")):
        idx = [s for s in data[key] if s["ticker"].startswith("^") and s.get("change_day") is not None]
        if idx:
            avg = sum(s["change_day"] for s in idx) / len(idx)
            mood = ("올랐습니다" if avg > 0.3 else "내렸습니다" if avg < -0.3
                    else "%s입니다" % ex.t("보합"))
            out.append("• %s는 %s. %s입니다." % (
                title, mood, ", ".join("%s %s" % (s["name"], fmt_pct(s["change_day"])) for s in idx)))
    return out


# ------------------------------------------------------------------ 브리핑 본문
def build_briefing(cfg, data, dom_delta):
    ex = Explainer()
    kst = mk.now_kst(cfg["briefing"].get("timezone_offset_hours", 9))
    head = "📊 *세력의 시장 보고서* — %s(%s) %s" % (
        kst.strftime("%m월 %d일"), WEEKDAY_KR[kst.weekday()], kst.strftime("%H:%M"))

    lines = [head, "_%s이 3시간마다 정리해 드리는 시장 브리핑입니다._" % BOT_NAME, ""]
    summ = summary_lines(data, dom_delta, ex)
    if summ:
        lines.append("*📌 한눈에 보기*")
        lines += summ
        lines.append("")

    # ---------------- 코인
    lines.append("*━━ 🪙 코인 ━━*")
    for c in data["coins"]:
        if c.get("error"):
            lines.append("• %s: 이번에는 시세를 가져오지 못했습니다." % c["name"])
            lines.append("")
            continue
        lines.append("%s *%s* %s   (하루 %s · 1시간 %s · 일주일 %s)" % (
            arrow(c.get("change_24h")), c["name"], fmt_price(c.get("price")),
            fmt_pct(c.get("change_24h")), fmt_pct(c.get("change_1h")),
            fmt_pct(c.get("change_7d"))))
        if cfg["briefing"].get("show_ta", True) and c.get("ta"):
            lines += coin_story(c, ex)
        lines.append("")

    # ---------------- 도미넌스
    if data["dom"]:
        d = data["dom"]
        lines.append("*━━ 🧲 돈이 어디로 몰리나 ━━*")
        dd = ""
        if dom_delta is not None:
            dd = " 하루 전보다 %s %s." % (ex.t("%p", "%.2f%%p" % abs(dom_delta)),
                                     "늘었습니다" if dom_delta >= 0 else "줄었습니다")
        lines.append("• 비트코인 %s는 *%.1f%%*입니다.%s" % (ex.t("도미넌스"), d["btc"], dd))
        lines.append("• 이더리움 비중은 %.1f%%, 달러 %s USDT 비중은 %.1f%%입니다." % (
            d["eth"] or 0, ex.t("스테이블코인"), d["usdt"] or 0))
        lines.append("• 코인 시장 전체 %s은 약 %.2f조 달러이며, 하루 동안 %s 움직였습니다." % (
            ex.t("시가총액"), d["total_mcap_usd"] / 1e12, fmt_pct(d.get("total_mcap_change_24h"))))
        if dom_delta is not None:
            if dom_delta <= -0.5:
                lines.append("  👉 🟢 비트코인 비중이 줄고 있습니다. %s으로 돈이 옮겨가는 중일 수 있습니다."
                             % ex.t("알트코인"))
            elif dom_delta >= 0.5:
                lines.append("  👉 🔴 비트코인 비중이 늘고 있습니다. %s에서 돈이 빠져 비트코인으로 피신하는 중으로 보입니다."
                             % ex.t("알트코인"))
        if (d["usdt"] or 0) >= 6:
            lines.append("  👉 스테이블코인 비중이 높다는 것은 현금을 들고 %s하는 돈이 많다는 뜻입니다."
                         % ex.t("관망"))
        lines.append("")

    # ---------------- 주식
    for key, title in (("us", "🇺🇸 미국 증시"), ("kr", "🇰🇷 한국 증시")):
        rows = [s for s in data[key] if not s.get("error") and s.get("change_day") is not None]
        if not rows:
            continue
        rows.sort(key=lambda x: x["change_day"], reverse=True)
        up = sum(1 for s in rows if s["change_day"] > 0)
        lines.append("*━━ %s ━━*" % title)
        lines.append("_%d개 중 %d개가 오르고 %d개가 내렸습니다._" % (len(rows), up, len(rows) - up))
        weak = hot = cold = 0
        for s in rows:
            cur = s.get("currency", "USD")
            is_index = s["ticker"].startswith("^")
            extra = ""
            ta = s.get("ta")
            if ta and s.get("price"):
                if ta.get("rsi_1d") and ta["rsi_1d"] >= 70:
                    extra = " · 🔥 단기 과열"
                    hot += 1
                elif ta.get("rsi_1d") and ta["rsi_1d"] <= 30:
                    extra = " · 🧊 많이 빠진 구간"
                    cold += 1
                elif ta.get("ma200") and s["price"] < ta["ma200"]:
                    extra = " · 장기추세 약함"
                    weak += 1
            shown = ("{:,.2f}p".format(s["price"]) if is_index and s.get("price")
                     else fmt_price(s.get("price"), cur))
            lines.append("%s %s %s  `%s`%s" % (
                arrow(s["change_day"], 3.0), s["name"], shown,
                fmt_pct(s["change_day"]), extra))
        if hot or cold:
            parts = []
            if hot:
                parts.append("'🔥 단기 과열'은 %s가 70 이상" % ex.t("RSI"))
            if cold:
                parts.append("'🧊 많이 빠진 구간'은 %s가 30 이하" % ex.t("RSI"))
            tail = "이라는" if parts[-1].endswith("이상") else "라는"
            lines.append("  👉 %s%s 뜻입니다." % (", ".join(parts), tail))
        if weak:
            lines.append("  👉 '장기추세 약함'은 %s보다 아래에 있어 큰 흐름이 아직 하락 쪽이라는 뜻입니다."
                         % ex.t("200일선"))
        lines.append("")

    if data["errors"]:
        names = []
        for e in data["errors"]:
            n = e.split(":", 1)[0].strip()
            if n and n not in names:
                names.append(n)
        lines.append("_이번에는 %s 데이터를 가져오지 못했습니다._" % ", ".join(names[:5]))
    lines.append("_투자 판단을 돕는 참고용 요약이며, 매매 신호는 아닙니다._")

    text = "\n".join(lines)
    blocks = to_blocks(text)
    return text, blocks


def to_blocks(text, limit=2900):
    """긴 텍스트를 슬랙 섹션 블록들로 자른다."""
    blocks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit:
            blocks.append({"type": "section",
                           "text": {"type": "mrkdwn", "text": buf or " "}})
            buf = ""
        buf += line + "\n"
    if buf.strip():
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": buf}})
    return blocks[:45]


def brief_photo(data):
    """정기 브리핑 사진: 비트코인 최근 7일 가격 선차트. (url, caption) 또는 (None, None)"""
    btc = next((c for c in data["coins"] if "BTC" in str(c.get("symbol", "")).upper()
                and c.get("chart_1h")), None)
    if not btc:
        return None, None
    ch = btc["chart_1h"]
    url = charts.line_chart_url(ch["close"], ch["time"], title="BTC/USDT 7D", label_every=7, label_fmt="%-m/%-d")
    if not url:
        return None, None
    caption = "📊 <b>%s 최근 7일 가격 흐름</b>\n%s (일주일 %s)" % (
        tg.esc(btc["name"]), tg.esc(fmt_price(btc.get("price"))), tg.esc(fmt_pct(btc.get("change_7d"))))
    return url, caption


# ------------------------------------------------------------------ 급등락 감시
def ladder(base, steps):
    """알림 단계: 기준값 + 그보다 큰 계단들. ladder(7, (10, 15)) -> [7, 10, 15]"""
    return sorted(set([base] + [s for s in steps if s > base]))


COIN_STEPS = (10, 15, 20, 30, 50, 100)
STOCK_STEPS = (7, 10, 15, 20, 30)
INDEX_STEPS = (3, 4, 5, 7, 10)
DOM_STEPS = (1.0, 1.5, 2.0, 3.0)


class AlertMemory(object):
    """같은 움직임을 한 번만 알리는 기억장치 (state.json 안에 저장).

    - escalate: '24시간 +7%' 처럼 계속 유지되는 값은 쿨다운으로 막으면 하루 종일 반복된다.
      그래서 **새 계단(7→10→15%)에 올라설 때만** 알리고, 기준의 60% 아래로 식으면 초기화한다.
    - cooldown: '1시간 +3%' 처럼 금방 사라지는 값은 쿨다운으로 충분하다.
    """

    def __init__(self, state, now=None):
        self.now = now or dt.datetime.utcnow()
        self.levels = state.setdefault("levels", {})
        self.alerts = state.setdefault("alerts", {})
        # 오래된 기록 정리(거래일 키가 무한히 쌓이지 않도록)
        for k in list(self.levels):
            try:
                if self.now - dt.datetime.fromisoformat(self.levels[k]["t"]) > dt.timedelta(days=3):
                    del self.levels[k]
            except Exception:
                del self.levels[k]

    def escalate(self, key, value, steps, reset_ratio=0.6, expire_h=None):
        """value(>=0)가 새 계단에 닿았으면 그 계단을, 아니면 None."""
        rec = self.levels.get(key)
        if rec and expire_h and self.now - dt.datetime.fromisoformat(rec["t"]) > dt.timedelta(hours=expire_h):
            rec = None
            self.levels.pop(key, None)
        if value < steps[0] * reset_ratio:
            self.levels.pop(key, None)
            return None
        hits = [s for s in steps if s <= value]
        if not hits or (rec and rec["lvl"] >= hits[-1]):
            return None
        reached = hits[-1]
        self.levels[key] = {"lvl": reached, "t": self.now.isoformat()}
        return reached

    def cooldown(self, key, minutes):
        last = self.alerts.get(key)
        if last and self.now - dt.datetime.fromisoformat(last) < dt.timedelta(minutes=minutes):
            return False
        self.alerts[key] = self.now.isoformat()
        return True


def _both_ways(mem, key, signed, steps, **kw):
    """상승/하락 계단을 따로 관리. 반대 방향 기억은 자동으로 식는다."""
    up = mem.escalate(key + ":up", max(signed, 0), steps, **kw)
    down = mem.escalate(key + ":down", max(-signed, 0), steps, **kw)
    return up or down


def check_alerts(cfg, state, now=None):
    """새로 알릴 만한 움직임만 골라낸다. 같은 움직임 반복 알림은 AlertMemory 가 막는다."""
    a = cfg["alerts"]
    mem = AlertMemory(state, now)
    cool_1h = a.get("cooldown_minutes", 90)
    fired = []

    # 코인 — 비트코인 등락을 기준으로 '혼자 움직였는지'도 같이 본다
    snaps = []
    for c in cfg["coins"]:
        try:
            s = mk.coin_snapshot(c["symbol"], with_ta=True)
        except Exception:
            continue
        if not s.get("error"):
            snaps.append((c, s))
    btc = next((s for c, s in snaps if c["symbol"].upper().startswith("BTC")), None)

    for c, s in snaps:
        ch1, ch24 = s.get("change_1h"), s.get("change_24h")
        lvl = None
        if ch24 is not None:
            lvl = _both_ways(mem, "coin24:%s" % c["symbol"], ch24,
                             ladder(a["coin_24h_pct"], COIN_STEPS), expire_h=24)
        fast = None
        if ch1 is not None and abs(ch1) >= a["coin_1h_pct"]:
            if mem.cooldown("coin1h:%s:%s" % (c["symbol"], "up" if ch1 > 0 else "down"), cool_1h):
                fast = ch1
        if lvl is None and fast is None:
            continue
        lead = fast if fast is not None else ch24
        ta = s.get("ta") or {}
        is_btc = s is btc
        fired.append({
            "kind": "coin", "name": c["name"], "symbol": c["symbol"],
            "hit": "급등" if lead > 0 else "급락", "icon": "🚀" if lead > 0 else "🚨",
            "price": fmt_price(s["price"]), "price_raw": s["price"],
            "ch1": ch1, "ch24": ch24, "fast": fast is not None, "level": lvl,
            "btc_ch24": None if (is_btc or not btc) else btc.get("change_24h"),
            "vol_ratio": ta.get("vol_ratio_1h"),
            "verdict": mk.read_signal(s)["verdict"],
            "support": ta.get("support"), "resistance": ta.get("resistance"),
            "chart": s.get("chart_1h"),
        })

    # 도미넌스 — 줄어드는 계단만 (0.5 → 1.0 → 1.5%p)
    try:
        dom = mk.dominance()
        dd = dominance_delta(state, dom, 24)
        if dd is not None:
            steps = ladder(abs(a["dominance_drop_24h_pp"]), DOM_STEPS)
            if mem.escalate("dom:down", max(-dd, 0), steps, reset_ratio=0.5, expire_h=24):
                fired.append({"kind": "dominance", "icon": "🟢", "btc": dom["btc"], "dd": dd,
                              "total": dom["total_mcap_usd"]})
    except Exception:
        pass

    # 주식 — 정규장 중에만. 장이 닫힌 뒤의 '오늘 등락'은 지난 거래일 숫자라 다시 알릴 이유가 없다.
    for lst in (cfg["us_stocks"], cfg["kr_stocks"]):
        for s in lst:
            q = mk.stock_quick(s["ticker"])
            if not q or q.get("change_day") is None:
                continue
            if (q.get("market_state") or "").upper() != "REGULAR":
                continue
            is_index = s["ticker"].startswith("^")
            thr = a["index_day_pct"] if is_index else a["stock_day_pct"]
            ch = q["change_day"]
            steps = ladder(thr, INDEX_STEPS if is_index else STOCK_STEPS)
            lvl = _both_ways(mem, "stock:%s:%s" % (s["ticker"], q.get("session_date") or ""), ch, steps)
            if not lvl:
                continue
            shown = ("{:,.2f}p".format(q["price"]) if is_index
                     else fmt_price(q["price"], q.get("currency", "USD")))
            fired.append({
                "kind": "stock", "name": s["name"], "ticker": s["ticker"],
                "hit": "급등" if ch > 0 else "급락", "icon": "🚨" if ch < 0 else "🚀",
                "price": shown, "ch": ch, "level": lvl, "market_state": q.get("market_state", ""),
                "chart": q.get("chart_1d"),
            })
    return fired


def attach_news(fired, lookup=None, limit=4):
    """코인·주식 알림마다 '왜 움직였나' 뉴스 1건을 붙인다(실패해도 알림은 나간다)."""
    if lookup is None:
        import news_lookup
        lookup = news_lookup.headline
    for f in [f for f in fired if f.get("kind") in ("coin", "stock")][:limit]:
        try:
            q = f["name"] + (" 주가" if f["kind"] == "stock" and not f["ticker"].startswith("^") else "")
            f["news"] = lookup(f["name"], q)
        except Exception:
            f["news"] = None
    return fired


def build_alert(cfg, fired, note=None):
    ex = Explainer()
    kst = mk.now_kst(cfg["briefing"].get("timezone_offset_hours", 9))
    lines = ["⚡ *세력의 레이더망* — %s" % kst.strftime("%m월 %d일 %H:%M"),
             "_평소보다 큰 가격 변동이 감지되어 %s이 바로 알려 드립니다._" % BOT_NAME]
    if note:
        lines.append("_%s_" % note)
    lines.append("")
    for f in fired:
        kind = f.get("kind")
        if kind == "coin":
            lines.append("%s *%s %s %s*" % (f["icon"], f["name"], f["hit"], f["price"]))
            lines.append("     👉 " + coin_move_line(f))
            rel = relative_line(f)
            if rel:
                lines.append("     👉 " + rel)
            if f.get("vol_ratio") and f["vol_ratio"] >= 2:
                lines.append("     👉 %s도 평소의 %.1f배로 늘었습니다. 실제로 사고파는 사람이 몰렸다는 뜻입니다."
                             % (ex.t("거래량"), f["vol_ratio"]))
            lvl = level_line(f, ex)
            lines.append("     👉 큰 흐름은 %s.%s" % (verdict_kr(f.get("verdict")), (" " + lvl) if lvl else ""))
            lines += news_lines(f)
        elif kind == "dominance":
            lines.append("%s *비트코인 비중 하락 %.2f%%*" % (f["icon"], f["btc"]))
            lines.append("     👉 %s가 24시간 동안 %s 줄었습니다. %s으로 돈이 옮겨가는 신호일 수 있습니다." % (
                ex.t("도미넌스"), ex.t("%p", "%.2f%%p" % abs(f["dd"])), ex.t("알트코인")))
            lines.append("     👉 참고로 코인 시장 전체 %s은 약 %.2f조 달러입니다." % (
                ex.t("시가총액"), f["total"] / 1e12))
        elif kind == "stock":
            lines.append("%s *%s %s %s*" % (f["icon"], f["name"], f["hit"], f["price"]))
            step = (" 오늘 %s%% 선을 새로 넘었습니다." % _num(f["level"])) if f.get("level") else ""
            lines.append("     👉 오늘 하루 %s 움직였습니다.%s" % (fmt_pct(f["ch"]), step))
            state_txt = MARKET_STATE_KR.get((f.get("market_state") or "").upper())
            if state_txt:
                for term in ("정규장", "프리마켓", "애프터마켓"):
                    if "{%s}" % term in state_txt:  # 실제로 쓰일 때만 '설명함'으로 표시
                        state_txt = state_txt.replace("{%s}" % term, ex.t(term))
                lines.append("     👉 참고로 %s." % state_txt)
            lines += news_lines(f)
        lines.append("")
    lines.append("_같은 움직임은 더 커질 때(예: 7%→10%→15%)만 다시 알려 드립니다. "
                 "급하게 따라 사거나 팔기보다는 이유부터 확인해 보시는 것이 좋아 보입니다._")
    text = "\n".join(lines)
    return text, to_blocks(text)


def _num(v):
    return ("%g" % v)


def coin_move_line(f):
    ch1, ch24 = f.get("ch1"), f.get("ch24")
    if f.get("fast") and ch1 is not None:
        s = "최근 1시간 만에 %s 움직였습니다." % fmt_pct(ch1)
        if ch24 is not None:
            s += " 하루 기준으로는 %s입니다." % fmt_pct(ch24)
        return s
    s = "하루 동안 %s 움직였습니다." % fmt_pct(ch24)
    if ch1 is not None and abs(ch1) >= 0.5:
        s += " 최근 1시간은 %s입니다." % fmt_pct(ch1)
    if f.get("level"):
        s += " 이번 움직임에서 %s%% 선을 새로 넘었습니다." % _num(f["level"])
    return s


def relative_line(f):
    """비트코인과 비교해 '혼자 움직였나, 시장 전체인가'."""
    me, b = f.get("ch24"), f.get("btc_ch24")
    if me is None or b is None or abs(me) < 1:
        return None
    if me * b > 0 and abs(b) >= abs(me) * 0.5:
        return "같은 시간 비트코인도 %s 움직여, 시장 전체 흐름으로 보입니다." % fmt_pct(b)
    return "같은 시간 비트코인은 %s라, %s만 따로 움직인 것으로 보입니다." % (fmt_pct(b), f["name"])


def level_line(f, ex):
    """지지·저항까지 남은 거리. 멀리 있는 숫자 두 개보다 '얼마나 남았나'가 쓸모 있다."""
    p, sup, res = f.get("price_raw"), f.get("support"), f.get("resistance")
    if not p or not sup or not res:
        if sup and res:
            return "%s은 %s, %s은 %s입니다." % (ex.t("지지선"), fmt_price(sup), ex.t("저항선"), fmt_price(res))
        return ""
    if p >= res * 0.999:
        return "최근 20일 최고가(%s) 위로 올라서, 위쪽에 막히는 자리가 없는 구간입니다." % fmt_price(res)
    if p <= sup * 1.001:
        return "최근 20일 최저가(%s) 아래로 내려가, 아래쪽 받쳐 줄 자리가 없는 구간입니다." % fmt_price(sup)
    if f.get("hit") == "급등":
        return "%s %s까지 %.1f%% 남았습니다." % (ex.t("저항선"), fmt_price(res), (res / p - 1) * 100)
    return "%s %s까지 %.1f%% 남았습니다." % (ex.t("지지선"), fmt_price(sup), (1 - sup / p) * 100)


def news_lines(f):
    if "news" not in f:
        return []
    n = f.get("news")
    if not n:
        return ["     📰 지금 이 움직임을 설명하는 뉴스는 보이지 않습니다. "
                "뉴스 없이 움직였다면 되돌림도 빠를 수 있어 주의하시는 것이 좋아 보입니다."]
    age = n.get("age_h")
    when = "" if age is None else (" · 방금" if age < 1 else " · %d시간 전" % int(age))
    title = n["title"].replace("|", "·").replace("<", "‹").replace(">", "›")
    return ["     📰 이유로 보이는 뉴스: <%s|%s> (%s%s)" % (n["url"], title, n.get("source", ""), when)]


def alert_photo(fired):
    """급등락 사진: 차트 데이터가 있는 첫 종목의 최근 흐름. (url, caption) 또는 (None, None)"""
    for f in fired:
        ch = f.get("chart")
        if not ch or not ch.get("close"):
            continue
        if f["kind"] == "coin":
            closes, times = ch["close"][-48:], ch["time"][-48:]
            title = "%s 48H" % f["symbol"].replace("USDT", "/USDT")
            url = charts.line_chart_url(closes, times, title=title, label_every=6, label_fmt="%H:00")
            span = "최근 48시간"
        else:
            title = "%s 1M" % f["ticker"]
            url = charts.line_chart_url(ch["close"], ch["time"], title=title, label_every=5, label_fmt="%-m/%-d")
            span = "최근 한 달"
        if url:
            return url, "⚡ <b>%s %s 흐름</b>\n%s %s" % (
                tg.esc(f["name"]), span, tg.esc(f["hit"]), tg.esc(f["price"]))
    return None, None


# ------------------------------------------------------------------ 발송
def send_telegram(stream, text, photo=(None, None)):
    """텔레그램 발송. 실패해도 예외를 밖으로 던지지 않는다(슬랙을 막지 않도록)."""
    try:
        html_text = tg.slack_to_html(text)
        url, caption = photo if photo else (None, None)
        ok, msg = tg.deliver(stream, html_text, url, caption)
        if ok is False:
            slack_sender.archive(html_text, tag="%s-telegram" % stream)
        return ok, msg
    except Exception as e:
        return False, "텔레그램 처리 오류: %s" % e


def safe_photo(fn, arg):
    try:
        return fn(arg)
    except Exception:
        return None, None


# ------------------------------------------------------------------ 엔트리
def cmd_brief(cfg, send=True):
    state = load_state()
    data = collect(cfg, with_ta=True)
    dd = dominance_delta(state, data["dom"], 24)
    text, blocks = build_briefing(cfg, data, dd)
    save_state(state)
    if send:
        ok, msg = slack_sender.send_or_archive(cfg, text, blocks, tag="brief")
        tok, tmsg = send_telegram("brief", text, safe_photo(brief_photo, data))
        log("정기 브리핑 (코인 %d, 미국 %d, 한국 %d) - %s / %s"
            % (len(data["coins"]), len(data["us"]), len(data["kr"]), msg, tmsg))
    return text


def cmd_watch(cfg, send=True):
    state = load_state()
    fired = check_alerts(cfg, state)
    save_state(state)
    if not fired:
        log("감시: 새로 알릴 움직임 없음")
        return None
    attach_news(fired)
    text, blocks = build_alert(cfg, fired)
    if send:
        ok, msg = slack_sender.send_or_archive(cfg, text, blocks, tag="alert")
        tok, tmsg = send_telegram("watch", text, safe_photo(alert_photo, fired))
        log("급등락 알림 %d건 - %s / %s" % (len(fired), msg, tmsg))
    return text


def cmd_test(cfg):
    try:
        slack_sender.send(cfg, "✅ 마켓 브리핑 봇 연결 테스트에 성공했습니다! 이제 3시간마다 브리핑이 도착합니다.")
        log("슬랙 테스트 발송 완료")
    except Exception as e:
        log("슬랙 테스트 실패: %s" % e)
    no_channels = {k: v for k, v in os.environ.items() if not k.startswith("TELEGRAM_CHANNEL_")}
    for stream in ("brief", "watch"):   # 연결 테스트는 채널(구독자)에는 안 보낸다
        ok, msg = tg.deliver(stream, "✅ <b>%s</b> 연결 테스트에 성공했습니다. %s이 이 토픽으로 소식을 보내 드립니다."
                             % (tg.esc(STREAM_TITLE[stream]), BOT_NAME), env=no_channels)
        log("텔레그램 테스트 [%s] - %s" % (stream, msg))


# ------------------------------------------------------------------ 미리보기 (전송 없음)
def build_preview(cfg, mode):
    """실제 데이터로 메시지를 만든다. state.json 은 읽기만 하고 저장하지 않는다."""
    state = copy.deepcopy(load_state())
    note = None
    if mode == "brief":
        data = collect(cfg, with_ta=True)
        dd = dominance_delta(state, data["dom"], 24)
        text, _ = build_briefing(cfg, data, dd)
        photo = safe_photo(brief_photo, data)
    else:
        fired = check_alerts(cfg, state)
        if not fired:
            # 지금 임계치를 넘은 게 없으면, 코인 임계치를 0으로 낮춰 가장 크게 움직인 코인으로 샘플을 만든다.
            c2 = copy.deepcopy(cfg)
            c2["alerts"].update({"coin_1h_pct": 999, "coin_24h_pct": 0, "stock_day_pct": 999,
                                 "index_day_pct": 999, "dominance_drop_24h_pp": 999, "cooldown_minutes": 0})
            c2["us_stocks"], c2["kr_stocks"] = [], []
            c2["alerts"]["coin_24h_pct"] = 0.01
            fired = check_alerts(c2, {"alerts": {}, "levels": {}})
            fired.sort(key=lambda f: -abs(f.get("ch24") or f.get("ch1") or 0))
            fired = fired[:2]
            note = "미리보기 샘플: 지금은 기준을 넘은 종목이 없어 가장 크게 움직인 코인으로 만들었습니다."
        attach_news(fired)
        text, _ = build_alert(cfg, fired, note=note)
        photo = safe_photo(alert_photo, fired)
    return text, photo, note


def write_preview(mode, text, photo):
    if not os.path.isdir(OUTBOX_DIR):
        os.makedirs(OUTBOX_DIR)
    html_text = tg.slack_to_html(text)
    chunks = tg.split_html(html_text)
    url, caption = photo if photo else (None, None)
    conf = tg.stream_config(mode)
    topic_env = "TELEGRAM_TOPIC_%s" % mode.upper()

    slack_path = os.path.join(OUTBOX_DIR, "preview_slack_%s.txt" % mode)
    with io.open(slack_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")

    e = html.escape
    parts = ["""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>텔레그램 미리보기 - %s</title>
<style>
body{font-family:'Segoe UI','Malgun Gothic',sans-serif;background:#0e1621;color:#e6edf3;margin:0;padding:24px}
h1{font-size:20px}h2{font-size:15px;color:#8ab4f8;margin-top:28px}
.meta{font-size:13px;color:#9fb0c0;line-height:1.7}
.bubble{background:#182533;border-radius:12px;padding:12px 14px;max-width:560px;white-space:pre-wrap;
line-height:1.5;font-size:14px;margin:8px 0}
.bubble code{background:#0e1621;padding:1px 4px;border-radius:4px}
.bubble a{color:#6ab3f3}
pre{background:#11161d;border:1px solid #2b3642;padding:10px;white-space:pre-wrap;word-break:break-all;
font-size:12px;color:#c9d1d9;max-width:900px}
img{max-width:560px;border-radius:12px;display:block;background:#fff}
</style></head><body>""" % e(mode)]
    parts.append("<h1>%s — 텔레그램 미리보기 (%s)</h1>" % (e(STREAM_TITLE.get(mode, mode)), e(mode)))
    parts.append("<div class='meta'>생성: %s · 봇: %s · 실제 전송 안 함<br>"
                 "텔레그램 설정: %s · 토픽 변수: %s<br>메시지 조각: %d개 (각 조각 UTF-16 길이 %s / 제한 %d)</div>" % (
                     e(dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")), e(BOT_NAME),
                     "있음" if conf else "없음(실행 시 조용히 건너뜀)", e(topic_env),
                     len(chunks), ", ".join(str(tg.tg_len(c)) for c in chunks), tg.TEXT_LIMIT))
    parts.append("<h2>1) sendPhoto (먼저 전송, 실패해도 본문은 전송)</h2>")
    if url:
        parts.append("<div class='meta'>URL 길이 %d자</div><pre>%s</pre>" % (len(url), e(url)))
        parts.append("<div class='bubble'><img src='%s' alt='chart'>%s</div>" % (e(url, quote=True), caption))
        parts.append("<div class='meta'>caption 원문 (%d자 / 1024)</div><pre>%s</pre>" % (tg.tg_len(caption), e(caption)))
    else:
        parts.append("<div class='meta'>사진 없음 (차트 데이터를 만들지 못함)</div>")
    for i, c in enumerate(chunks, 1):
        parts.append("<h2>%d) sendMessage 조각 %d/%d — %d자</h2>" % (i + 1, i, len(chunks), tg.tg_len(c)))
        parts.append("<div class='bubble'>%s</div>" % c)
        parts.append("<pre>%s</pre>" % e(c))
    parts.append("<h2>슬랙 텍스트 (같은 내용, mrkdwn)</h2><pre>%s</pre>" % e(text))
    parts.append("</body></html>")
    tg_path = os.path.join(OUTBOX_DIR, "preview_telegram_%s.html" % mode)
    with io.open(tg_path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return tg_path, slack_path, chunks, url


def cmd_preview_telegram(cfg, mode):
    if mode not in ("brief", "watch"):
        print("사용법: python briefing.py preview-telegram brief|watch")
        return
    text, photo, note = build_preview(cfg, mode)
    tg_path, slack_path, chunks, url = write_preview(mode, text, photo)
    print("텔레그램 미리보기: %s (조각 %d개, 사진 %s)" % (tg_path, len(chunks), "있음" if url else "없음"))
    print("슬랙 미리보기   : %s" % slack_path)
    if note:
        print("참고: " + note)


def cmd_status(cfg):
    """슬랙 연결 / 텔레그램 / 최근 실행 상태를 한 화면에 보여준다."""
    url = slack_sender.resolve_webhook(cfg)
    print("슬랙 모드   : %s" % cfg.get("slack_mode", "webhook"))
    if url:
        print("웹훅 URL    : 설정됨 (...%s)" % url[-12:])
    else:
        print("웹훅 URL    : ❌ 없음  -> set_webhook.ps1 로 등록하세요")
    for stream in ("brief", "watch"):
        conf = tg.stream_config(stream)
        if conf:
            print("텔레그램 %-5s: 설정됨 (chat %s, 토픽 %s)" % (stream, conf["chat_id"], conf["thread_id"] or "기본"))
        else:
            print("텔레그램 %-5s: 없음 (TELEGRAM_BOT_TOKEN[_%s] / TELEGRAM_CHAT_ID)" % (stream, stream.upper()))

    st = load_state()
    hist = st.get("dominance_history", [])
    print("도미넌스 이력: %d건%s" % (len(hist), (" (최초 %s)" % hist[0]["t"][:16]) if hist else ""))

    if os.path.isdir(OUTBOX_DIR):
        files = sorted(os.listdir(OUTBOX_DIR))
        print("outbox      : %d개 보관 %s" % (len(files), files[-3:] if files else ""))
    else:
        print("outbox      : 비어있음(=전송 실패 없었음)")

    if os.path.exists(LOG_PATH):
        with io.open(LOG_PATH, encoding="utf-8") as f:
            tail = f.read().strip().split("\n")[-5:]
        print("\n최근 로그 5줄:")
        for t in tail:
            print("  " + t)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "brief"
    try:
        if cmd in ("-h", "--help", "help"):
            print(__doc__)
            return
        cfg = load_config()
        if cmd == "brief":
            cmd_brief(cfg)
        elif cmd == "watch":
            cmd_watch(cfg)
        elif cmd == "test":
            cmd_test(cfg)
        elif cmd == "preview":
            print(cmd_brief(cfg, send=False))
        elif cmd == "preview-telegram":
            cmd_preview_telegram(cfg, sys.argv[2] if len(sys.argv) > 2 else "brief")
        elif cmd == "status":
            cmd_status(cfg)
        elif cmd == "preview-alert":
            print(cmd_watch(cfg, send=False) or "(임계치 초과 종목 없음)")
        else:
            print(__doc__)
    except Exception:
        log("에러:\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
