# -*- coding: utf-8 -*-
"""
glossary.py - 어려운 용어를 '처음 나올 때 한 번만' 풀어 쓰는 용어집.

    ex = Explainer()
    ex.t("도미넌스")  -> "도미넌스(전체 코인 시장에서 비트코인이 차지하는 비중)"
    ex.t("도미넌스")  -> "도미넌스"            # 같은 메시지 안에서 두 번째부터는 그대로

메시지 1건(브리핑 1회, 알림 1회)마다 Explainer 를 새로 만들어 쓴다.
슬랙·텔레그램이 같은 문장을 쓰므로 설명도 양쪽에 똑같이 들어간다.
"""

GLOSSARY = {
    "도미넌스": "전체 코인 시장에서 비트코인이 차지하는 비중",
    "알트코인": "비트코인을 뺀 나머지 코인",
    "스테이블코인": "달러 가치에 1:1로 묶어 둔 코인",
    "시가총액": "가격×발행량으로 계산한 전체 몸값",
    "%p": "퍼센트 수치끼리 뺀 차이",
    "200일선": "최근 200일 동안의 평균 가격",
    "RSI": "최근 상승·하락 힘을 0~100으로 나타낸 지표",
    "과열": "짧은 기간에 많이 올라 잠시 쉬어 가기 쉬운 상태",
    "과매도": "짧은 기간에 많이 빠져 반등이 나오기 쉬운 상태",
    "골든크로스": "단기 흐름이 장기 흐름을 위로 뚫는 상승 전환 신호",
    "데드크로스": "단기 흐름이 장기 흐름을 아래로 뚫는 하락 전환 신호",
    "지지선": "최근 20일 동안 가격이 버텨 준 가장 낮은 자리",
    "저항선": "최근 20일 동안 가격이 넘지 못한 가장 높은 자리",
    "보합": "거의 제자리",
    "거래량": "실제로 사고판 양",
    "정규장": "정식 거래 시간",
    "프리마켓": "정규장 시작 전 거래",
    "애프터마켓": "정규장이 끝난 뒤 거래",
    "관망": "사지도 팔지도 않고 지켜보는 것",
}


class Explainer(object):
    """한 메시지 안에서 용어별로 딱 한 번만 설명을 붙인다."""

    def __init__(self, glossary=None):
        self.glossary = glossary or GLOSSARY
        self.used = set()

    def t(self, term, shown=None):
        """term 설명을 처음 한 번만 붙여 돌려준다. shown 을 주면 표시 글자를 바꾼다(예: 숫자+%p)."""
        shown = term if shown is None else shown
        desc = self.glossary.get(term)
        if not desc or term in self.used:
            return shown
        self.used.add(term)
        return "%s(%s)" % (shown, desc)


# ------------------------------------------------------------------ 조사
def _has_batchim(word):
    """마지막 글자에 받침이 있는지. 숫자/영문 끝도 읽는 소리로 판정."""
    if not word:
        return False
    ch = word.strip()[-1:]
    if not ch:
        return False
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    # 숫자: 영(0) 일(1) 삼(3) 육(6) 칠(7) 팔(8) 십 → 받침 있음
    if ch.isdigit():
        return ch in "013678"
    # 영문 약어: L M N R 로 끝나면 받침 소리
    if ch.isalpha():
        return ch.upper() in "LMNR"
    return False


def josa(word, pair):
    """josa('비트코인', '은/는') -> '비트코인은'"""
    a, b = pair.split("/")
    return word + (a if _has_batchim(word) else b)
