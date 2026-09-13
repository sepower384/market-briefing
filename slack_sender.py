# -*- coding: utf-8 -*-
"""
slack_sender.py - 슬랙 전송기 2종.

1) webhook  (권장, 기본값)  : Incoming Webhook URL 하나면 끝. 무료. 크롬 안 켜도 됨.
2) playwright(대체)        : 이미 로그인된 크롬에 CDP로 붙어 슬랙 웹에 직접 타이핑.
                             크롬이 --remote-debugging-port=9222 로 떠 있어야 함.

config.json 의 "slack_mode" 로 선택: "webhook" | "playwright" | "auto"
"auto" 는 웹훅 먼저 시도하고 실패하면 playwright 로 넘어감.
"""
import datetime as dt
import io
import json
import os
import time

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
OUTBOX_DIR = os.path.join(BASE, "outbox")

UA = {"User-Agent": "Mozilla/5.0 market-briefing/1.0"}


def resolve_webhook(cfg):
    """웹훅 URL을 config.json -> 환경변수 SLACK_WEBHOOK_URL 순으로 찾는다."""
    url = (cfg.get("slack_webhook_url") or "").strip()
    if not url or "PUT_YOUR" in url:
        url = (os.environ.get("SLACK_WEBHOOK_URL") or "").strip()
    return url


# ------------------------------------------------------------------ 1) 웹훅
def send_webhook(url, text, blocks=None):
    if not url or "PUT_YOUR" in url:
        raise RuntimeError(
            "슬랙 웹훅 URL이 없습니다. config.json 의 slack_webhook_url 에 넣거나 "
            "set_webhook.ps1 로 등록하세요.")
    payload = {"text": text}
    if blocks:
        payload["blocks"] = blocks
    r = requests.post(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json; charset=utf-8", **UA},
                      timeout=20)
    if r.status_code != 200 or r.text.strip() != "ok":
        raise RuntimeError("슬랙 웹훅 실패 %s: %s" % (r.status_code, r.text[:300]))
    return True


# ------------------------------------------------------------- 2) Playwright
def send_playwright(channel_url, text, cdp="http://127.0.0.1:9222",
                    user_data_dir=None, headless=True):
    """
    로그인된 크롬(CDP)에 붙어 슬랙 채널에 메시지를 입력/전송한다.
    channel_url 예) https://app.slack.com/client/T01ABCD/C01EFGH
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = ctx = None
        try:
            browser = p.chromium.connect_over_cdp(cdp)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        except Exception:
            if not user_data_dir:
                raise RuntimeError(
                    "크롬 CDP(%s) 연결 실패. 크롬을 --remote-debugging-port=9222 로 띄우거나 "
                    "config 의 chrome_user_data_dir 를 지정하세요." % cdp)
            ctx = p.chromium.launch_persistent_context(user_data_dir, headless=headless)

        page = ctx.new_page()
        page.goto(channel_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)

        box = None
        for sel in ('div[data-qa="message_input"] div[contenteditable="true"]',
                    'div[role="textbox"][contenteditable="true"]',
                    '.ql-editor[contenteditable="true"]'):
            try:
                page.wait_for_selector(sel, timeout=15000)
                box = page.query_selector(sel)
                if box:
                    break
            except Exception:
                continue
        if box is None:
            page.close()
            raise RuntimeError("슬랙 입력창을 찾지 못했습니다. 로그인 상태/채널 URL 확인 필요.")

        box.click()
        # 슬랙은 Enter=전송이라 줄바꿈은 Shift+Enter 로 넣는다.
        for i, line in enumerate(text.split("\n")):
            if i:
                page.keyboard.press("Shift+Enter")
            page.keyboard.insert_text(line)
        page.wait_for_timeout(400)
        page.keyboard.press("Enter")
        page.wait_for_timeout(2500)
        page.close()
        return True


# ------------------------------------------------------------------ 디스패치
def send(cfg, text, blocks=None):
    mode = cfg.get("slack_mode", "webhook")
    url = resolve_webhook(cfg)
    pw = cfg.get("playwright", {}) or {}

    errors = []
    order = {"webhook": ["webhook"],
             "playwright": ["playwright"],
             "auto": ["webhook", "playwright"]}.get(mode, ["webhook"])

    for m in order:
        try:
            if m == "webhook":
                return send_webhook(url, text, blocks)
            if pw.get("slack_channel_url") and not pw.get("chrome_user_data_dir"):
                import slack_cdp
                return slack_cdp.post(pw["slack_channel_url"], text,
                                      cdp=pw.get("cdp_url", "http://127.0.0.1:9222"))
            return send_playwright(
                pw.get("slack_channel_url", ""),
                text,
                cdp=pw.get("cdp_url", "http://127.0.0.1:9222"),
                user_data_dir=pw.get("chrome_user_data_dir") or None,
                headless=pw.get("headless", True),
            )
        except Exception as e:
            errors.append("%s: %s" % (m, e))
            time.sleep(1)
    raise RuntimeError("슬랙 전송 실패 -> " + " | ".join(errors))


# ------------------------------------------------------------------ outbox
def archive(text, tag="brief"):
    """슬랙에 못 보냈어도 내용은 남긴다. outbox/YYYYMMDD.md 에 append."""
    try:
        if not os.path.isdir(OUTBOX_DIR):
            os.makedirs(OUTBOX_DIR)
        path = os.path.join(OUTBOX_DIR, dt.datetime.now().strftime("%Y%m%d") + ".md")
        with io.open(path, "a", encoding="utf-8") as f:
            f.write("\n\n---\n### [%s] %s\n\n%s\n"
                    % (tag, dt.datetime.now().strftime("%H:%M:%S"), text))
        return path
    except Exception:
        return None


def send_or_archive(cfg, text, blocks=None, tag="brief"):
    """
    전송 시도 -> 실패하면 outbox 에 보관하고 (True/False, 메시지) 를 돌려준다.
    예약작업에서 쓰는 경로. 슬랙이 죽어도 작업 자체는 실패로 안 끝난다.
    """
    try:
        send(cfg, text, blocks)
        return True, "슬랙 전송 완료"
    except Exception as e:
        path = archive(text, tag)
        return False, "슬랙 전송 실패(%s) -> outbox 보관: %s" % (e, path)
