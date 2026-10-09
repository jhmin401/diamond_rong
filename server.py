"""나만의 대시보드 사이트 서버.

http://localhost:8080 (같은 와이파이의 폰: http://PC주소:8080)
  /             화면 (index.html)
  /data/*.json  시세·신고가·매크로·뉴스·추정치 JSON (GitHub Pages와 같은 주소)
"""
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
PORT = 8080
PREFIX = "/diamond_rong"
CACHE_SEC = 60

# (구역, 이름, 야후 심볼)
SYMBOLS = {
    "kr_index": [("코스피", "^KS11"), ("코스닥", "^KQ11"), ("원/달러", "KRW=X")],
    "kr_stock": [("삼성전자", "005930.KS"), ("SK하이닉스", "000660.KS"), ("펨트론", "168360.KQ")],
    "us_index": [("S&P 500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI"),
                 ("필라델피아 반도체", "^SOX"), ("나스닥 선물", "NQ=F"), ("VIX 공포지수", "^VIX")],
    "us_stock": [("마이크론", "MU"), ("엔비디아", "NVDA"), ("TSMC", "TSM")],
}

_cache = {"at": 0, "data": None}


def fetch(name, symbol):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range=1d&interval=5m")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            res = json.loads(r.read())["chart"]["result"][0]
        meta = res["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if c]
        period = dict(meta.get("currentTradingPeriod", {}).get("regular", {}))
        if symbol in ("^KS11", "^KQ11") and period.get("end"):
            period["end"] += 1800  # 야후는 15:00으로 주지만 국장은 15:30 마감
        return {
            "name": name, "symbol": symbol, "price": price, "prev": prev,
            "change": price - prev if prev else None,
            "pct": (price / prev - 1) * 100 if prev else None,
            "currency": meta.get("currency"),
            "spark": closes[::3],  # 15분 간격으로 줄이기
            "time": meta.get("regularMarketTime"),
            "open": period.get("start"), "close": period.get("end"),
        }
    except Exception as e:
        return {"name": name, "symbol": symbol, "error": str(e)}


def market():
    if _cache["data"] and time.time() - _cache["at"] < CACHE_SEC:
        return _cache["data"]
    jobs = [(g, n, s) for g, items in SYMBOLS.items() for n, s in items]
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(lambda j: fetch(j[1], j[2]), jobs))
    data = {g: [] for g in SYMBOLS}
    for (g, _, _), r in zip(jobs, results):
        data[g].append(r)
    data["updated"] = int(time.time())
    _cache.update(at=time.time(), data=data)
    return data


_high_cache = {"at": 0, "data": None}


def new_highs():
    """오늘 52주 신고가를 찍은 국내 종목 (네이버 증권)."""
    if _high_cache["data"] and time.time() - _high_cache["at"] < CACHE_SEC:
        return _high_cache["data"]
    # startIdx로 넘기는 방식이 불규칙해서 한 번에 넉넉히 받는다
    url = ("https://stock.naver.com/api/domestic/market/stock/default?tradeType=KRX"
           "&marketType=ALL&orderType=high52week&startIdx=0&pageSize=1000")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        items = json.loads(r.read())

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    stocks = [{
        "name": it["itemname"], "code": it["itemcode"],
        "market": "코스피" if it.get("sosok") == "0" else "코스닥",
        "price": num(it.get("nowPrice")), "pct": num(it.get("prevChangeRate")),
        "change": num(it.get("prevChangePrice")), "high": num(it.get("highPrice")),
        "high52": num(it.get("week52HighPrice")), "amount": num(it.get("tradeAmount")),
        "cap": num(it.get("marketSum")),
    } for it in items]
    # upDownGb 4(하한)·5(하락)인데 변동폭이 양수로 오면 부호를 맞춘다
    signs = {it["itemcode"]: it.get("upDownGb") for it in items}
    for s in stocks:
        if signs.get(s["code"]) in ("4", "5") and s["change"] and s["change"] > 0:
            s["change"] = -s["change"]
            if s["pct"] and s["pct"] > 0:
                s["pct"] = -s["pct"]
    stocks.sort(key=lambda s: s["pct"] or 0, reverse=True)
    data = {"stocks": stocks, "updated": int(time.time())}
    _high_cache.update(at=time.time(), data=data)
    return data


_news_cache = {"at": 0, "data": None}
NEWS_API = "https://m.stock.naver.com/front-api/news/category?category={}&page={}&pageSize=20"
# 실시간 뉴스 중에서 이 말머리가 붙은 것만 '속보'로 남긴다
FLASH_TAGS = ("속보", "긴급", "1보", "단독")


def news():
    """네이버 증권 주요뉴스 + 실시간 뉴스 중 속보만."""
    if _news_cache["data"] and time.time() - _news_cache["at"] < CACHE_SEC:
        return _news_cache["data"]

    def get(category, pages):
        # 한 번에 많이 달라고 하면 400이 나서 20건씩 나눠 받는다
        out = []
        for page in range(1, pages + 1):
            req = urllib.request.Request(NEWS_API.format(category, page), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as r:
                out += json.loads(r.read()).get("result") or []
        return out

    def item(it, kind):
        dt = it.get("datetime", "")
        return {
            "kind": kind, "title": it.get("titleFull") or it.get("title"), "press": it.get("officeName"),
            "time": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}T{dt[8:10]}:{dt[10:12]}:{dt[12:14]}+09:00" if len(dt) >= 14 else None,
            "url": f"https://n.news.naver.com/mnews/article/{it.get('officeId')}/{it.get('articleId')}",
            "summary": (it.get("body") or "").strip(),
        }

    items = {}
    for it in get("flashnews", 5):
        head = (it.get("titleFull") or it.get("title") or "")[:12]
        if any(t in head for t in FLASH_TAGS):
            items[it["articleId"]] = item(it, "flash")
    for it in get("mainnews", 1):
        items.setdefault(it["articleId"], item(it, "main"))
    data = {"items": sorted(items.values(), key=lambda x: x["time"] or "", reverse=True),
            "updated": int(time.time())}
    _news_cache.update(at=time.time(), data=data)
    return data


NAVER = "https://stock.naver.com/api/securityService/marketindex"
# (그룹, 이름, 네이버 종류, 로이터 코드)
MACRO = [
    ("oil", "WTI", "energy", "CLcv1"),
    ("oil", "브렌트유", "energy", "LCOcv1"),
    ("oil", "두바이유", "energy", "DCBc1"),
    ("kr_bond", "국채 3년", "bond", "KR3YT=RR"),
    ("kr_bond", "국채 10년", "bond", "KR10YT=RR"),
    ("us_bond", "국채 5년", "bond", "US5YT=RR"),
    ("us_bond", "국채 10년", "bond", "US10YT=RR"),
    ("dxy", "달러인덱스", "exchange", ".DXY"),
    ("gold", "국제 금", "metals", "GCcv1"),
]
MACRO_PAGES = 5  # 한 번에 60일씩 → 약 1년치
_macro_cache = {"at": 0, "data": None}


def naver_json(path):
    req = urllib.request.Request(NAVER + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def macro_history(kind, code):
    rows = []
    for page in range(1, MACRO_PAGES + 1):
        part = naver_json(f"/{kind}/{code}/prices?page={page}&pageSize=60")
        rows += part
        if len(part) < 60:
            break
    pts = {r["localTradedAt"][:10]: num(r["closePrice"]) for r in rows}
    return sorted((d, v) for d, v in pts.items() if v is not None)


def macro():
    if _macro_cache["data"] and time.time() - _macro_cache["at"] < 600:
        return _macro_cache["data"]
    # 지금 값 (실시간)
    now = {}
    for path in ["/energy", "/bond/nation/KOR?sortType=", "/bond/nation/USA?sortType=",
                 "/metals", "/majors/exchange"]:
        for it in naver_json(path):
            now[it["reutersCode"]] = (it["localTradedAt"][:10], num(it["closePrice"]))
    with ThreadPoolExecutor(len(MACRO)) as ex:
        hists = list(ex.map(lambda m: macro_history(m[2], m[3]), MACRO))
    data = {"updated": int(time.time())}
    for (group, name, kind, code), hist in zip(MACRO, hists):
        if code in now and now[code][1] is not None:
            day, val = now[code]
            hist = [p for p in hist if p[0] != day] + [(day, val)]
        data.setdefault(group, []).append({"name": name, "code": code, "points": hist})
    _macro_cache.update(at=time.time(), data=data)
    return data


def estimates_data():
    path = BASE / "estimates.json"
    if not path.exists():
        return {"stocks": [], "updated": None, "collecting": True}
    return json.loads(path.read_text(encoding="utf-8"))


def estimates_refresher():
    """estimates.json이 12시간보다 오래됐으면 새로 모은다 (1시간마다 확인)."""
    import estimates
    while True:
        path = BASE / "estimates.json"
        if not path.exists() or time.time() - path.stat().st_mtime > 12 * 3600:
            try:
                estimates.collect()
            except Exception:
                pass
        time.sleep(3600)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(BASE), **kw)

    def do_GET(self):
        # 사이트 주소는 /diamond_rong/ 아래. 다른 주소는 그쪽으로 보낸다
        if self.path == PREFIX or not self.path.startswith(PREFIX + "/"):
            self.send_response(302)
            self.send_header("Location", PREFIX + "/")
            self.end_headers()
            return
        self.path = self.path[len(PREFIX):]
        # GitHub Pages와 같은 주소(data/*.json)로 내 PC에서도 바로 데이터를 준다
        api = {"/data/market.json": market, "/data/newhigh.json": new_highs, "/data/macro.json": macro,
               "/data/news.json": news,
               "/data/estimates.json": estimates_data}.get(self.path.split("?")[0])
        if api:
            try:
                body = json.dumps(api(), ensure_ascii=False).encode()
            except Exception as e:
                self.send_error(502, str(e))
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.endswith((".py", ".json", ".tmp")):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import threading
    threading.Thread(target=estimates_refresher, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
