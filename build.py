"""GitHub Pages용 정적 사이트 만들기.

public/ 폴더에 index.html과 data/*.json을 만든다.
GitHub Actions가 15분마다 이 파일을 실행해서 사이트를 갱신한다.
추정치(estimates.json)는 20시간보다 오래됐을 때만 새로 모은다.
"""
import json
import shutil
import time
from pathlib import Path

import estimates
import server

BASE = Path(__file__).resolve().parent
OUT = BASE / "public"


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def main():
    (OUT / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(BASE / "index.html", OUT / "index.html")

    for name, fn in [("market", server.market), ("newhigh", server.new_highs), ("macro", server.macro),
                     ("news", server.news)]:
        try:
            write(OUT / "data" / f"{name}.json", fn())
            print(f"{name}: OK")
        except Exception as e:
            print(f"{name}: 실패 {e}")

    est = BASE / "estimates.json"
    # 파일 수정 시각은 GitHub에서 받을 때마다 바뀌므로 안에 적힌 수집 시각으로 판단
    updated = json.loads(est.read_text(encoding="utf-8")).get("updated", 0) if est.exists() else 0
    if time.time() - updated > 20 * 3600:
        try:
            estimates.collect()
            print("estimates: 새로 수집")
        except Exception as e:
            print(f"estimates: 실패 {e}")
    if est.exists():
        shutil.copy(est, OUT / "data" / "estimates.json")


if __name__ == "__main__":
    main()
