# -*- coding: utf-8 -*-
"""
market.py - 무료 공개 API만으로 시세/기술적분석 데이터를 모으는 모듈.
API 키 필요 없음. 요금 0원.
  - 코인 시세/캔들 : Binance public REST (백업: Bitget public REST)
  - BTC 도미넌스   : CoinGecko public /global
  - 미국/한국 주식 : Yahoo Finance public chart API
"""
import math
import time
import datetime as dt

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) market-briefing/1.0"}
TIMEOUT = 20

BINANCE = "https://api.binance.com"
BITGET = "https://api.bitget.com"
COINGECKO = "https://api.coingecko.com/api/v3"
YAHOO_HOSTS = ["https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"]


def _get(url, params=None, tries=3, sleep=1.2):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json()
            last = "HTTP %s: %s" % (r.status_code, r.text[:200])
        except Exception as e:  # 네트워크/SSL 일시 오류
            last = repr(e)
        time.sleep(sleep * (i + 1))
    raise RuntimeError("GET failed %s :: %s" % (url, last))


# ------------------------------------------------------------------ 지표 계산
def sma(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema_series(values, period):
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(values, period=14):
    """Wilder 방식 RSI."""
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag, al = sum(gains) / period, sum(losses) / period
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0
    return 100 - (100 / (1 + ag / al))


def macd(values, fast=12, slow=26, signal=9):
    if len(values) < slow + signal:
        return None
    ef, es = ema_series(values, fast), ema_series(values, slow)
    ef = ef[-len(es):]
    line = [a - b for a, b in zip(ef, es)]
    sig = ema_series(line, signal)
    if not sig:
        return None
    hist = line[-1] - sig[-1]
    prev_hist = line[-2] - sig[-2] if len(line) > 1 and len(sig) > 1 else hist
    return {"macd": line[-1], "signal": sig[-1], "hist": hist, "prev_hist": prev_hist}


def bollinger(values, period=20, mult=2.0):
    if len(values) < period:
        return None
    win = values[-period:]
    mid = sum(win) / period
    sd = math.sqrt(sum((v - mid) ** 2 for v in win) / period)
    up, lo = mid + mult * sd, mid - mult * sd
    pos = (values[-1] - lo) / (up - lo) * 100 if up > lo else 50.0
    return {"upper": up, "mid": mid, "lower": lo, "pct_b": pos}


def atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


# ------------------------------------------------------------------ 코인
def binance_klines(symbol, interval="1h", limit=300):
    raw = _get(BINANCE + "/api/v3/klines",
               {"symbol": symbol, "interval": interval, "limit": limit})
    return {
        "open": [float(k[1]) for k in raw],
        "high": [float(k[2]) for k in raw],
        "low": [float(k[3]) for k in raw],
        "close": [float(k[4]) for k in raw],
        "volume": [float(k[5]) for k in raw],
        "time": [int(k[0]) for k in raw],
    }


def binance_ticker(symbol):
    d = _get(BINANCE + "/api/v3/ticker/24hr", {"symbol": symbol})
    return {
        "price": float(d["lastPrice"]),
        "change_24h": float(d["priceChangePercent"]),
        "high_24h": float(d["highPrice"]),
        "low_24h": float(d["lowPrice"]),
        "quote_volume": float(d["quoteVolume"]),
    }


def bitget_ticker(symbol):
    """Binance 장애 시 백업 + 가격 교차검증용."""
    d = _get(BITGET + "/api/v2/spot/market/tickers", {"symbol": symbol})
    row = d["data"][0]
    return {
        "price": float(row["lastPr"]),
        "change_24h": float(row.get("change24h", 0)) * 100,
        "high_24h": float(row["high24h"]),
        "low_24h": float(row["low24h"]),
        "quote_volume": float(row.get("usdtVolume", 0)),
    }


def coin_snapshot(symbol, with_ta=True):
    """코인 1종의 시세 + 기술적 분석 묶음."""
    out = {"symbol": symbol, "source": "binance", "error": None}
    try:
        out.update(binance_ticker(symbol))
    except Exception:
        try:
            out.update(bitget_ticker(symbol))
            out["source"] = "bitget"
        except Exception as e:
            out["error"] = "시세 조회 실패: %s" % e
            return out
    try:
        out["bitget_price"] = bitget_ticker(symbol)["price"]
    except Exception:
        out["bitget_price"] = None

    if not with_ta:
        return out

    try:
        h1 = binance_klines(symbol, "1h", 300)
        d1 = binance_klines(symbol, "1d", 260)
        c1, cd = h1["close"], d1["close"]

        out["change_1h"] = (c1[-1] / c1[-2] - 1) * 100 if len(c1) > 1 else 0.0
        out["change_4h"] = (c1[-1] / c1[-5] - 1) * 100 if len(c1) > 5 else 0.0
        out["change_7d"] = (cd[-1] / cd[-8] - 1) * 100 if len(cd) > 8 else None
        out["change_30d"] = (cd[-1] / cd[-31] - 1) * 100 if len(cd) > 31 else None

        vol_base = sum(h1["volume"][-25:-1]) / 24 if len(h1["volume"]) > 25 else 0
        out["ta"] = {
            "rsi_1h": rsi(c1, 14),
            "rsi_1d": rsi(cd, 14),
            "macd_1h": macd(c1),
            "macd_1d": macd(cd),
            "bb_1h": bollinger(c1),
            "ma20": sma(cd, 20),
            "ma50": sma(cd, 50),
            "ma200": sma(cd, 200),
            "atr_1d": atr(d1["high"], d1["low"], cd, 14),
            "high_52w": max(d1["high"]),
            "low_52w": min(d1["low"]),
            "support": min(d1["low"][-20:]),
            "resistance": max(d1["high"][-20:]),
            "vol_ratio_1h": (h1["volume"][-1] / vol_base) if vol_base > 0 else None,
        }
    except Exception as e:
        out["ta"] = None
        out["ta_error"] = str(e)
    return out


# ------------------------------------------------------------------ 도미넌스
def dominance():
    d = _get(COINGECKO + "/global")["data"]
    mcp = d["market_cap_percentage"]
    return {
        "btc": mcp.get("btc"),
        "eth": mcp.get("eth"),
        "usdt": mcp.get("usdt"),
        "total_mcap_usd": d["total_market_cap"]["usd"],
        "total_mcap_change_24h": d.get("market_cap_change_percentage_24h_usd"),
    }


# ------------------------------------------------------------------ 주식
def yahoo_chart(ticker, rng="1y", interval="1d"):
    last = None
    for host in YAHOO_HOSTS:
        try:
            j = _get(host + "/v8/finance/chart/" + ticker,
                     {"range": rng, "interval": interval, "includePrePost": "false"},
                     tries=2)
            res = j["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            stamps = res.get("timestamp") or []
            closes, highs, lows, times = [], [], [], []
            for i, c in enumerate(q.get("close") or []):
                if c is None:
                    continue
                h = (q.get("high") or [None])[i] if i < len(q.get("high") or []) else None
                l = (q.get("low") or [None])[i] if i < len(q.get("low") or []) else None
                closes.append(c)
                highs.append(h if h is not None else c)
                lows.append(l if l is not None else c)
                times.append(stamps[i] if i < len(stamps) else None)
            return {"meta": res["meta"], "close": closes, "high": highs,
                    "low": lows, "time": times}
        except Exception as e:
            last = e
    raise RuntimeError("yahoo 실패 %s: %s" % (ticker, last))


def _session_date(ts, gmtoffset):
    """거래소 현지 날짜(YYYY-MM-DD)."""
    if ts is None:
        return None
    return (dt.datetime.utcfromtimestamp(ts + (gmtoffset or 0))).strftime("%Y-%m-%d")


def _day_change(ch):
    """
    Yahoo meta 의 previousClose 는 없고 chartPreviousClose 는 '조회 기간 시작 전 종가'라
    그대로 쓰면 등락률이 엉뚱하게 나온다. 일봉 종가 시계열로 직접 계산한다.
    반환: (현재가, 전일종가, 등락률%)
    """
    m, closes, times = ch["meta"], ch["close"], ch.get("time") or []
    if not closes:
        return None, None, None
    price = m.get("regularMarketPrice")
    if price is None:
        price = closes[-1]

    gmt = m.get("gmtoffset", 0)
    last_bar_day = _session_date(times[-1] if times else None, gmt)
    now_day = _session_date(m.get("regularMarketTime"), gmt)

    # 마지막 봉이 '오늘 현재가가 속한 봉'이면 전일종가는 그 앞 봉.
    if last_bar_day and now_day and last_bar_day == now_day and len(closes) > 1:
        prev = closes[-2]
    elif abs(price - closes[-1]) < max(closes[-1] * 1e-6, 1e-9) and len(closes) > 1:
        prev = closes[-2]
    else:
        prev = closes[-1]

    if not prev:
        return price, None, None
    return price, prev, (price / prev - 1) * 100


def stock_snapshot(ticker, with_ta=True):
    out = {"ticker": ticker, "error": None}
    try:
        ch = yahoo_chart(ticker, "1y", "1d")
    except Exception as e:
        out["error"] = str(e)
        return out

    m, closes = ch["meta"], ch["close"]
    price, prev, chg = _day_change(ch)
    out["price"] = price
    out["prev_close"] = prev
    out["currency"] = m.get("currency", "")
    out["change_day"] = chg
    out["market_state"] = m.get("marketState", "")

    if with_ta and len(closes) > 30:
        out["change_5d"] = (closes[-1] / closes[-6] - 1) * 100 if len(closes) > 6 else None
        out["change_1m"] = (closes[-1] / closes[-22] - 1) * 100 if len(closes) > 22 else None
        out["ta"] = {
            "rsi_1d": rsi(closes, 14),
            "ma20": sma(closes, 20),
            "ma50": sma(closes, 50),
            "ma200": sma(closes, 200),
            "macd_1d": macd(closes),
            "high_52w": max(ch["high"]),
            "low_52w": min(ch["low"]),
        }
    return out


def stock_quick(ticker):
    """급등락 감시용 - 당일 등락률만 빠르게."""
    try:
        ch = yahoo_chart(ticker, "1mo", "1d")
        price, prev, chg = _day_change(ch)
        if price is None or chg is None:
            return None
        return {
            "ticker": ticker,
            "price": price,
            "change_day": chg,
            "market_state": ch["meta"].get("marketState", ""),
            "currency": ch["meta"].get("currency", ""),
        }
    except Exception:
        return None


# ------------------------------------------------------------------ 해석
def read_signal(snap):
    """지표를 사람 말로 번역. (강세/중립/약세 + 근거)"""
    ta = snap.get("ta")
    if not ta:
        return {"verdict": "판단보류", "score": 0, "reasons": ["지표 데이터 없음"]}

    score, reasons = 0, []
    price = snap.get("price")

    r = ta.get("rsi_1d")
    if r is not None:
        if r >= 70:
            score -= 1
            reasons.append("일봉 RSI %.0f 과매수" % r)
        elif r <= 30:
            score += 1
            reasons.append("일봉 RSI %.0f 과매도" % r)
        else:
            reasons.append("일봉 RSI %.0f 중립" % r)

    for key, label in (("ma50", "MA50"), ("ma200", "MA200")):
        v = ta.get(key)
        if v and price:
            gap = (price / v - 1) * 100
            if gap >= 0:
                score += 1
                reasons.append("%s 위 (+%.1f%%)" % (label, gap))
            else:
                score -= 1
                reasons.append("%s 아래 (%.1f%%)" % (label, gap))

    mk = ta.get("macd_1d")
    if mk:
        if mk["hist"] > 0 and mk["prev_hist"] <= 0:
            score += 2
            reasons.append("MACD 골든크로스 발생")
        elif mk["hist"] < 0 and mk["prev_hist"] >= 0:
            score -= 2
            reasons.append("MACD 데드크로스 발생")
        elif mk["hist"] > 0:
            score += 1
            reasons.append("MACD 양(+) 유지")
        else:
            score -= 1
            reasons.append("MACD 음(-) 유지")

    bb = ta.get("bb_1h")
    if bb:
        if bb["pct_b"] >= 100:
            reasons.append("1H 볼린저 상단 이탈")
        elif bb["pct_b"] <= 0:
            reasons.append("1H 볼린저 하단 이탈")

    vr = ta.get("vol_ratio_1h")
    if vr and vr >= 2:
        reasons.append("직전 1시간 거래량 평소의 %.1f배" % vr)

    if score >= 3:
        verdict = "강세"
    elif score >= 1:
        verdict = "약강세"
    elif score <= -3:
        verdict = "약세"
    elif score <= -1:
        verdict = "약약세"
    else:
        verdict = "중립"
    return {"verdict": verdict, "score": score, "reasons": reasons}


def now_kst(offset_hours=9):
    return dt.datetime.utcnow() + dt.timedelta(hours=offset_hours)
