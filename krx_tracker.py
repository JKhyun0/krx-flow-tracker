# -*- coding: utf-8 -*-
"""
KRX 투자자별 수급 트래커
------------------------
삼성전자(005930) · SK하이닉스(000660) · 삼성전기(009150)의
투자자별(개인 / 외국인 / 연기금 / 금융투자) 일별 매수·매도·순매수 대금을
KRX 정보데이터시스템(data.krx.co.kr)에서 수집해 시계열로 기록합니다.

- 첫 실행: 과거 약 6개월치를 한 번에 소급(backfill)해서 채웁니다.
- 이후 실행: 마지막 기록일 다음 날부터 오늘까지만 이어서 수집합니다.
- 금액 단위: 원(KRW). 대시보드에서는 억원으로 환산해 표시합니다.

필요 환경변수(GitHub Secrets로 설정): KRX_ID, KRX_PW
  → data.krx.co.kr 무료 회원 계정. 설치 가이드 3단계 참고.

결과 파일:
  data/history.csv   (엑셀로 열 수 있는 원본)
  data/history.json  (대시보드가 읽는 파일)
"""

import csv
import json
import os
import sys
import datetime

import pandas as pd
from pykrx import stock

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
HISTORY_CSV = os.path.join(DATA_DIR, "history.csv")
HISTORY_JSON = os.path.join(DATA_DIR, "history.json")

TICKERS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "009150": "삼성전기",
}

# KRX 상세 구분 중 추적 대상 (pykrx 버전에 따라 '연기금'/'연기금 등' 표기가 달라 둘 다 대응)
CATEGORY_ALIASES = {
    "개인": ["개인"],
    "외국인": ["외국인"],
    "연기금": ["연기금", "연기금 등", "연기금등"],
    "금융투자": ["금융투자"],
}

BACKFILL_DAYS = 185  # 첫 실행 시 소급 기간 (약 6개월)

FIELDS = ["date", "code", "name", "investor", "buy_krw", "sell_krw", "net_krw"]


def pick_col(df, aliases):
    for a in aliases:
        if a in df.columns:
            return a
    return None


def load_history():
    if not os.path.exists(HISTORY_CSV):
        return []
    with open(HISTORY_CSV, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:  # CSV는 문자열로 읽히므로 숫자 필드를 복원
        for k in ("buy_krw", "sell_krw", "net_krw"):
            try:
                r[k] = float(r[k])
            except (TypeError, ValueError):
                r[k] = None
    return rows


def fetch_range(code, start, end):
    """start~end 기간의 투자자별 매수/매도 거래대금을 롱 포맷 행 리스트로 반환"""
    s, e = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    buy = stock.get_market_trading_value_by_date(s, e, code, on="매수", detail=True)
    sell = stock.get_market_trading_value_by_date(s, e, code, on="매도", detail=True)
    if buy is None or buy.empty:
        return []

    rows = []
    for dt in buy.index:
        date_str = pd.Timestamp(dt).strftime("%Y-%m-%d")
        for cat, aliases in CATEGORY_ALIASES.items():
            cb = pick_col(buy, aliases)
            cs = pick_col(sell, aliases)
            if cb is None:
                continue
            b = float(buy.loc[dt, cb])
            v = float(sell.loc[dt, cs]) if (cs is not None and dt in sell.index) else None
            rows.append({
                "date": date_str,
                "code": code,
                "name": TICKERS[code],
                "investor": cat,
                "buy_krw": b,
                "sell_krw": v,
                "net_krw": (b - v) if v is not None else None,
            })
    return rows


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    history = load_history()
    today = datetime.date.today()

    total_new = 0
    errors = []

    for code in TICKERS:
        dates = sorted({h["date"] for h in history if h["code"] == code})
        if dates:
            start = datetime.date.fromisoformat(dates[-1]) + datetime.timedelta(days=1)
        else:
            start = today - datetime.timedelta(days=BACKFILL_DAYS)
            print(f"[안내] {TICKERS[code]}({code}) 첫 실행 → {start}부터 소급 수집")

        if start > today:
            print(f"[안내] {TICKERS[code]}({code}) 최신 상태, 건너뜀")
            continue

        try:
            new_rows = fetch_range(code, start, today)
        except Exception as e:
            errors.append(f"{TICKERS[code]}({code}): {e}")
            continue

        # 혹시 모를 중복 방지
        existing = {(h["code"], h["date"], h["investor"]) for h in history}
        new_rows = [r for r in new_rows
                    if (r["code"], r["date"], r["investor"]) not in existing]
        history.extend(new_rows)
        total_new += len(new_rows)
        print(f"[성공] {TICKERS[code]}({code}) {start}~{today} — {len(new_rows)}행 추가")

    history.sort(key=lambda h: (h["date"], h["code"], h["investor"]))

    with open(HISTORY_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for h in history:
            w.writerow({k: h.get(k, "") for k in FIELDS})

    with open(HISTORY_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "generated_utc": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds"),
            "source": "KRX 정보데이터시스템(data.krx.co.kr) 투자자별 거래실적, 거래대금(원) 기준",
            "rows": history,
        }, f, ensure_ascii=False)

    if errors:
        print("\n".join("[오류] " + e for e in errors))
        if total_new == 0:
            # 아무것도 수집하지 못했으면 실패 처리 → Actions에 빨간 X 표시
            sys.exit(1)

    print(f"완료. 신규 {total_new}행, 누적 {len(history)}행.")


if __name__ == "__main__":
    main()
