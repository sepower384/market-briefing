# -*- coding: utf-8 -*-
"""
slack_login.py - 봇 전용 크롬 프로필에 슬랙 로그인을 1회만 시켜두는 설정 마법사.

  python slack_login.py

창이 하나 뜬다. 거기서
  1) 슬랙에 로그인하고
  2) 브리핑 받을 채널을 클릭한 다음
  3) 그 창을 그냥 닫으면 된다.
닫는 순간 채널 주소를 config.json 에 자동 저장하고 테스트 메시지를 보낸다.

이 프로필은 강회장님이 평소 쓰는 크롬과 완전히 별개라서,
평소 크롬을 껐다 켜든 말든 봇은 영향받지 않는다.
"""
import io
import json
import os
import re
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(BASE, "bot_chrome")
CONFIG = os.path.join(BASE, "config.json")

CHANNEL_RE = re.compile(r"^https://app\.slack\.com/client/T[A-Z0-9]+/C[A-Z0-9]+")


def main():
    from playwright.sync_api import sync_playwright

    if not os.path.isdir(PROFILE):
        os.makedirs(PROFILE)

    print("=" * 62)
    print(" 크롬 창을 띄웁니다. 창에서 이렇게 하세요:")
    print("   1) 슬랙에 로그인")
    print("   2) 브리핑 받을 채널을 클릭")
    print("   3) 창을 그냥 닫기  (닫으면 자동으로 저장됩니다)")
    print("=" * 62)

    last_channel = [None]

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE, headless=False, channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://app.slack.com/client", wait_until="domcontentloaded", timeout=120000)

        # 창이 닫힐 때까지 현재 URL을 계속 훑어서 마지막 채널 주소를 기억한다.
        while True:
            try:
                if not ctx.pages:
                    break
                for pg in ctx.pages:
                    u = pg.url
                    if CHANNEL_RE.match(u):
                        base_url = CHANNEL_RE.match(u).group(0)
                        if base_url != last_channel[0]:
                            last_channel[0] = base_url
                            print("  채널 감지: " + base_url)
                time.sleep(2)
            except Exception:
                break

        try:
            ctx.close()
        except Exception:
            pass

    if not last_channel[0]:
        print("")
        print("채널을 못 잡았습니다. 창에서 채널을 한 번 클릭한 뒤 닫아야 합니다.")
        print("다시 실행하세요:  python slack_login.py")
        return 1

    cfg = json.load(io.open(CONFIG, encoding="utf-8"))
    cfg["slack_mode"] = "playwright"
    pw = cfg.get("playwright") or {}
    pw["slack_channel_url"] = last_channel[0]
    pw["chrome_user_data_dir"] = PROFILE
    pw["cdp_url"] = ""          # 켜둔 크롬에 붙는 경로는 안 쓴다
    pw["headless"] = True
    cfg["playwright"] = pw
    with io.open(CONFIG, "w", encoding="utf-8") as f:
        f.write(json.dumps(cfg, ensure_ascii=False, indent=2))

    print("")
    print("저장 완료 -> " + last_channel[0])
    print("테스트 메시지 보내는 중...")

    sys.path.insert(0, BASE)
    import slack_sender
    slack_sender.send(cfg, "마켓 브리핑 봇 연결 완료. 이제 3시간마다 브리핑이 옵니다.")
    print("슬랙 확인해보세요. 도착했으면 끝입니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
