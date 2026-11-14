"""Replay the fixed head recipe day by day, as if it had run live. usage: replay.py [round]
For each bucket and day, the head is trained only on listings, Luna scores and verdicts from earlier days and then scores
that day's listings. Each day it alerts on its best listings, with a budget that adds up to Luna's alert count for the
bucket; a second tier ("next") takes the same number of listings after those. Writes data/replay.csv. With a round name it also writes data/rounds/<round>.json:
the local alerts and next tier without a verdict, plus Luna's alerts without a verdict."""
import json, sys
import numpy as np, pandas as pd
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold
from sklearn.preprocessing import StandardScaler
from common import DATA
from head_recipe import BLOCKS, bucket, df, fit_head, source, y, z

MIN_LIKES = 3  # a bucket starts once this many likes exist from earlier days
raw, image = z["cover"].astype(np.float64), BLOCKS["image"]
day, luna_alert = df.day.values, (df.decision == "alert").values


def luna_direction(prior, today):
    """Luna's score predicted from the cover: out of fold for the earlier listings, from all of them for today's."""
    sc = StandardScaler().fit(raw[prior])
    P, target = sc.transform(raw[prior]), df.target.values[prior]
    oof = np.zeros(len(prior))
    for tr, te in KFold(5, shuffle=True, random_state=0).split(prior):
        oof[te] = Ridge(alpha=1000).fit(P[tr], target[tr]).predict(P[te])
    new = Ridge(alpha=1000).fit(P, target).predict(sc.transform(raw[today]))
    return (oof - oof.mean()) / oof.std(), (new - oof.mean()) / oof.std()


rows = []
for b in np.unique(bucket):
    in_b = bucket == b
    days = [d for d in sorted(set(day[in_b])) if (y[in_b & (day < d)] == 1).sum() >= MIN_LIKES]
    rate = luna_alert[in_b & np.isin(day, days)].mean()
    seen = sent = 0
    for d in days:
        prior, today = np.where(in_b & (day < d))[0], np.where(in_b & (day == d))[0]
        lp, lt = luna_direction(prior, today)
        X = np.zeros((len(df), image.shape[1] + 1))
        X[prior], X[today] = np.c_[image[prior], lp], np.c_[image[today], lt]
        unl = prior[y[prior] < 0]
        neg = np.random.default_rng(0).choice(unl, min(2000, len(unl) // 2), replace=False)
        head = fit_head(X, prior[y[prior] >= 0], neg)
        ranked = np.sort(head.decision_function(X[np.setdiff1d(unl, neg)]))
        s = head.decision_function(X[today])
        pct = (len(ranked) - np.searchsorted(ranked, s, side="left") + 1) / (len(ranked) + 1)
        seen += len(today)
        k = round(rate * seen) - sent  # today's budget keeps the running total at Luna's rate
        sent += k
        rank = np.argsort(np.argsort(-s))
        for i, p, q in zip(today, pct, rank):
            rows.append({"item_id": df.item_id[i], "bucket": b, "day": d, "local_pct": 100 * p,
                         "local": "alert" if q < k else "next" if q < 2 * k else "", "luna_alert": bool(luna_alert[i]),
                         "luna_score": df.score[i] if df.judged[i] else np.nan, "verdict": {1: "like", 0: "dislike", -1: ""}[y[i]], "source": source[i]})

r = pd.DataFrame(rows)
r.to_csv(DATA / "replay.csv", index=False)
pd.set_option("display.width", 220)
count = lambda m: int(m.sum())
print(r.groupby(["bucket", "day"]).apply(lambda g: pd.Series({"listings": len(g), "Luna alerts": count(g.luna_alert), "local alerts": count(g.local == "alert"),
      "both": count(g.luna_alert & (g.local == "alert")), "local next": count(g.local == "next")}), include_groups=False).to_string())

def verdicts(name, m):
    g = r[m]
    like, dis = count(g.verdict == "like"), count(g.verdict == "dislike")
    print(f"{name:42s} {len(g):4d} | liked {like}, disliked {dis}, no verdict {len(g) - like - dis}" + (f" | liked share of rated {like / (like + dis):.0%}" if like + dis else ""))

print()
verdicts("Luna alerts on replay days", r.luna_alert)
verdicts("local alerts", r.local == "alert")
verdicts("local next tier", r.local == "next")
verdicts("local alerts that Luna did not alert", (r.local == "alert") & ~r.luna_alert)
print()
for name, m in [("Luna alerts", r.luna_alert), ("Luna alerts you liked", r.luna_alert & (r.verdict == "like")), ("Luna alerts you disliked", r.luna_alert & (r.verdict == "dislike")),
                ("all likes", r.verdict == "like"), ("all dislikes", r.verdict == "dislike")]:
    g = r[m]
    print(f"{name:26s} {len(g):3d} | local would alert {count(g.local == 'alert')}, alert or next {count(g.local != '')}, in its top 1% {count(g.local_pct <= 1)}, top 5% {count(g.local_pct <= 5)}, median percentile {g.local_pct.median():.2f}%")
g = r[(r.local == "alert") & ~r.luna_alert]
print("\nLuna's view of the local alerts it did not send: screened out", count(g.luna_score.isna()), "| scored <80", count(g.luna_score < 80), "| 80-89", count(g.luna_score.between(80, 89)), "| 90-94", count(g.luna_score >= 90))

if len(sys.argv) > 1:
    pick = r[(r.verdict == "") & ((r.local != "") | r.luna_alert)].copy()
    pick["group"] = np.where(pick.local != "", "replay_" + pick.local, "luna_alert")
    meta = df.set_index("item_id").loc[pick.item_id]
    items = [{"item_id": i, "bucket": b, "group": g, "luna_alert": bool(la), "local_pct": round(p, 3),
              "title": meta.title[i], "brand": meta.brand[i], "size": meta["size"][i], "price": meta.price[i]}
             for i, b, g, la, p in zip(pick.item_id, pick.bucket, pick.group, pick.luna_alert, pick.local_pct)]
    items = [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in it.items()} for it in items]
    items = [items[k] for k in np.random.default_rng(2).permutation(len(items))]
    (DATA / "rounds" / f"{sys.argv[1]}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1))
    print(f"\nround {sys.argv[1]}: {len(items)} listings |", pick.group.value_counts().to_dict())
