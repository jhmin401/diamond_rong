"""GitHub Pages용 정적 사이트 만들기.

public/ 폴더에 index.html과 data/*.json을 만든다.
GitHub Actions가 15분마다 이 파일을 실행해서 사이트를 갱신한다.
추정치(estimates.json)는 20시간보다 오래됐거나 종목이 너무 적을 때만 새로 모은다.
"""
import json
import os
import shutil
import time
from pathlib import Path

import estimates
import server

BASE = Path(__file__).resolve().parent
OUT = BASE / "public"


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def keep_live(name):
    """GitHub Pages에 올라가 있는 data/<name>.json을 받아 public에 그대로 둔다."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return False
    owner, proj = repo.split("/", 1)
    url = f"https://{owner.lower()}.github.io/{proj}/data/{name}.json?t={int(time.time())}"
    try:
        body = server.get(url, timeout=20)
        json.loads(body)  # 깨진 파일은 올리지 않는다
        (OUT / "data" / f"{name}.json").write_bytes(body)
        return True
    except Exception:
        return False


def main():
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE / "index.html", OUT / "index.html")

    # 추정치는 오래 걸릴 수 있어서 먼저 모은다 (시세가 늦게 올라가지 않도록)
    est = BASE / "estimates.json"
    # 파일 수정 시각은 GitHub에서 받을 때마다 바뀌므로 안에 적힌 수집 시각으로 판단
    old = json.loads(est.read_text(encoding="utf-8")) if est.exists() else {}
    stale = time.time() - old.get("updated", 0) > 20 * 3600
    # 지난번 수집이 반쯤 실패해서 종목이 빠졌으면 기다리지 않고 다시 모은다
    thin = len(old.get("stocks", [])) < estimates.UNIVERSE * 0.8
    if stale or thin:
        t = time.time()
        try:
            d = estimates.collect()
            print(f"estimates: 새로 수집 {len(d['stocks'])}종목, {time.time() - t:.0f}초")
        except Exception as e:
            print(f"estimates: 실패, 기존 파일 유지 ({e}), {time.time() - t:.0f}초")
    if est.exists():
        shutil.copy(est, OUT / "data" / "estimates.json")

    for name, fn in [("market", server.market), ("newhigh", server.new_highs), ("macro", server.macro),
                     ("news", server.news), ("kr", server.kr_board), ("stocks", server.stocks),
                     ("us", server.us_board), ("us_stocks", server.us_stocks)]:
        try:
            write(OUT / "data" / f"{name}.json", fn())
            print(f"{name}: OK")
        except Exception as e:
            # 실패하면 지금 사이트에 올라가 있는 파일을 그대로 다시 올린다 (파일이 사라져 화면이 비지 않게)
            print(f"{name}: 실패 {e} → 기존 파일 유지 {'성공' if keep_live(name) else '실패'}")


if __name__ == "__main__":
    main()
