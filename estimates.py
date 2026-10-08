"""컨센서스(추정치) 변화 수집.

시가총액 상위 종목의 올해·내년 컨센서스를 WiseReport에서 받아
현재 / 1주 전 / 1개월 전 값을 estimates.json에 저장한다.
하루 한 번이면 충분하다 (server.py가 알아서 돌린다).
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "estimates.json"
UNIVERSE = 400  # 시가총액 상위 몇 종목까지 볼지
WORKERS = 4     # 동시에 보내는 요청 수 (너무 늘리지 말 것)

WISE = "https://navercomp.wisereport.co.kr/v2/company"
# 추정치 표의 항목 코드 → 이름
ITEMS = {"121000": "sales", "121500": "op", "122710": "np", "312000": "eps"}


def get_json(url, referer=None):
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
        return json.loads(r.read())


def universe():
    """네이버 증권 시가총액 순위 (보통주만, 스팩 제외)."""
    # startIdx로 넘기는 방식이 불규칙해서 한 번에 넉넉히 받는다
    page = get_json("https://stock.naver.com/api/domestic/market/stock/default?tradeType=KRX"
                    f"&marketType=ALL&orderType=marketSum&startIdx=0&pageSize={int(UNIVERSE * 1.5)}")
    out = []
    for it in page:
        if it.get("type") != "ST" or not it["itemcode"].endswith("0") or "스팩" in it["itemname"]:
            continue
        out.append({
            "code": it["itemcode"], "name": it["itemname"],
            "market": "코스피" if it.get("sosok") == "0" else "코스닥",
            "price": float(it.get("nowPrice") or 0),
            "pct": float(it.get("prevChangeRate") or 0) * (-1 if it.get("upDownGb") in ("4", "5") else 1),
            "cap": float(it.get("marketSum") or 0),
        })
    out.sort(key=lambda s: s["cap"], reverse=True)
    return out[:UNIVERSE]


def consensus(code, yymm):
    ref = f"{WISE}/c1050001.aspx?cmp_cd={code}"
    d = get_json(f"{WISE}/ajax/c1050001_data.aspx?flag=4&cmp_cd={code}&finGubun=MAIN&frq=0"
                 f"&sDT={datetime.now():%Y%m%d}&yymm={yymm}&acc_cd=121500&chartType=svg", ref)
    vals = {}
    for row in d.get("JsonData", []):
        key = ITEMS.get(row["ACC_CD"])
        if key:
            # VAL1 현재, VAL2 1주 전, VAL3 1개월 전
            vals[key] = [row.get("VAL1"), row.get("VAL2"), row.get("VAL3")]
    return vals


def collect():
    year = datetime.now().year
    years = [f"{year}12", f"{year + 1}12"]
    stocks = universe()

    def one(s):
        for y in years:
            try:
                s[y] = consensus(s["code"], y)
            except Exception:
                s[y] = {}
            time.sleep(0.2)
        return s

    with ThreadPoolExecutor(WORKERS) as ex:
        stocks = list(ex.map(one, stocks))
    data = {"updated": int(time.time()), "years": years,
            "stocks": [s for s in stocks if any(s.get(y) for y in years)]}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    return data


if __name__ == "__main__":
    t = time.time()
    d = collect()
    print(f"{len(d['stocks'])}종목, {time.time() - t:.0f}초")
