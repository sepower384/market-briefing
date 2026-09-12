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


# ------------------------------------------------------------------ 브리핑 본문
def build_briefing(cfg, data, dom_delta):
    kst = mk.now_kst(cfg["briefing"].get("timezone_offset_hours", 9))
    head = "📊 *마켓 브리핑* — %s(%s) %s KST" % (
        kst.strftime("%Y-%m-%d"), WEEKDAY_KR[kst.weekday()], kst.strftime("%H:%M"))

    lines = [head, ""]

    # ---------------- 코인
    lines.append("*━━ 코인 ━━*")
    for c in data["coins"]:
        if c.get("error"):
            lines.append("• %s: 조회실패" % c["name"])
            continue
        sig = mk.read_signal(c)
        lines.append("%s *%s* %s  `24h %s`  `1h %s`  `7d %s`" % (
            arrow(c.get("change_24h")), c["name"], fmt_price(c.get("price")),
            fmt_pct(c.get("change_24h")), fmt_pct(c.get("change_1h")),
            fmt_pct(c.get("change_7d"))))
        if cfg["briefing"].get("show_ta", True) and c.get("ta"):
            ta = c["ta"]
            lines.append("     └ %s · %s" % (sig["verdict"], " / ".join(sig["reasons"][:4])))
            lines.append("     └ 지지 %s · 저항 %s · 1H RSI %s" % (
                fmt_price(ta.get("support")), fmt_price(ta.get("resistance")),
                "%.0f" % ta["rsi_1h"] if ta.get("rsi_1h") else "-"))
    lines.append("")

    # ---------------- 도미넌스
    if data["dom"]:
        d = data["dom"]
        dd = ""
        if dom_delta is not None:
            dd = "  (24h %s%.2f%%p)" % ("+" if dom_delta >= 0 else "", dom_delta)
        lines.append("*━━ 도미넌스 ━━*")
        lines.append("• BTC.D *%.2f%%*%s / ETH.D %.2f%% / USDT.D %.2f%%" % (
            d["btc"], dd, d["eth"] or 0, d["usdt"] or 0))
        lines.append("• 전체 시총 $%.2fT (24h %s)" % (
            d["total_mcap_usd"] / 1e12, fmt_pct(d.get("total_mcap_change_24h"))))
        if dom_delta is not None:
            if dom_delta <= -0.5:
                lines.append("  └ 🟢 도미넌스 하락 → 알트로 자금 이동 신호")
            elif dom_delta >= 0.5:
                lines.append("  └ 🔴 도미넌스 상승 → 알트 약세 / BTC 쏠림")
        lines.append("")

    # ---------------- 주식
    for key, title in (("us", "미국 증시"), ("kr", "한국 증시")):
        rows = [s for s in data[key] if not s.get("error") and s.get("change_day") is not None]
        if not rows:
            continue
        rows.sort(key=lambda x: x["change_day"], reverse=True)
        lines.append("*━━ %s ━━*" % title)
        for s in rows:
            cur = s.get("currency", "USD")
            is_index = s["ticker"].startswith("^")
            extra = ""
            ta = s.get("ta")
            if ta and s.get("price"):
                if ta.get("ma200") and s["price"] < ta["ma200"]:
                    extra = " · MA200 아래"
                elif ta.get("rsi_1d") and ta["rsi_1d"] >= 70:
                    extra = " · RSI %.0f 과열" % ta["rsi_1d"]
                elif ta.get("rsi_1d") and ta["rsi_1d"] <= 30:
                    extra = " · RSI %.0f 침체" % ta["rsi_1d"]
            shown = ("{:,.2f}p".format(s["price"]) if is_index and s.get("price")
                     else fmt_price(s.get("price"), cur))
            lines.append("%s %s %s  `%s`%s" % (
                arrow(s["change_day"], 3.0), s["name"], shown,
                fmt_pct(s["change_day"]), extra))
        lines.append("")

    if data["errors"]:
        lines.append("_수집 실패: %s_" % ", ".join(data["errors"][:5]))

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
    lines = ["⚡ *급등/급락 알림* — %s KST" % kst.strftime("%m-%d %H:%M"), ""]
    for f in fired:
        lines.append("%s *%s*" % (f["icon"], f["title"]))
        lines.append("     └ %s" % f["detail"])
        if f.get("extra"):
            lines.append("     └ %s" % f["extra"])
        lines.append("")
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
