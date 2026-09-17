# -*- coding: utf-8 -*-
"""
news_lookup.py - 급등락 알림에 "왜 움직였나" 뉴스 한 줄을 붙인다.

구글 뉴스 RSS 검색(키 불필요)으로 최근 기사 중 '움직임을 설명하는 제목'을 고른다.
    headline("지캐시") -> {"title": "...", "url": "...", "source": "블루밍비트", "age_h": 1.2} 또는 None
실패는 전부 None 으로 삼킨다 — 뉴스가 없다고 알림이 막히면 안 된다.
"""
import re
import datetime as dt
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

RSS = "https://news.google.com/rss/search"
UA = {"User-Agent": "Mozilla/5.0 (market-briefing)"}

# 가격이 움직인 이유를 담았을 가능성이 높은 단어
MOVE_WORDS = ("급등", "급락", "폭등", "폭락", "껑충", "뚝", "상승", "하락", "반등", "신고가", "최고치",
              "이유", "왜", "때문", "여파", "호재", "악재", "청산", "상장", "승인", "해킹")
# 시황 모음·전망글은 '이유'가 아니다
NOISE_WORDS = ("전망", "예측", "추천", "칼럼", "포토", "[표]", "마감시황", "주간", "Forecast", "Prediction")


def _age_hours(pub):
    try:
        t = parsedate_to_datetime(pub)
        return (dt.datetime.now(dt.timezone.utc) - t).total_seconds() / 3600
    except Exception:
        return 99.0


def _clean_title(title, source):
    # 구글 뉴스 제목 끝의 " - 매체명" 제거
    if source and title.endswith(" - " + source):
        title = title[: -len(" - " + source)]
    return re.sub(r"\s+", " ", title).strip()


def pick(items, name, max_age_h=18):
    """후보 [(title, url, source, age_h)] 중 가장 '이유'다운 기사 1건."""
    best, best_s = None, -1e9
    for title, url, source, age in items:
        if age > max_age_h or name not in title:
            continue
        if any(n in title for n in NOISE_WORDS):
            continue
        s = -age * 1.5
        s += 6 * sum(1 for w in MOVE_WORDS if w in title)
        if best is None or s > best_s:
            best, best_s = {"title": title, "url": url, "source": source, "age_h": round(age, 1)}, s
    return best


def fetch(query, get=requests.get):
    r = get(RSS, params={"q": query + " when:1d", "hl": "ko", "gl": "KR", "ceid": "KR:ko"},
            headers=UA, timeout=12)
    root = ET.fromstring(r.content)
    out = []
    for it in root.findall(".//item")[:40]:
        source = it.findtext("source") or ""
        title = _clean_title(it.findtext("title") or "", source)
        out.append((title, it.findtext("link") or "", source, _age_hours(it.findtext("pubDate") or "")))
    return out


def headline(name, query=None, get=requests.get):
    try:
        return pick(fetch(query or name, get=get), name)
    except Exception:
        return None
