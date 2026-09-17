# -*- coding: utf-8 -*-
"""PC(content-radar)가 암호화해 보낸 회장님 전용 메시지를 복호화해 텔레그램으로 보낸다.

    RELAY_PAYLOAD=<암호문> CONTENT_RELAY_KEY=<키> python relay_send.py NEWS
토큰·대상은 이 저장소의 기존 Secret 을 그대로 쓴다(TELEGRAM_BOT_TOKEN_<STREAM> → TELEGRAM_BOT_TOKEN,
TELEGRAM_CHAT_ID). 채널(구독자) 쪽으로는 보내지 않는다 — 회장님 전용.
"""
import gzip
import json
import os
import sys
import time

import requests
from cryptography.fernet import Fernet


def env(k):
    return (os.environ.get(k) or "").strip()


def main():
    stream = (sys.argv[1] if len(sys.argv) > 1 else "NEWS").upper()
    key, payload = env("CONTENT_RELAY_KEY"), env("RELAY_PAYLOAD")
    if not key or not payload:
        print("::error::CONTENT_RELAY_KEY 또는 payload 없음")
        return 1
    chunks = json.loads(gzip.decompress(Fernet(key.encode()).decrypt(payload.encode())).decode("utf-8"))["chunks"]
    token = env("TELEGRAM_BOT_TOKEN_" + stream) or env("TELEGRAM_BOT_TOKEN")
    chat = env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("::error::텔레그램 토큰/대상 없음")
        return 1
    ok = 0
    for i, text in enumerate(chunks):
        r = None
        for _ in range(3):
            r = requests.post("https://api.telegram.org/bot%s/sendMessage" % token, timeout=20, json={
                "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)) + 1)
                continue
            break
        if r is not None and r.status_code == 200:
            ok += 1
        else:
            print("::warning::조각 %d 실패 %s %s" % (i + 1, r.status_code, r.text[:200]))
        time.sleep(1.2)
    print("전송 %d/%d" % (ok, len(chunks)))
    return 0 if ok == len(chunks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
