# -*- coding: utf-8 -*-
"""
briefing.py - 3시간마다 정기 브리핑 + 급등/급락 즉시 알림.

사용법
  python briefing.py brief     정기 브리핑 1회 발송 (예약작업이 3시간마다 호출)
  python briefing.py watch     급등/급락 감시 1회 실행 (예약작업이 5~10분마다 호출)
  python briefing.py test      슬랙 연결만 테스트
  python briefing.py preview   슬랙 안 보내고 화면에만 출력
  python briefing.py status    슬랙 연결/이력/로그 상태 점검
"""
import io
import json
import os
import sys
import traceback
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market as mk
import slack_sender

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE, "config.json")
STATE_PATH = os.path.join(BASE, "state.json")
LOG_PATH = os.path.join(BASE, "briefing.log")

WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]


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


# ------------------------------------------------------------------ 사람 말로 풀기
VERDICT_KR = {
    "강세": "상승 흐름이 뚜렷해요",
    "약강세": "살짝 오르는 쪽으로 기울어 있어요",
    "중립": "방향이 아직 안 정해졌어요",
    "약약세": "살짝 내리는 쪽으로 기울어 있어요",
    "약세": "하락 흐름이 뚜렷해요",
    "판단보류": "지표가 부족해 판단을 미룰게요",
}


def move_word(v, unit="하루"):
    """+1.2 → '하루 동안 1.2% 올랐어요'"""
    if v is None:
        return "%s 변동은 확인이 안 됐어요" % unit
    if abs(v) < 0.05:
        return "%s 동안 거의 그대로예요" % unit
    return "%s 동안 %.1f%% %s" % (unit, abs(v), "올랐어요" if v > 0 else "내렸어요")


def coin_story(c):
    """코인 한 개를 2~3줄 설명으로."""
    ta = c.get("ta") or {}
    price = c.get("price")
    sig = mk.read_signal(c)
    ch = c.get("change_24h") or 0
    # 오늘 움직임과 큰 흐름이 반대면 '잠깐'이라고 짚어줘야 헷갈리지 않는다
    against = (ch < 0 and sig["score"] > 0) or (ch > 0 and sig["score"] < 0)
    verdict = VERDICT_KR.get(sig["verdict"], sig["verdict"])
    if against:
        first = "하루 동안 %.1f%% %s 몇 주 단위 큰 흐름은 *%s*" % (
            abs(ch), "빠졌지만" if ch < 0 else "올랐지만", verdict)
    else:
        first = "%s. 흐름은 *%s*" % (move_word(c.get("change_24h")), verdict)
    out = ["     👉 " + first]

    hints = []
    if ta.get("ma200") and price:
        hints.append("장기 평균가격(200일선)보다 %s 큰 추세는 %s" % (
            "위라서" if price >= ta["ma200"] else "아래라서",
            "아직 살아 있어요" if price >= ta["ma200"] else "약한 편이에요"))
    r = ta.get("rsi_1d")
    if r is not None and r >= 70:
        hints.append("최근 많이 올라 *과열* 구간이라 쉬어갈 수 있어요")
    elif r is not None and r <= 30:
        hints.append("많이 빠져서 *과매도* 구간이라 반등이 나올 수 있어요")
    macd = ta.get("macd_1d")
    if macd and macd["hist"] > 0 and macd["prev_hist"] <= 0:
        hints.append("상승 전환 신호(골든크로스)가 막 나왔어요")
    elif macd and macd["hist"] < 0 and macd["prev_hist"] >= 0:
        hints.append("하락 전환 신호(데드크로스)가 막 나왔어요")
    if ta.get("vol_ratio_1h") and ta["vol_ratio_1h"] >= 2:
        hints.append("최근 1시간 거래량이 평소의 %.1f배로 사람들이 몰렸어요" % ta["vol_ratio_1h"])
    if hints:
        out.append("     👉 " + " / ".join(hints[:2]))
    if ta.get("support") and ta.get("resistance"):
        out.append("     👉 버팀목(최근 저점) %s · 넘어야 할 벽(최근 고점) %s" % (
            fmt_price(ta["support"]), fmt_price(ta["resistance"])))
    return out


def summary_lines(data, dom_delta):
    """맨 위 '한눈에 보기' 3~4줄."""
    out = []
    btc = next((c for c in data["coins"] if not c.get("error") and "BTC" in str(c.get("symbol", "")).upper()), None)
    btc = btc or next((c for c in data["coins"] if not c.get("error")), None)
    if btc:
        sig = mk.read_signal(btc)
        out.append("• *%s*는 지금 %s이고, %s 몇 주 단위 큰 흐름은 %s." % (
            btc["name"], fmt_price(btc.get("price")), move_word(btc.get("change_24h")),
            VERDICT_KR.get(sig["verdict"], sig["verdict"])))
    alts = [c for c in data["coins"] if not c.get("error") and c is not btc and c.get("change_24h") is not None]
    if alts:
        up = sum(1 for c in alts if c["change_24h"] > 0)
        best = max(alts, key=lambda c: c["change_24h"])
        worst = min(alts, key=lambda c: c["change_24h"])
        head = ("다른 코인 %d개가 모두 내렸어요" % len(alts) if up == 0 else
                "다른 코인 %d개가 모두 올랐어요" % len(alts) if up == len(alts) else
                "다른 코인 %d개 중 %d개가 올랐어요" % (len(alts), up))
        out.append("• %s. 제일 선방한 건 %s(%s), 제일 약한 건 %s(%s)." % (
            head, best["name"], fmt_pct(best["change_24h"]), worst["name"], fmt_pct(worst["change_24h"])))
    if dom_delta is not None:
        if dom_delta <= -0.5:
            out.append("• 비트코인 쏠림이 줄고 있어요 → 알트코인으로 돈이 옮겨갈 수 있는 신호예요.")
        elif dom_delta >= 0.5:
            out.append("• 돈이 비트코인으로 쏠리고 있어요 → 이럴 땐 보통 알트코인이 더 약해요.")
    for key, title in (("us", "미국 증시"), ("kr", "한국 증시")):
        idx = [s for s in data[key] if s["ticker"].startswith("^") and s.get("change_day") is not None]
        if idx:
            avg = sum(s["change_day"] for s in idx) / len(idx)
            mood = "올랐어요" if avg > 0.3 else "내렸어요" if avg < -0.3 else "보합(거의 제자리)이에요"
            out.append("• %s는 %s (%s)." % (
                title, mood, ", ".join("%s %s" % (s["name"], fmt_pct(s["change_day"])) for s in idx)))
    return out


# ------------------------------------------------------------------ 브리핑 본문
def build_briefing(cfg, data, dom_delta):
    kst = mk.now_kst(cfg["briefing"].get("timezone_offset_hours", 9))
    head = "📊 *마켓 브리핑* — %s(%s) %s" % (
        kst.strftime("%m월 %d일"), WEEKDAY_KR[kst.weekday()], kst.strftime("%H:%M"))

    lines = [head, ""]
    summ = summary_lines(data, dom_delta)
    if summ:
        lines.append("*📌 한눈에 보기*")
        lines += summ
        lines.append("")

    # ---------------- 코인
    lines.append("*━━ 🪙 코인 ━━*")
    for c in data["coins"]:
        if c.get("error"):
            lines.append("• %s: 이번엔 시세를 못 가져왔어요" % c["name"])
            continue
        lines.append("%s *%s* %s   (하루 %s · 1시간 %s · 일주일 %s)" % (
            arrow(c.get("change_24h")), c["name"], fmt_price(c.get("price")),
            fmt_pct(c.get("change_24h")), fmt_pct(c.get("change_1h")),
            fmt_pct(c.get("change_7d"))))
        if cfg["briefing"].get("show_ta", True) and c.get("ta"):
            lines += coin_story(c)
        lines.append("")

    # ---------------- 도미넌스
    if data["dom"]:
        d = data["dom"]
        lines.append("*━━ 🧲 돈이 어디로 몰리나 (도미넌스) ━━*")
        dd = ""
        if dom_delta is not None:
            dd = ", 하루 전보다 %.2f%%p %s" % (abs(dom_delta), "늘었어요" if dom_delta >= 0 else "줄었어요")
        lines.append("• 코인 전체 돈 중 *비트코인 비중 %.1f%%*%s" % (d["btc"], dd))
        lines.append("• 이더리움 %.1f%% · 달러 스테이블코인(USDT) %.1f%%" % (d["eth"] or 0, d["usdt"] or 0))
        lines.append("• 코인 시장 전체 규모 약 %.2f조 달러 (하루 %s)" % (
            d["total_mcap_usd"] / 1e12, fmt_pct(d.get("total_mcap_change_24h"))))
        if dom_delta is not None:
            if dom_delta <= -0.5:
                lines.append("  👉 🟢 비트코인 비중이 줄어요 = 알트코인으로 돈이 옮겨가는 중일 수 있어요")
            elif dom_delta >= 0.5:
                lines.append("  👉 🔴 비트코인 비중이 늘어요 = 알트코인에서 돈이 빠져 비트코인으로 피신 중")
        if (d["usdt"] or 0) >= 6:
            lines.append("  👉 스테이블코인 비중이 높을수록 '관망하며 현금 들고 있는 돈'이 많다는 뜻이에요")
        lines.append("")

    # ---------------- 주식
    for key, title in (("us", "🇺🇸 미국 증시"), ("kr", "🇰🇷 한국 증시")):
        rows = [s for s in data[key] if not s.get("error") and s.get("change_day") is not None]
        if not rows:
            continue
        rows.sort(key=lambda x: x["change_day"], reverse=True)
        up = sum(1 for s in rows if s["change_day"] > 0)
        lines.append("*━━ %s ━━*" % title)
        lines.append("_%d개 중 %d개 상승, %d개 하락_" % (len(rows), up, len(rows) - up))
        weak = 0
        for s in rows:
            cur = s.get("currency", "USD")
            is_index = s["ticker"].startswith("^")
            extra = ""
            ta = s.get("ta")
            if ta and s.get("price"):
                if ta.get("rsi_1d") and ta["rsi_1d"] >= 70:
                    extra = " · 🔥 단기 과열"
                elif ta.get("rsi_1d") and ta["rsi_1d"] <= 30:
                    extra = " · 🧊 많이 빠진 구간"
                elif ta.get("ma200") and s["price"] < ta["ma200"]:
                    extra = " · 장기추세 약함"
                    weak += 1
            shown = ("{:,.2f}p".format(s["price"]) if is_index and s.get("price")
                     else fmt_price(s.get("price"), cur))
            lines.append("%s %s %s  `%s`%s" % (
                arrow(s["change_day"], 3.0), s["name"], shown,
                fmt_pct(s["change_day"]), extra))
        if weak:
            lines.append("  👉 '장기추세 약함' = 200일 평균가격보다 아래라 큰 흐름이 아직 하락 쪽이라는 뜻이에요")
        lines.append("")

    if data["errors"]:
        lines.append("_이번에 못 가져온 것: %s_" % ", ".join(data["errors"][:5]))

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


# ------------------------------------------------------------------ 급등락 감시
def check_alerts(cfg, state):
    """임계치를 넘은 종목만 골라낸다. 쿨다운으로 도배 방지."""
    a = cfg["alerts"]
    cooldown = dt.timedelta(minutes=a.get("cooldown_minutes", 60))
    now = dt.datetime.utcnow()
    fired = []

    def allow(key):
        last = state["alerts"].get(key)
        if last and now - dt.datetime.fromisoformat(last) < cooldown:
            return False
        state["alerts"][key] = now.isoformat()
        return True

    # 코인
    for c in cfg["coins"]:
        try:
            s = mk.coin_snapshot(c["symbol"], with_ta=True)
        except Exception:
            continue
        if s.get("error"):
            continue
        ch1, ch24 = s.get("change_1h"), s.get("change_24h")
        hit, why = None, []
        if ch1 is not None and abs(ch1) >= a["coin_1h_pct"]:
            hit = "급등" if ch1 > 0 else "급락"
            why.append("1시간 %s" % fmt_pct(ch1))
        if ch24 is not None and abs(ch24) >= a["coin_24h_pct"]:
            hit = "급등" if ch24 > 0 else "급락"
            why.append("24시간 %s" % fmt_pct(ch24))
        if hit:
            direction = "up" if (ch1 or ch24 or 0) > 0 else "down"
            if allow("coin:%s:%s" % (c["symbol"], direction)):
                ta = s.get("ta") or {}
                vr = ta.get("vol_ratio_1h")
                if vr and vr >= 2:
                    why.append("거래량 평소 %.1f배" % vr)
                sig = mk.read_signal(s)
                fired.append({
                    "icon": "🚨" if hit == "급락" else "🚀",
                    "title": "%s %s %s" % (c["name"], hit, fmt_price(s["price"])),
                    "detail": " · ".join(why),
                    "extra": "%s / 지지 %s · 저항 %s" % (
                        sig["verdict"], fmt_price(ta.get("support")),
                        fmt_price(ta.get("resistance"))),
                })

    # 도미넌스
    try:
        dom = mk.dominance()
        dd = dominance_delta(state, dom, 24)
        if dd is not None and dd <= -abs(a["dominance_drop_24h_pp"]):
            if allow("dominance:down"):
                fired.append({
                    "icon": "🟢",
                    "title": "BTC 도미넌스 하락 %.2f%% (24h %.2f%%p)" % (dom["btc"], dd),
                    "detail": "알트코인으로 자금 이동 신호",
                    "extra": "전체 시총 $%.2fT" % (dom["total_mcap_usd"] / 1e12),
                })
    except Exception:
        pass

    # 주식
    for lst in (cfg["us_stocks"], cfg["kr_stocks"]):
        for s in lst:
            q = mk.stock_quick(s["ticker"])
            if not q or q.get("change_day") is None:
                continue
            is_index = s["ticker"].startswith("^")
            thr = a["index_day_pct"] if is_index else a["stock_day_pct"]
            ch = q["change_day"]
            if abs(ch) < thr:
                continue
            direction = "up" if ch > 0 else "down"
            if not allow("stock:%s:%s" % (s["ticker"], direction)):
                continue
            shown = ("{:,.2f}p".format(q["price"]) if is_index
                     else fmt_price(q["price"], q.get("currency", "USD")))
            fired.append({
                "icon": "🚨" if ch < 0 else "🚀",
                "title": "%s %s %s" % (s["name"], "급등" if ch > 0 else "급락", shown),
                "detail": "당일 %s" % fmt_pct(ch),
                "extra": "장상태: %s" % q.get("market_state", "-"),
            })
    return fired


def build_alert(cfg, fired):
    kst = mk.now_kst(cfg["briefing"].get("timezone_offset_hours", 9))
    lines = ["⚡ *가격이 크게 움직였어요* — %s" % kst.strftime("%m월 %d일 %H:%M"),
             "_평소보다 큰 변동이 감지돼서 바로 알려드려요._", ""]
    for f in fired:
        lines.append("%s *%s*" % (f["icon"], f["title"]))
        lines.append("     👉 무슨 일? %s" % f["detail"]
                     .replace("1시간 ", "1시간 만에 ").replace("24시간 ", "하루 동안 ").replace("당일 ", "오늘 ")
                     .replace("거래량 평소", "거래량이 평소의"))
        if "거래량" in f["detail"]:
            lines.append("     👉 거래량이 같이 터졌다는 건 실제로 사고파는 사람이 몰렸다는 뜻이에요")
        if f.get("extra"):
            lines.append("     👉 참고: %s" % f["extra"]
                         .replace("지지 ", "버팀목 ").replace("저항 ", "벽 ")
                         .replace("장상태: REGULAR", "정규장 진행 중").replace("장상태: CLOSED", "장 마감 상태")
                         .replace("장상태: PRE", "개장 전 거래").replace("장상태: POST", "장 마감 후 거래"))
        lines.append("")
    lines.append("_급하게 따라 사거나 팔기보다, 왜 움직였는지 뉴스부터 확인해보세요._")
    text = "\n".join(lines)
    return text, to_blocks(text)


# ------------------------------------------------------------------ 엔트리
def cmd_brief(cfg, send=True):
    state = load_state()
    data = collect(cfg, with_ta=True)
    dd = dominance_delta(state, data["dom"], 24)
    text, blocks = build_briefing(cfg, data, dd)
    save_state(state)
    if send:
        ok, msg = slack_sender.send_or_archive(cfg, text, blocks, tag="brief")
        log("정기 브리핑 (코인 %d, 미국 %d, 한국 %d) - %s"
            % (len(data["coins"]), len(data["us"]), len(data["kr"]), msg))
    return text


def cmd_watch(cfg, send=True):
    state = load_state()
    fired = check_alerts(cfg, state)
    save_state(state)
    if not fired:
        log("감시: 임계치 초과 없음")
        return None
    text, blocks = build_alert(cfg, fired)
    if send:
        ok, msg = slack_sender.send_or_archive(cfg, text, blocks, tag="alert")
        log("급등락 알림 %d건 - %s" % (len(fired), msg))
    return text


def cmd_status(cfg):
    """슬랙 연결 / 예약작업 / 최근 실행 상태를 한 화면에 보여준다."""
    url = slack_sender.resolve_webhook(cfg)
    print("슬랙 모드   : %s" % cfg.get("slack_mode", "webhook"))
    if url:
        print("웹훅 URL    : 설정됨 (...%s)" % url[-12:])
    else:
        print("웹훅 URL    : ❌ 없음  -> set_webhook.ps1 로 등록하세요")

    st = load_state()
    hist = st.get("dominance_history", [])
    print("도미넌스 이력: %d건%s" % (len(hist), (" (최초 %s)" % hist[0]["t"][:16]) if hist else ""))

    outbox = os.path.join(BASE, "outbox")
    if os.path.isdir(outbox):
        files = sorted(os.listdir(outbox))
        print("outbox      : %d일치 보관 %s" % (len(files), files[-3:] if files else ""))
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
        cfg = load_config()
        if cmd == "brief":
            cmd_brief(cfg)
        elif cmd == "watch":
            cmd_watch(cfg)
        elif cmd == "test":
            slack_sender.send(cfg, "✅ 마켓 브리핑 봇 연결 테스트 성공! 이제 3시간마다 브리핑이 옵니다.")
            log("슬랙 테스트 발송 완료")
        elif cmd == "preview":
            print(cmd_brief(cfg, send=False))
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
