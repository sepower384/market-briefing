# -*- coding: utf-8 -*-
"""
telegram_sender.py - 텔레그램 슈퍼그룹 토픽으로 보내는 전송기 (requests 만 사용).

환경변수
  TELEGRAM_BOT_TOKEN          공용 봇 토큰 (스트림별 토큰이 없을 때 사용)
  TELEGRAM_BOT_TOKEN_BRIEF    정기 브리핑 전용 봇 토큰 (있으면 우선)
  TELEGRAM_BOT_TOKEN_WATCH    급등락 감시 전용 봇 토큰 (있으면 우선)
  TELEGRAM_CHAT_ID            슈퍼그룹 ID (-100...)
  TELEGRAM_TOPIC_BRIEF        📊 세력의 시장 보고서 토픽 스레드 ID
  TELEGRAM_TOPIC_WATCH        ⚡ 세력의 레이더망 토픽 스레드 ID
  TELEGRAM_CHANNEL_CHATS_<STREAM>  같은 내용을 추가로 올릴 채널들(쉼표). 예: 브리핑을 세력 채널에도
  TELEGRAM_CHANNEL_BOT_TOKEN       그 채널에 관리자로 들어가 있는 봇 토큰(세력의 매니저). 없으면 스트림 토큰

토큰이나 채팅 ID 가 없으면 조용히 건너뛴다. 토픽 ID 가 없으면 그룹의 기본(General) 스레드로 보낸다.
"""
import html
import os
import re
import time

import requests

API = "https://api.telegram.org/bot%s/%s"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024
TIMEOUT = 20
PHOTO_TIMEOUT = 15
MAX_RETRY_AFTER = 300

# 슬랙 이모지 코드 -> 유니코드. 텔레그램은 :rocket: 을 글자 그대로 보여 주므로 반드시 바꿔야 한다.
# 이 저장소는 이모지를 유니코드로 직접 쓰지만(2026-09-15 grep 결과 코드 0건),
# 슬랙 텍스트가 섞여 들어와도 안전하도록 흔히 쓰는 코드를 넓게 매핑해 둔다.
SLACK_EMOJI = {
    "rocket": "🚀", "point_right": "👉", "point_left": "👈", "point_up": "☝️", "point_down": "👇",
    "small_red_triangle": "🔺", "small_red_triangle_down": "🔻",
    "arrow_up": "⬆️", "arrow_down": "⬇️", "arrow_right": "➡️", "arrow_left": "⬅️",
    "arrow_upper_right": "↗️", "arrow_lower_right": "↘️",
    "chart_with_upwards_trend": "📈", "chart_with_downwards_trend": "📉", "bar_chart": "📊",
    "rotating_light": "🚨", "boom": "💥", "collision": "💥", "zap": "⚡", "fire": "🔥",
    "ice_cube": "🧊", "magnet": "🧲", "coin": "🪙", "moneybag": "💰", "money_with_wings": "💸",
    "dollar": "💵", "gem": "💎", "pushpin": "📌", "round_pushpin": "📍", "memo": "📝",
    "bell": "🔔", "loudspeaker": "📢", "mega": "📣", "newspaper": "📰", "mag": "🔍",
    "eyes": "👀", "warning": "⚠️", "white_check_mark": "✅", "heavy_check_mark": "✔️",
    "x": "❌", "no_entry": "⛔", "red_circle": "🔴", "large_green_circle": "🟢",
    "green_circle": "🟢", "large_blue_circle": "🔵", "blue_circle": "🔵",
    "yellow_circle": "🟡", "white_circle": "⚪", "black_small_square": "▪️",
    "white_small_square": "▫️", "small_blue_diamond": "🔹", "small_orange_diamond": "🔸",
    "large_blue_diamond": "🔷", "large_orange_diamond": "🔶",
    "bulb": "💡", "star": "⭐", "sparkles": "✨", "tada": "🎉", "clock3": "🕒",
    "hourglass": "⌛", "hourglass_flowing_sand": "⏳", "calendar": "📆", "date": "📅",
    "flag-us": "🇺🇸", "flag-kr": "🇰🇷", "us": "🇺🇸", "kr": "🇰🇷",
    "thumbsup": "👍", "+1": "👍", "thumbsdown": "👎", "-1": "👎",
    "chart": "💹", "information_source": "ℹ️", "speech_balloon": "💬",
    "robot_face": "🤖", "briefcase": "💼", "bank": "🏦", "trophy": "🏆",
    "heavy_plus_sign": "➕", "heavy_minus_sign": "➖", "radio_button": "🔘",
}

_EMOJI_RE = re.compile(r":([a-z0-9_+\-]+):")
_LINK_RE = re.compile(r"<((?:https?|mailto):[^|>\s]+)(?:\|([^>]*))?>")
_CODE_RE = re.compile(r"`([^`\n]+)`")
_BOLD_RE = re.compile(r"(?<![A-Za-z0-9*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![A-Za-z0-9*])")
_ITALIC_RE = re.compile(r"(?<![A-Za-z0-9_])_(?=\S)([^_\n]+?)(?<=\S)_(?![A-Za-z0-9_])")
_STRIKE_RE = re.compile(r"(?<![A-Za-z0-9~])~(?=\S)([^~\n]+?)(?<=\S)~(?![A-Za-z0-9~])")
_SLACK_SPECIAL_RE = re.compile(r"<!(here|channel|everyone)(?:\|[^>]*)?>")


# ------------------------------------------------------------------ 설정
def stream_config(stream, env=None):
    """
    스트림(brief/watch)별 전송 설정. 토큰은 TELEGRAM_BOT_TOKEN_<STREAM> -> TELEGRAM_BOT_TOKEN 순.
    토큰 또는 채팅 ID 가 없으면 None.
    """
    env = os.environ if env is None else env
    key = (stream or "").upper()
    token = (env.get("TELEGRAM_BOT_TOKEN_%s" % key) or "").strip() if key else ""
    if not token:
        token = (env.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (env.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat:
        return None
    topic = (env.get("TELEGRAM_TOPIC_%s" % key) or "").strip() if key else ""
    return {"token": token, "chat_id": chat, "thread_id": int(topic) if topic.lstrip("-").isdigit() else None}


# ------------------------------------------------------------------ 변환
def emoji_codes_to_unicode(text):
    return _EMOJI_RE.sub(lambda m: SLACK_EMOJI.get(m.group(1), m.group(0)), text)


def esc(text):
    """텔레그램 HTML 모드에서 필요한 & < > 이스케이프."""
    return html.escape(text or "", quote=False)


def slack_to_html(text):
    """슬랙 mrkdwn -> 텔레그램 HTML."""
    text = emoji_codes_to_unicode(text or "")
    holders = []

    def hold(s):
        holders.append(s)
        return "\x00%d\x00" % (len(holders) - 1)

    text = _SLACK_SPECIAL_RE.sub(lambda m: hold("@" + m.group(1)), text)

    def link(m):
        url, label = m.group(1), m.group(2)
        return hold('<a href="%s">%s</a>' % (html.escape(url, quote=True), esc(label or url)))

    text = _LINK_RE.sub(link, text)
    text = esc(text)
    text = _CODE_RE.sub(lambda m: hold("<code>%s</code>" % m.group(1)), text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    text = _STRIKE_RE.sub(r"<s>\1</s>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: holders[int(m.group(1))], text)


# ------------------------------------------------------------------ 분할
def tg_len(s):
    """텔레그램 글자 수 제한은 UTF-16 단위 기준 → 이모지 2칸으로 보수적으로 센다."""
    return len(s.encode("utf-16-le")) // 2


_TOKEN_RE = re.compile(r"<[^>]+>|&[#a-zA-Z0-9]+;|[\s\S]")
_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9-]+)[^>]*>")


def _hard_split(line, limit):
    """한 줄이 제한보다 길 때: 태그/엔티티 중간을 피해서 자르고, 열린 태그는 닫았다가 다음 조각에서 다시 연다."""
    chunks, buf, stack = [], "", []  # stack: (name, open_tag_text)

    def closing():
        return "".join("</%s>" % n for n, _ in reversed(stack))

    for tok in _TOKEN_RE.findall(line):
        m = _TAG_RE.fullmatch(tok)
        extra = tg_len(tok) + tg_len(closing())
        if m and m.group(1):  # 닫는 태그는 닫는 태그 몫으로 이미 계산됨
            extra = 0
        if buf and tg_len(buf) + extra > limit:
            chunks.append(buf + closing())
            buf = "".join(t for _, t in stack)
        buf += tok
        if m:
            if m.group(1):
                if stack and stack[-1][0] == m.group(2):
                    stack.pop()
            else:
                stack.append((m.group(2), tok))
    if buf:
        chunks.append(buf + closing())
    return chunks


def split_html(text, limit=TEXT_LIMIT):
    """섹션(빈 줄) → 줄 → 글자 순으로 경계를 찾아 limit 이하 조각들로 나눈다."""
    text = (text or "").strip()
    if not text:
        return []
    if tg_len(text) <= limit:
        return [text]

    pieces = []  # (조각, 앞 구분자)
    for para in text.split("\n\n"):
        if tg_len(para) <= limit:
            pieces.append((para, "\n\n"))
            continue
        first = True
        for line in para.split("\n"):
            parts = [line] if tg_len(line) <= limit else _hard_split(line, limit)
            for p in parts:
                pieces.append((p, "\n\n" if first else "\n"))
                first = False

    out, cur = [], ""
    for piece, sep in pieces:
        cand = (cur + sep + piece) if cur else piece
        if tg_len(cand) <= limit:
            cur = cand
        else:
            if cur.strip():
                out.append(cur.strip())
            cur = piece
    if cur.strip():
        out.append(cur.strip())
    return out


def clip_caption(caption, limit=CAPTION_LIMIT):
    caption = caption or ""
    if tg_len(caption) <= limit:
        return caption
    return _hard_split(caption, limit - 1)[0] + "…"


# ------------------------------------------------------------------ 전송
def _call(token, method, payload, timeout=TIMEOUT, post=None, sleep=time.sleep):
    """Bot API 호출. 429 는 retry_after 만큼 쉬고 한 번만 다시 시도한다."""
    post = post or requests.post
    url = API % (token, method)
    for attempt in (1, 2):
        r = post(url, json=payload, timeout=timeout)
        try:
            body = r.json()
        except Exception:
            body = {"ok": False, "description": (getattr(r, "text", "") or "")[:200]}
        if r.status_code == 429 and attempt == 1:
            wait = ((body.get("parameters") or {}).get("retry_after")) or 1
            sleep(min(float(wait), MAX_RETRY_AFTER))
            continue
        if r.status_code != 200 or not body.get("ok"):
            raise RuntimeError("텔레그램 %s 실패 %s: %s" % (method, r.status_code, body.get("description")))
        return body
    raise RuntimeError("텔레그램 %s 실패: 429 재시도 후에도 제한" % method)


def send_message(conf, text, post=None, sleep=time.sleep):
    payload = {"chat_id": conf["chat_id"], "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if conf.get("thread_id"):
        payload["message_thread_id"] = conf["thread_id"]
    return _call(conf["token"], "sendMessage", payload, post=post, sleep=sleep)


def send_photo(conf, photo_url, caption="", post=None, sleep=time.sleep):
    payload = {"chat_id": conf["chat_id"], "photo": photo_url,
               "caption": clip_caption(caption), "parse_mode": "HTML"}
    if conf.get("thread_id"):
        payload["message_thread_id"] = conf["thread_id"]
    return _call(conf["token"], "sendPhoto", payload, timeout=PHOTO_TIMEOUT, post=post, sleep=sleep)


def channel_configs(stream, env=None):
    """추가 발행 채널 설정 목록 (채널 글은 봇 이름이 아니라 채널 이름으로 보이므로 관리자 봇 토큰을 쓴다)."""
    env = os.environ if env is None else env
    key = (stream or "").upper()
    chats = [c.strip() for c in (env.get("TELEGRAM_CHANNEL_CHATS_%s" % key) or "").split(",") if c.strip()]
    base = stream_config(stream, env) or {}
    token = (env.get("TELEGRAM_CHANNEL_BOT_TOKEN") or "").strip() or base.get("token")
    if not token:
        return []
    return [{"token": token, "chat_id": c, "thread_id": None} for c in chats]


def _deliver_to(conf, html_text, photo_url, photo_caption, post, sleep):
    note = ""
    if photo_url:
        try:
            send_photo(conf, photo_url, photo_caption or "", post=post, sleep=sleep)
            sleep(1)
        except Exception as e:  # 사진은 실패해도 본문은 반드시 보낸다
            note = " (사진 생략: %s)" % str(e)[:120]
    chunks = split_html(html_text)
    for i, chunk in enumerate(chunks):
        if i:
            sleep(1)
        send_message(conf, chunk, post=post, sleep=sleep)
    return len(chunks), note


def deliver(stream, html_text, photo_url=None, photo_caption=None, env=None, post=None, sleep=time.sleep):
    """
    사진(선택) 먼저, 본문은 분할해서 1초 간격으로. 반환 (ok, 메시지)
      ok=None  → 설정이 없어 건너뜀
      ok=True  → 본문 전송 완료 (사진 실패는 무시)
      ok=False → 본문 전송 실패
    기본 대상(TELEGRAM_CHAT_ID) 다음에 추가 채널(TELEGRAM_CHANNEL_CHATS_<STREAM>)에도 보낸다.
    추가 채널 실패는 결과(ok)에 영향을 주지 않고 메시지에만 적는다.
    """
    conf = stream_config(stream, env)
    if conf is None:
        return None, "텔레그램 설정 없음 - 건너뜀"
    try:
        n, note = _deliver_to(conf, html_text, photo_url, photo_caption, post, sleep)
    except Exception as e:
        return False, "텔레그램 전송 실패(%s)" % e
    extra = []
    for ch in channel_configs(stream, env):
        try:
            _deliver_to(ch, html_text, photo_url, photo_caption, post, sleep)
            extra.append("%s 완료" % ch["chat_id"])
        except Exception as e:
            extra.append("%s 실패(%s)" % (ch["chat_id"], str(e)[:80]))
    return True, "텔레그램 전송 완료 %d건%s%s" % (n, note, (" / 채널: " + ", ".join(extra)) if extra else "")
