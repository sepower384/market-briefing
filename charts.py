# -*- coding: utf-8 -*-
"""
charts.py - quickchart.io 로 가격 선차트 이미지 URL 을 만든다 (키 없음, 무료).

텔레그램 sendPhoto 에 URL 을 그대로 넘기면 텔레그램 서버가 이미지를 받아 간다.
차트 제목은 quickchart 폰트에 한글이 없을 수 있어 영문으로 쓰고, 한글 설명은 캡션에 넣는다.
"""
import datetime as dt
import json
from urllib.parse import quote

BASE = "https://quickchart.io/chart"
MAX_POINTS = 48
MAX_URL = 2000


def downsample(values, n):
    """마지막 값은 반드시 포함하면서 n개 이하로 고르게 추린다. 인덱스 목록 반환."""
    size = len(values)
    if size <= n:
        return list(range(size))
    step = (size - 1) / float(n - 1)
    idx = sorted(set(int(round(i * step)) for i in range(n)))
    if idx[-1] != size - 1:
        idx[-1] = size - 1
    return idx


def _round_price(v):
    if v >= 1000:
        return int(round(v))
    if v >= 1:
        return round(v, 2)
    return float("%.5g" % v)


def line_chart_url(closes, times_ms=None, title="", label_every=6, tz_hours=9,
                   label_fmt="%-m/%-d", max_points=MAX_POINTS, max_url=MAX_URL):
    """
    closes: 가격 리스트, times_ms: 같은 길이의 epoch 밀리초 리스트(없으면 라벨 없음).
    URL 이 max_url 을 넘으면 점 개수를 줄여 다시 만든다. 데이터가 모자라면 None.
    """
    closes = [c for c in (closes or []) if c is not None]
    if len(closes) < 2:
        return None
    n0 = n = min(max_points, len(closes))
    while n >= 8:
        idx = downsample(closes, n)
        data = [_round_price(closes[i]) for i in idx]
        labels = []
        # label_every 는 '출력 점' 기준. 점을 줄였으면 라벨 간격도 같은 비율로 줄인다.
        every = max(1, int(round(label_every * n / float(n0))))
        for k, i in enumerate(idx):
            if times_ms and i < len(times_ms) and times_ms[i] and k % every == 0:
                t = dt.datetime.utcfromtimestamp(times_ms[i] / 1000.0) + dt.timedelta(hours=tz_hours)
                labels.append(_fmt_time(t, label_fmt))
            else:
                labels.append("")
        up = data[-1] >= data[0]
        # quickchart 기본값은 y축이 0부터라 가격 변화가 납작해진다 → 데이터 범위에 여백만 조금 준다
        lo, hi = min(data), max(data)
        pad = (hi - lo) * 0.08 or abs(hi) * 0.01 or 1
        y_min, y_max = _round_price(max(lo - pad, 0)), _round_price(hi + pad)
        cfg = {
            "type": "line",
            "data": {"labels": labels, "datasets": [{
                "data": data, "fill": False, "borderWidth": 2, "pointRadius": 0, "lineTension": 0,
                "borderColor": "#16a34a" if up else "#dc2626"}]},
            "options": {"legend": {"display": False},
                        "title": {"display": bool(title), "text": title},
                        "scales": {"yAxes": [{"ticks": {"min": y_min, "max": y_max}}]}},
        }
        c = json.dumps(cfg, separators=(",", ":"), ensure_ascii=False)
        url = "%s?w=800&h=400&bkg=white&c=%s" % (BASE, quote(c, safe=""))
        if len(url) <= max_url:
            return url
        n = int(n * 0.8)
    return None


def _fmt_time(t, fmt):
    # 윈도우 strftime 은 %-m 을 모르므로 직접 처리
    return (fmt.replace("%-m", str(t.month)).replace("%-d", str(t.day))
            .replace("%H", "%02d" % t.hour).replace("%M", "%02d" % t.minute))
