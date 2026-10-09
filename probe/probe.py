import urllib.request, re
URLS = [
 "https://finance.naver.com/news/mainnews.naver",
 "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258",
 "https://m.stock.naver.com/front-api/news/category?category=mainnews&page=1&pageSize=5",
 "https://m.stock.naver.com/api/news/list?category=mainnews&page=1&pageSize=5",
 "https://m.stock.naver.com/front-api/news/category?category=flashnews&page=1&pageSize=5",
 "https://www.yna.co.kr/rss/news.xml",
 "https://news.google.com/rss/search?q=%5B%EC%86%8D%EB%B3%B4%5D&hl=ko&gl=KR&ceid=KR:ko",
]
for u in URLS:
    print("=" * 30, u)
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            print("status", r.status, r.headers.get("Content-Type"))
        for enc in ("utf-8", "euc-kr"):
            try:
                t = raw.decode(enc); print("enc", enc); break
            except Exception:
                pass
        i = max(t.find("articleSubject"), t.find("<item"), 0)
        print(t[i:i+2500] if i else t[:2500])
    except Exception as e:
        print("ERR", e)
