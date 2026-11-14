"""Download the 310 px cover of every candidate, and the second listing photo (downscaled) where there is one."""
import gzip, io, json, time
from concurrent.futures import ThreadPoolExecutor
import httpx
from PIL import Image
from common import DATA

rows = [json.loads(line) for line in gzip.open(DATA / "candidates.jsonl.gz", "rt")]
(DATA / "img310").mkdir(exist_ok=True); (DATA / "img2").mkdir(exist_ok=True)
jobs = []
for r in rows:
    cover = next((u for w, h, u in r["thumbs"] if w == 310), None)
    if cover and not (DATA / "img310" / f"{r['item_id']}.webp").exists():
        jobs.append((DATA / "img310" / f"{r['item_id']}.webp", cover, False))
    if len(r["image_urls"]) > 1 and not (DATA / "img2" / f"{r['item_id']}.webp").exists():
        jobs.append((DATA / "img2" / f"{r['item_id']}.webp", r["image_urls"][1], True))
gone = set(open(DATA / "gone.txt").read().split()) if (DATA / "gone.txt").exists() else set()
jobs = [j for j in jobs if j[1] not in gone]
client = httpx.Client(timeout=20, headers={"User-Agent": "Mozilla/5.0"}, limits=httpx.Limits(max_connections=12))

def get(job):
    path, url, shrink = job
    for attempt in range(3):
        try:
            r = client.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                if shrink:  # second photos only come at 800 px; store them at cover size
                    im = Image.open(io.BytesIO(r.content)).convert("RGB"); im.thumbnail((310, 430))
                    im.save(path, "WEBP", quality=85)
                else:
                    path.write_bytes(r.content)
                return "ok", url
            if r.status_code in (403, 404, 410): return "gone", url
            if r.status_code == 429: time.sleep(5 * (attempt + 1))
        except (httpx.HTTPError, OSError):
            time.sleep(1)
    return "fail", url

stats, t = {}, time.time()
with ThreadPoolExecutor(12) as pool, open(DATA / "gone.txt", "a") as gone_file:
    for n, (s, url) in enumerate(pool.map(get, jobs), 1):
        stats[s] = stats.get(s, 0) + 1
        if s == "gone": gone_file.write(url + "\n")
        if n % 2000 == 0: print(n, len(jobs), stats, f"{time.time() - t:.0f}s", flush=True)
print("done", len(jobs), stats, f"{time.time() - t:.0f}s")
