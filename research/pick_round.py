"""Pick the listings for a blind rating round. usage: pick_round.py <round> [--dry]
The local head is trained on the likes against random listings and scores every unrated listing out of fold.
Groups, per bucket:
  local   the local head ranks it at the top, Luna scored it low (screened out or below 80)
  both    the local head ranks it at the top, Luna scored it 90 or more
  luna    Luna's highest unrated scores that the local head does not rank in its top 5%
  random  drawn uniformly from the unrated listings
Writes data/rounds/<round>.json in shuffled order."""
import json, sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from common import DATA, load_candidates

QUOTA = {  # bucket -> listings per group
    "obenrum_kalt": {"local": 16, "both": 8, "luna": 8, "random": 14},
    "hosen": {"local": 12, "both": 6, "luna": 6, "random": 13},
    "obenrum_mittel": {"local": 12, "both": 6, "luna": 6, "random": 13},
}
TOP = 0.02        # "the local head ranks it at the top": best 2% of the bucket
SAME_ITEM = 0.90  # cosine above which two covers count as the same item

rng = np.random.default_rng(1)
z = np.load(DATA / "emb" / "fashion-siglip_all.npz")
df = load_candidates().set_index("item_id").loc[z["ids"]].reset_index()
X = z["cover"].astype(np.float64)
X -= X.mean(0)
X /= np.linalg.norm(X, axis=1, keepdims=True)

picked, rows = [], []
for bucket, quota in QUOTA.items():
    m = np.where((df.bucket == bucket).values)[0]
    likes = m[(df.feedback.values[m] == "like")]
    unrated = rng.permutation(m[(df.feedback.values[m] == "unknown")])
    score = np.zeros(len(df))
    for fold in np.array_split(unrated, 4):  # each listing is scored by a head that never saw it as a negative
        rest = np.setdiff1d(unrated, fold)
        neg = rng.choice(rest, min(3000, len(rest)), replace=False)
        head = LogisticRegression(C=10.0, class_weight="balanced", max_iter=3000)
        head.fit(np.vstack([X[likes], X[neg]]), np.r_[np.ones(len(likes)), np.zeros(len(neg))])
        score[fold] = head.decision_function(X[fold])
    order = unrated[np.argsort(-score[unrated])]
    pct = {i: (k + 1) / len(order) for k, i in enumerate(order)}
    judged, luna = df.judged.values, df.score.values

    def take(group, candidates):
        n = 0
        for i in candidates:
            if n == quota[group]:
                break
            if picked and (X[picked] @ X[i]).max() > SAME_ITEM:
                continue
            picked.append(i); n += 1
            rows.append({"item_id": df.item_id[i], "bucket": bucket, "group": group, "local_pct": round(100 * pct[i], 3),
                         "luna_score": int(luna[i]) if judged[i] else None, "screen_p": df.screen_p[i],
                         "pool": len(order), "title": df.title[i], "brand": df.brand[i], "size": df["size"][i], "price": df.price[i]})
        if n < quota[group]:
            print(f"  {bucket} {group}: only {n} of {quota[group]}")

    top = order[: int(TOP * len(order))]
    take("local", [i for i in top if not judged[i] or luna[i] < 80])
    take("both", [i for i in top if judged[i] and luna[i] >= 90])
    by_luna = sorted((i for i in unrated if judged[i] and pct[i] > 0.05), key=lambda i: -luna[i])
    take("luna", by_luna)
    take("random", unrated)  # `unrated` is already a random permutation

    t = df.loc[top]
    print(f"{bucket}: {len(likes)} likes, {len(order)} unrated, top {len(top)}: Luna low {int((~t.judged | (t.score < 80)).sum())}, 80-89 {int((t.judged & t.score.between(80, 89)).sum())}, 90+ {int((t.judged & (t.score >= 90)).sum())}")

rows = [rows[k] for k in rng.permutation(len(rows))]
for r in rows:
    r.update({k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()})
if "--dry" not in sys.argv:
    (DATA / "rounds").mkdir(exist_ok=True)
    (DATA / "rounds" / f"{sys.argv[1]}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
import pandas as pd
pd.set_option("display.width", 220)
r = pd.DataFrame(rows)
print(r.groupby(["bucket", "group"]).agg(n=("item_id", "size"), local_pct=("local_pct", "median"), luna=("luna_score", "median")).to_string())
