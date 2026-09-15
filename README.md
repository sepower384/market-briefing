# 마켓 브리핑 봇 (코인 + 도미넌스 + 미국/한국 주식 → 슬랙)

3시간마다 정기 브리핑, 급등/급락은 예외적으로 즉시 알림.
**API 키 0개 / 요금 0원.** 전부 공개 무료 API만 사용.

| 항목 | 출처 | 비용 |
|---|---|---|
| 코인 시세·캔들(BTC/ETH/XRP/ZEC) | Binance public REST | 무료·키없음 |
| 코인 가격 교차검증·백업 | Bitget public REST | 무료·키없음 |
| BTC/ETH/USDT 도미넌스, 전체 시총 | CoinGecko `/global` | 무료·키없음 |
| 미국·한국 주식/지수 | Yahoo Finance chart API | 무료·키없음 |
| 슬랙 발송 | Incoming Webhook | 무료 |
| 3시간 실행 | 윈도우 예약작업 | 무료 |

> 코인마켓캡은 API 키가 필요해서(무료 티어도 가입 필요) **CoinGecko로 대체**했습니다. 같은 도미넌스 수치를 키 없이 받습니다.

---

## 1. 설치 (이미 완료)

파이썬 3.10 + `requests` 만 있으면 끝. 추가 설치 없음.

## 2. 슬랙 연결 — 둘 중 하나

### 방법 A. Incoming Webhook (권장)
1. https://api.slack.com/apps → **Create New App** → From scratch
2. 앱 이름 아무거나, 워크스페이스 선택
3. 좌측 **Incoming Webhooks** → On → **Add New Webhook to Workspace** → 채널 선택
4. 나오는 `https://hooks.slack.com/services/...` 복사
5. **한 줄로 등록 + 즉시 테스트** (config.json 직접 편집 불필요)

```
powershell -ExecutionPolicy Bypass -File C:\dev\market-briefing\set_webhook.ps1 https://hooks.slack.com/services/XXX/YYY/ZZZ
```
저장 후 곧바로 슬랙에 테스트 메시지가 한 건 갑니다.
(환경변수 `SLACK_WEBHOOK_URL` 로 넣어도 인식합니다 — config 값이 우선.)

### 방법 B. Playwright (이미 크롬에 슬랙 로그인해 둔 경우)
```
"slack_mode": "playwright",
"playwright": {
  "slack_channel_url": "https://app.slack.com/client/T.../C...",
  "cdp_url": "http://127.0.0.1:9222"
}
```
크롬을 `--remote-debugging-port=9222` 로 띄워둬야 하고, 크롬이 꺼져 있으면 실패합니다.
백그라운드 3시간 자동실행에는 **방법 A가 훨씬 안정적**입니다.
`"slack_mode": "auto"` 로 두면 웹훅 먼저 → 실패 시 Playwright로 넘어갑니다.

### 텔레그램 동시 발송 (슬랙과 병행)
환경변수(깃허브 Actions 는 Secrets)만 넣으면 슬랙과 같은 내용이 텔레그램 슈퍼그룹 토픽으로도 갑니다. 없으면 조용히 건너뜁니다.

| 변수 | 뜻 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 공용 봇 토큰 (스트림별 토큰이 없을 때) |
| `TELEGRAM_BOT_TOKEN_BRIEF` / `TELEGRAM_BOT_TOKEN_WATCH` | 스트림 전용 봇 토큰 (있으면 공용보다 우선) |
| `TELEGRAM_CHAT_ID` | 슈퍼그룹 ID (`-100...`) |
| `TELEGRAM_TOPIC_BRIEF` | 📊 세력의 시장 보고서 토픽 스레드 ID (정기 브리핑) |
| `TELEGRAM_TOPIC_WATCH` | ⚡ 세력의 레이더망 토픽 스레드 ID (급등락 감시) |

- 슬랙 mrkdwn 을 텔레그램 HTML 로 변환(`telegram_sender.py`)해 보내고, 4096자를 넘으면 섹션 경계로 나눠 1초 간격으로 보냅니다.
- 사진은 quickchart.io 차트 URL(키 없음) 1장을 먼저 보내고, 실패해도 본문은 보냅니다. 브리핑=비트코인 7일, 감시=해당 종목 최근 흐름.
- 어려운 용어는 `glossary.py` 용어집으로 처음 한 번만 풀어 씁니다.
- 미리보기(전송 안 함, state.json 저장 안 함): `python briefing.py preview-telegram brief` / `... watch` → `outbox/preview_telegram_<mode>.html`, `outbox/preview_slack_<mode>.txt`
- 테스트: `python -m pytest tests`

## 3. 연결 테스트
```
python briefing.py test        # 슬랙에 테스트 메시지 1건
python briefing.py preview     # 슬랙 안 보내고 화면에만 브리핑 출력
python briefing.py brief       # 실제 브리핑 1회 발송
python briefing.py status      # 웹훅 설정/이력/outbox/최근 로그 한눈에
```

## 4. 자동 실행 등록 (창 안 뜸) — **등록 완료됨**

이미 아래 두 작업이 등록·검증되어 돌고 있습니다(2026-09-12 실행 결과코드 0, 창 안 뜸 확인).
재등록이 필요하면:
```
powershell -ExecutionPolicy Bypass -File C:\dev\market-briefing\install_tasks.ps1
```
- `MarketBriefing_3h` : 3시간마다 정기 브리핑 (00·03·06·09·12·15·18·21시)
- `MarketBriefing_Watch` : 10분마다 급등/급락 감시, 걸릴 때만 발송

제거: `powershell -ExecutionPolicy Bypass -File C:\dev\market-briefing\uninstall_tasks.ps1`

전부 `wscript.exe` + 숨김 vbs + `pythonw.exe` 조합이라 **검은 창이 절대 안 뜹니다.**

## 5. 브리핑에 담기는 것

**코인** — 현재가, 1H/24H/7D 등락, 강세·약세 판정, 판단 근거(RSI·MA50/200·MACD 크로스·볼린저·거래량 급증), 최근 20일 지지/저항

**도미넌스** — BTC.D / ETH.D / USDT.D, 24시간 변화(%p), 전체 시총.
BTC.D가 24h -0.5%p 이상 떨어지면 "알트로 자금 이동" 신호를 붙여 보냅니다.

**주식** — 등락률 순 정렬, MA200 이탈 / RSI 과열·침체 표시

## 6. 급등·급락 즉시 알림 기준 (`config.json` → `alerts`)

| 대상 | 기본 임계치 |
|---|---|
| 코인 1시간 | ±3% |
| 코인 24시간 | ±7% |
| 개별 주식 당일 | ±4% |
| 지수(코스피/나스닥 등) 당일 | ±2% |
| BTC 도미넌스 24시간 하락 | -0.5%p |
| 같은 종목 재알림 쿨다운 | 60분 |

숫자만 바꾸면 민감도가 바뀝니다. 알림이 너무 잦으면 임계치를 올리거나 쿨다운을 늘리세요.

## 7. 종목 바꾸기
`config.json` 의 `coins` / `us_stocks` / `kr_stocks` 에서 추가·삭제.
- 코인: 바이낸스 심볼 (`SOLUSDT`, `DOGEUSDT` …)
- 미국: 티커 그대로 (`AMD`, `PLTR` …), 지수는 `^GSPC` `^IXIC` `^DJI`
- 한국: 종목코드 + `.KS`(코스피) / `.KQ`(코스닥) — 예: 알테오젠 `196170.KQ`

## 8. 파일
```
market.py          시세 수집 + 지표 계산(RSI/MACD/볼린저/ATR/MA) + 신호 해석
slack_sender.py    슬랙 전송 (웹훅 / Playwright)
briefing.py        브리핑·알림 조립 및 실행 엔트리
config.json        종목·임계치·슬랙 설정
state.json         쿨다운 기록 + 도미넌스 이력(자동 생성)
briefing.log       실행 로그
outbox/            슬랙 전송 실패 시 브리핑 원문 보관 (YYYYMMDD.md)
set_webhook.ps1    슬랙 웹훅 URL 등록 + 테스트 발송
install_tasks.ps1  예약작업 등록 / uninstall_tasks.ps1  해제
```

## 9. 알아둘 점
- 도미넌스 24시간 변화는 **이력을 쌓아서** 계산합니다. 감시 작업이 10분마다 돌기 때문에 등록 후 24시간이 지나면 정확한 값이 나오고, 그 전에는 표시가 생략됩니다.
- Yahoo는 `previousClose`를 안 주고 `chartPreviousClose`가 조회 기간 시작 전 종가라, 전일 대비는 **일봉 종가 시계열로 직접** 계산합니다.
- 한국 주식은 장 마감 후에도 마지막 종가 기준으로 표시됩니다.
- **슬랙이 죽어도 내용은 안 날아갑니다.** 전송 실패하면 `outbox/YYYYMMDD.md` 에 브리핑 원문을 쌓고, 예약작업 자체는 정상 종료(결과코드 0)합니다. 웹훅을 나중에 등록해도 그동안 쌓인 내용을 파일로 볼 수 있습니다.
- 투자 판단 참고용 지표 요약일 뿐, 매매 신호가 아닙니다.
