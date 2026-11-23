"""나만의 대시보드 사이트 서버.

http://localhost:8080 (같은 와이파이의 폰: http://PC주소:8080)
  /             화면 (index.html)
  /data/*.json  시세·신고가·매크로·뉴스·추정치·국장(차트·공시)·종목 JSON (GitHub Pages와 같은 주소)
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
    "us_index": [("S&P 500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI"),
                 ("필라델피아 반도체", "^SOX"), ("나스닥 선물", "NQ=F"), ("VIX 공포지수", "^VIX")],
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


def get(url, data=None, timeout=20):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


_stocks_cache = {"at": 0, "data": None}


def stocks():
    """국장 전 종목 (종목검색용). 한 종목 = [코드, 이름, 시장(0 코스피·1 코스닥), 현재가, 등락률,
    시총(억), 거래대금(억), PER, PBR, 52주 최고, 52주 최저, 외국인 비율]."""
    if _stocks_cache["data"] and time.time() - _stocks_cache["at"] < CACHE_SEC:
        return _stocks_cache["data"]
    items = json.loads(get("https://stock.naver.com/api/domestic/market/stock/default?tradeType=KRX"
                           "&marketType=ALL&orderType=marketSum&startIdx=0&pageSize=5000"))

    def f(v, nd=2):
        try:
            return round(float(v), nd)
        except (TypeError, ValueError):
            return None

    rows = []
    for it in items:
        if it.get("sosok") not in ("0", "1"):
            continue  # 코넥스 제외
        pct = f(it.get("prevChangeRate"))
        if it.get("upDownGb") in ("4", "5") and pct and pct > 0:
            pct = -pct
        cap, amt = f(it.get("marketSum"), 0), f(it.get("tradeAmount"), 0)
        rows.append([it["itemcode"], it["itemname"], int(it["sosok"]), f(it.get("nowPrice"), 0), pct,
                     round(cap / 1e8) if cap else None, round(amt / 1e8) if amt else None,
                     f(it.get("per")), f(it.get("pbr")), f(it.get("week52HighPrice"), 0),
                     f(it.get("week52LowPrice"), 0), f(it.get("frgnHoldRate"))])
    data = {"rows": rows, "updated": int(time.time())}
    _stocks_cache.update(at=time.time(), data=data)
    return data


def yahoo_points(symbol, rng, interval):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           f"?range={rng}&interval={interval}")
    res = json.loads(get(url))["chart"]["result"][0]
    closes = res["indicators"]["quote"][0]["close"]
    pts = [(t, round(c, 2)) for t, c in zip(res.get("timestamp") or [], closes) if c is not None]
    return pts, res["meta"].get("chartPreviousClose")


def index_chart(symbol):
    """지수 차트: 당일 5분봉(한국시간 HH:MM)과 1년 일봉(YYYY-MM-DD)."""
    kst = lambda t, fmt: time.strftime(fmt, time.gmtime(t + 9 * 3600))
    day, prev = yahoo_points(symbol, "1d", "5m")
    year, _ = yahoo_points(symbol, "1y", "1d")
    return {"prev": prev, "day": [(kst(t, "%H:%M"), v) for t, v in day],
            "year": [(kst(t, "%Y-%m-%d"), v) for t, v in year]}


# 공시 분류 (제목에 이 말이 들어가면)
DISC_CATS = [
    ("실적", ("잠정)실적", "잠정실적", "결산실적", "매출액또는손익구조")),
    ("계약", ("공급계약", "단일판매", "수주")),
    ("주주환원", ("자기주식", "배당", "소각", "무상증자")),
    ("자금조달", ("유상증자", "전환사채", "신주인수권", "교환사채", "사채권")),
    ("지분", ("대량보유", "임원ㆍ주요주주", "최대주주")),
]


def dart_day(day):
    """DART '오늘의 공시'에서 그날 유가·코스닥 공시를 모두 가져온다 (최신순)."""
    import html as htmllib
    import re
    out = []
    for group, mkt in (("Y", "코스피"), ("K", "코스닥")):
        for page in range(1, 20):
            h = get("https://dart.fss.or.kr/dsac001/search.ax", {
                "currentPage": page, "maxResults": 100, "maxLinks": 10, "pageGrouping": group,
                "mdayCnt": 0, "selectDate": day.replace("-", ".")}).decode("utf-8")
            rows = re.findall(r"<tr>(.*?)</tr>", h, re.S)
            got = 0
            for r in rows:
                m = re.search(r"openReportViewer\('(\d+)'\)[^>]*>(.*?)</a>", r, re.S)
                if not m:
                    continue
                cells = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
                text = lambda s: re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
                corp = re.search(r"openCorpInfoNew[^>]*>(.*?)</a>", r, re.S)
                title = text(m.group(2))
                out.append({"t": text(cells[0]), "m": mkt, "nm": text(corp.group(1)) if corp else "",
                            "title": title, "id": m.group(1),
                            "cat": next((c for c, kws in DISC_CATS if any(k in title for k in kws)), "기타")})
                got += 1
            total = re.search(r'id="totalCnt" value="(\d+)"', h)
            if got < 100 or (total and page * 100 >= int(total.group(1))):
                break
    out.sort(key=lambda d: (d["t"], d["id"]), reverse=True)
    return out


def kr_board():
    """국장 탭: 코스피·코스닥 차트와 오늘의 공시."""
    with ThreadPoolExecutor(2) as ex:
        charts = dict(zip(["kospi", "kosdaq"], ex.map(index_chart, ["^KS11", "^KQ11"])))
    # 주말·휴일이면 공시가 있는 가장 최근 날로
    disc, day = [], None
    for back in range(0, 5):
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 9 * 3600 - back * 86400))
        disc = dart_day(day)
        if disc:
            break
    return {"charts": charts, "disc": {"day": day, "items": disc}, "updated": int(time.time())}



# ---------------- 미장 탭 ----------------
# SEC는 자동 접속에 도구 이름을 밝힌 User-Agent를 요구한다
SEC_UA = "personal-market-dashboard/1.0"
NASDAQ_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36", "Accept": "application/json"}


def get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def us_time(t, fmt):
    """유닉스 시각 → 한국시간 문자열."""
    return time.strftime(fmt, time.gmtime(t + 9 * 3600))


def us_index_chart(symbol):
    """미국 지수 차트: 당일 5분(한국시간, 정규장 전체 칸 포함)과 1년 일봉."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
           "?range=1d&interval=5m")
    res = json.loads(get(url))["chart"]["result"][0]
    meta = res["meta"]
    closes = res["indicators"]["quote"][0]["close"]
    day = [(us_time(t, "%H:%M"), round(c, 2)) for t, c in zip(res.get("timestamp") or [], closes) if c is not None]
    reg = meta.get("currentTradingPeriod", {}).get("regular", {})
    # 장이 쉬는 날이면 currentTradingPeriod가 다음 장을 가리키므로 실제 데이터 시각으로 맞춘다
    ts = res.get("timestamp") or []
    start = ts[0] - ts[0] % 300 if ts else reg.get("start")
    end = start + (reg["end"] - reg["start"]) if start and reg.get("end") else None
    session = [us_time(t, "%H:%M") for t in range(start, end + 1, 300)] if start and end else None
    year, _ = yahoo_points(symbol, "1y", "1d")
    return {"prev": meta.get("chartPreviousClose"), "day": day, "session": session,
            "year": [(us_time(t - 9 * 3600, "%Y-%m-%d"), v) for t, v in year]}


US_INDEX = [("spx", "^GSPC"), ("ndx", "^IXIC"), ("dji", "^DJI"), ("sox", "^SOX")]

# 8-K 항목 번호 → 한글 이름, 분류
ITEM_8K = {
    "1.01": ("주요 계약 체결", "계약·M&A"), "1.02": ("주요 계약 해지", "계약·M&A"), "1.03": ("파산·회생", "리스크"),
    "1.05": ("사이버 보안 사고", "리스크"),
    "2.01": ("자산 인수·처분", "계약·M&A"), "2.02": ("실적 발표", "실적"), "2.03": ("채무 발생", "자금조달"),
    "2.04": ("채무 조기상환 사유", "리스크"), "2.05": ("구조조정 비용", "리스크"), "2.06": ("자산 손상", "리스크"),
    "3.01": ("상장 유지 요건 미달", "리스크"), "3.02": ("비등록 증권 발행", "자금조달"), "3.03": ("주주 권리 변경", "지배구조"),
    "4.01": ("감사인 변경", "리스크"), "4.02": ("과거 재무제표 신뢰 불가", "리스크"),
    "5.01": ("지배권 변경", "계약·M&A"), "5.02": ("임원·이사 변동", "지배구조"), "5.03": ("정관 변경", "지배구조"),
    "5.07": ("주총 결과", "지배구조"), "5.08": ("주총 일정", "지배구조"),
    "7.01": ("공정공시(Reg FD)", "기타"), "8.01": ("기타 주요사항", "기타"), "9.01": ("재무제표·첨부", "기타"),
}
CAT_ORDER = ["실적", "계약·M&A", "자금조달", "리스크", "지배구조", "기타"]


def sec_8k(days=4):
    """최근 며칠 SEC 8-K 중 상장 종목(티커가 있는 회사) 것만."""
    import re
    end = time.strftime("%Y-%m-%d", time.gmtime())
    start = time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
    hits = []
    for page in range(10):
        d = get_json("https://efts.sec.gov/LATEST/search-index?forms=8-K&dateRange=custom"
                     f"&startdt={start}&enddt={end}&from={page * 100}", {"User-Agent": SEC_UA})
        part = d["hits"]["hits"]
        hits += part
        if len(part) < 100:
            break
    seen, out = set(), []
    for h in hits:
        src = h["_source"]
        adsh = src.get("adsh")
        if not adsh or adsh in seen:
            continue
        for name in src.get("display_names", []):
            m = re.match(r"(.*?)\s+\(([A-Z0-9.\-, ]+)\)\s+\(CIK (\d+)\)", name)
            if m:
                break
        else:
            continue  # 티커 없는 회사(펀드·비상장)는 뺀다
        seen.add(adsh)
        items = [i for i in src.get("items", []) if i in ITEM_8K]
        main = [i for i in items if i != "9.01"] or items
        cats = [ITEM_8K[i][1] for i in main]
        cat = min(cats, key=CAT_ORDER.index) if cats else "기타"
        cik = int(m.group(3))
        out.append({"t": src["file_date"][5:].replace("-", "/"), "d": src["file_date"], "code": m.group(2).split(",")[0].strip(),
                    "nm": m.group(1).strip(), "title": " · ".join(ITEM_8K[i][0] for i in main) or "수시공시",
                    "cat": cat, "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{adsh}-index.htm"})
    out.sort(key=lambda x: x["d"], reverse=True)
    return out


def earnings_week():
    """나스닥 실적 발표 일정: 미국 기준 지난 3거래일 ~ 다음 7거래일 (시총 3억 달러 이상)."""
    ny = time.time() - 5 * 3600  # 미국 동부 (대략)
    days = []
    for k in range(-5, 12):
        t = ny + k * 86400
        if time.gmtime(t).tm_wday < 5:
            days.append(time.strftime("%Y-%m-%d", time.gmtime(t)))
    days = [d for d in days if d < time.strftime("%Y-%m-%d", time.gmtime(ny))][-3:] + \
           [d for d in days if d >= time.strftime("%Y-%m-%d", time.gmtime(ny))][:7]

    def money(v):
        try:
            return float(str(v).replace("$", "").replace(",", "").replace("(", "-").replace(")", ""))
        except (TypeError, ValueError):
            return None

    def one(day):
        try:
            rows = get_json(f"https://api.nasdaq.com/api/calendar/earnings?date={day}", NASDAQ_HEADERS)["data"]["rows"] or []
        except Exception:
            return []
        out = []
        for r in rows:
            cap = money(r.get("marketCap"))
            if not cap or cap < 3e8:
                continue
            out.append({"d": day, "sym": r["symbol"], "nm": r["name"], "cap": round(cap / 1e6),
                        "when": {"time-pre-market": "pre", "time-after-hours": "post"}.get(r.get("time"), ""),
                        "q": r.get("fiscalQuarterEnding"), "est": money(r.get("epsForecast")), "n": r.get("noOfEsts"),
                        "eps": money(r.get("eps")), "sur": money(r.get("surprise")), "ly": money(r.get("lastYearEPS"))})
        return out

    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(one, days))
    items = [x for part in res for x in part]
    items.sort(key=lambda x: (x["d"], -x["cap"]))
    return {"days": days, "today": time.strftime("%Y-%m-%d", time.gmtime(ny)), "items": items}


def us_board():
    """미장 탭: 지수 차트 · SEC 8-K · 실적 발표 일정."""
    with ThreadPoolExecutor(6) as ex:
        charts = ex.map(lambda x: us_index_chart(x[1]), US_INDEX)
        disc = ex.submit(sec_8k)
        earn = ex.submit(earnings_week)
        charts = dict(zip([k for k, _ in US_INDEX], charts))
        data = {"charts": charts, "updated": int(time.time())}
        try:
            data["disc"] = {"items": disc.result()}
        except Exception as e:
            data["disc"] = {"items": [], "error": str(e)}
        try:
            data["earn"] = earn.result()
        except Exception as e:
            data["earn"] = {"days": [], "items": [], "error": str(e)}
    return data


_us_stocks_cache = {"at": 0, "data": None}
NAVER_US = [("NASDAQ", 5), ("NYSE", 5), ("AMEX", 1)]  # (거래소, 100종목씩 몇 페이지)


def us_stocks():
    """미국 종목 (종목검색용). 한 종목 = [티커, 한글 이름, 영문 이름, 현재가, 등락률, 시총(백만 달러),
    업종, 네이버 코드(차트 이미지용, 없으면 null)]."""
    if _us_stocks_cache["data"] and time.time() - _us_stocks_cache["at"] < CACHE_SEC:
        return _us_stocks_cache["data"]

    def num(v):
        try:
            return float(str(v).replace("$", "").replace(",", "").replace("%", ""))
        except (TypeError, ValueError):
            return None

    jobs = [("naver", ex_, p) for ex_, n in NAVER_US for p in range(1, n + 1)] + [("nasdaq", None, None)]

    def fetch_job(j):
        try:
            if j[0] == "naver":
                return j, get_json(f"https://api.stock.naver.com/stock/exchange/{j[1]}/marketValue?page={j[2]}&pageSize=100")["stocks"]
            return j, get_json("https://api.nasdaq.com/api/screener/stocks?tableonly=true&download=true", NASDAQ_HEADERS)["data"]["rows"]
        except Exception:
            return j, []

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(fetch_job, jobs))
    key = lambda sym: sym.replace("/", ".").replace(" ", ".").upper()
    rows = {}
    for j, items in results:
        if j[0] != "naver":
            continue
        for it in items:
            if it.get("stockEndType") != "stock":
                continue
            sym = key(it["symbolCode"])
            cap = num(it.get("marketValue"))  # 천 달러 단위
            ind = (it.get("industryCodeType") or {}).get("industryGroupKor")
            rows[sym] = [sym, it.get("stockName"), it.get("stockNameEng"), num(it.get("closePrice")),
                         num(it.get("fluctuationsRatio")), round(cap / 1000) if cap else None, ind, it["reutersCode"]]
    for j, items in results:
        if j[0] != "nasdaq":
            continue
        for it in items:
            sym = key(it["symbol"])
            if "^" in sym:
                continue  # 우선주 제외
            cap = num(it.get("marketCap"))
            price, pct = num(it.get("lastsale")), num(it.get("pctchange"))
            if sym in rows:
                continue  # 네이버에 있는 종목은 네이버 시세를 쓴다 (차트 이미지와 맞추려고)
            if not cap or cap < 1e8:
                continue
            nm = it.get("name", "")
            for cut in (" Common Stock", " Class A", " Ordinary Shares", " American Depositary"):
                nm = nm.split(cut)[0]
            rows[sym] = [sym, None, nm.strip(), price, pct, round(cap / 1e6), it.get("sector") or None, None]
    out = sorted(rows.values(), key=lambda r: -(r[5] or 0))
    data = {"rows": out, "updated": int(time.time())}
    _us_stocks_cache.update(at=time.time(), data=data)
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
               "/data/news.json": news, "/data/kr.json": kr_board, "/data/stocks.json": stocks,
               "/data/us.json": us_board, "/data/us_stocks.json": us_stocks,
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
