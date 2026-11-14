#!/bin/sh
# Read-only snapshot of the production data on pop-os into research/data. Nothing is written on the host.
set -eu
cd "$(dirname "$0")"
mkdir -p data
HOST=janis@pop-os
run() { ssh -o BatchMode=yes "$HOST" "cd ~/vsniper-docker && docker compose exec -T vsniper $1"; }

run "python -" > data/candidates.jsonl <<'EOF'
import sqlite3, json, sys
c = sqlite3.connect("file:/data/sqlite/vsniper.db?mode=ro", uri=True)
cur = c.execute("select id, external_item_id, clothing_item, title, brand, size, price_eur, image_urls, normalized_listing, score_trace, decision, feedback, feedback_comment, created_at from candidates")
for r in cur:
    nl = json.loads(r[8]) if r[8] else {}
    raw = nl.get("raw_listing") if isinstance(nl.get("raw_listing"), dict) else {}
    photo = raw.get("photo") if isinstance(raw.get("photo"), dict) else {}
    st = json.loads(r[9]) if r[9] else {}
    sys.stdout.write(json.dumps({
        "id": r[0], "item_id": r[1], "bucket": r[2], "title": r[3], "brand": r[4], "size": r[5], "price": r[6],
        "image_urls": json.loads(r[7]) if r[7] else [], "description": nl.get("description") or "", "status": raw.get("status"),
        "thumbs": [(t.get("width"), t.get("height"), t.get("url")) for t in photo.get("thumbnails") or []],
        "score": st.get("score_10"), "explanation": st.get("explanation"), "prompt_version": st.get("prompt_version"),
        "judged_at": st.get("judged_at"), "raw_response": st.get("raw_response"),
        "decision": r[10], "feedback": r[11], "feedback_comment": r[12], "created_at": r[13],
    }, ensure_ascii=False) + "\n")
EOF
gzip -f data/candidates.jsonl

run "python -" > data/taste_samples.jsonl <<'EOF'
import sqlite3, json, sys
c = sqlite3.connect("file:/data/sqlite/vsniper.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
for r in c.execute("select id, user_id, kind, clothing_item, note, file_name, storage_path, external_item_id, title, brand, image_urls, cached_image_paths, stored_image_paths, candidate_id, created_at from taste_samples"):
    d = dict(r)
    for k in ("image_urls", "cached_image_paths", "stored_image_paths"):
        d[k] = json.loads(d[k]) if d[k] else []
    sys.stdout.write(json.dumps(d, ensure_ascii=False) + "\n")
EOF

run "tar -cf - -C /data uploads feedback-assets" | tar -xf - -C data
wc -l data/taste_samples.jsonl
gzip -dc data/candidates.jsonl.gz | wc -l
du -sh data/uploads data/feedback-assets
