"""Compare recipes for the taste head on every verdict (Telegram and finished rating rounds). usage: head_recipe.py
Each verdict is scored by a head that never saw it (5-fold, 3 repeats) and placed as a percentile among the unlabelled
listings of its bucket. Reported per recipe:
  like rank   typical percentile of a held-out like (lower is better); "fair" leaves out likes the local head itself picked
  AUC         how often a held-out like outranks a held-out dislike of the same bucket; "hard" is within the local head's own picks"""
import glob, json, warnings
import numpy as np, pandas as pd
from joblib import Parallel, delayed
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.model_selection import KFold, StratifiedKFold
from sklearn.preprocessing import StandardScaler
from common import DATA, load_candidates

warnings.filterwarnings("ignore")
z = np.load(DATA / "emb" / "fashion-siglip_all.npz")
t = np.load(DATA / "emb" / "fashion-siglip_all_text.npz")
assert (z["ids"] == t["ids"]).all()
df = load_candidates().set_index("item_id").loc[z["ids"]].reset_index()
bucket = df.bucket.values

# labels: 1 like, 0 dislike, -1 none; source: telegram or the group of the rating round
y = np.where(df.feedback == "like", 1, np.where(df.feedback == "dislike", 0, -1))
source = np.where(y >= 0, "telegram", "")
row = {i: k for k, i in enumerate(df.item_id)}
for path in sorted(glob.glob(str(DATA / "rounds" / "*_labels.json"))):
    labels = json.loads(open(path).read())
    if labels["finished"]:
        for it in json.loads(open(path.replace("_labels", "")).read()):
            y[row[it["item_id"]]] = it["item_id"] in labels["likes"]
            source[row[it["item_id"]]] = it["group"]
labelled = np.where(y >= 0)[0]
fair = np.isin(source[labelled], ["telegram", "luna", "random"])
hard = np.isin(source[labelled], ["local", "both"])


def unit(X):
    X = X.astype(np.float64) - X.mean(0)
    return X / np.linalg.norm(X, axis=1, keepdims=True)


def luna_direction():
    """Luna's score predicted from the cover alone, out of fold, as a z-score per bucket."""
    raw, out = z["cover"].astype(np.float64), np.zeros(len(df))
    for b in np.unique(bucket):
        m = np.where(bucket == b)[0]
        for tr, te in KFold(5, shuffle=True, random_state=0).split(m):
            sc = StandardScaler().fit(raw[m[tr]])
            out[m[te]] = Ridge(alpha=1000).fit(sc.transform(raw[m[tr]]), df.target.values[m[tr]]).predict(sc.transform(raw[m[te]]))
        out[m] = (out[m] - out[m].mean()) / out[m].std()
    return out[:, None]


brand = df.brand.fillna("").str.strip().str.lower()
common_brands = brand.value_counts()[lambda c: c >= 20].index
BLOCKS = {
    "image": unit(z["cover"]),
    "brand one-hot": (brand.to_numpy(dtype=object)[:, None] == common_brands.to_numpy(dtype=object)[None, :]).astype(np.float64),
    "brand text": unit(t["brand"]),
    "title text": unit(t["text"]),
    "luna": luna_direction(),
}


def fit_fold(X, train, held, neg, pool, w_dis, C):
    pos, dis = train[y[train] == 1], train[y[train] == 0] if w_dis else train[:0]
    Xt = np.vstack([X[pos], X[dis], X[neg]])
    yt = np.r_[np.ones(len(pos)), np.zeros(len(dis) + len(neg))]
    w = np.r_[np.ones(len(pos)), np.full(len(dis), w_dis), np.ones(len(neg))]
    head = LogisticRegression(C=C, class_weight="balanced", max_iter=2000).fit(Xt, yt, sample_weight=w)
    ranked = np.sort(head.decision_function(X[pool]))
    above = len(ranked) - np.searchsorted(ranked, head.decision_function(X[held]), side="left")
    return held, (above + 1) / (len(ranked) + 1)


def percentiles(blocks, w_dis=0.0, C=10.0, n_neg=2000, repeats=3):
    """Held-out percentile of every labelled listing, as the geometric mean over repeats."""
    X = np.hstack([BLOCKS[name] * scale for name, scale in blocks.items()])
    jobs = []
    for b in np.unique(bucket):
        lab = labelled[bucket[labelled] == b]
        unl = np.where((bucket == b) & (y < 0))[0]
        for r in range(repeats):
            rng = np.random.default_rng(r)
            neg = rng.choice(unl, min(n_neg, len(unl) // 2), replace=False)
            pool = np.setdiff1d(unl, neg)
            for tr, te in StratifiedKFold(5, shuffle=True, random_state=r).split(lab, y[lab]):
                jobs.append(delayed(fit_fold)(X, lab[tr], lab[te], neg, pool, w_dis, C))
    logp = pd.Series(0.0, index=labelled)
    for held, p in Parallel(n_jobs=6)(jobs):
        logp[held] += np.log(p) / repeats
    return np.exp(logp.values)


def auc(p, mask):
    """Share of like/dislike pairs within a bucket where the like has the better percentile."""
    won = pairs = 0.0
    for b in np.unique(bucket):
        m = mask & (bucket[labelled] == b)
        lp, dp = p[m & (y[labelled] == 1)], p[m & (y[labelled] == 0)]
        won += (lp[:, None] < dp[None, :]).sum() + 0.5 * (lp[:, None] == dp[None, :]).sum()
        pairs += len(lp) * len(dp)
    return won / pairs


def summary(name, p):
    like = y[labelled] == 1
    gm = lambda v: 100 * float(np.exp(np.log(v).mean()))
    return {"recipe": name, "like rank %": round(gm(p[like]), 2), "like rank fair %": round(gm(p[like & fair]), 2),
            "likes top 1%": f"{int((p[like] <= .01).sum())}/{int(like.sum())}", "dislikes top 1%": f"{int((p[~like] <= .01).sum())}/{int((~like).sum())}",
            "AUC all": round(auc(p, np.ones(len(p), bool)), 3), "AUC fair": round(auc(p, fair), 3), "AUC hard": round(auc(p, hard), 3)}


def difference(p, base, mask, n=2000):
    """90% bootstrap interval of the AUC gain over the baseline recipe, resampling listings."""
    rng, idx, out = np.random.default_rng(0), np.where(mask)[0], []
    for _ in range(n):
        s = rng.choice(idx, len(idx))
        won = pairs = wb = 0.0
        for b in np.unique(bucket):
            sb = s[bucket[labelled][s] == b]
            l, d = sb[y[labelled][sb] == 1], sb[y[labelled][sb] == 0]
            won += (p[l][:, None] < p[d][None, :]).sum(); wb += (base[l][:, None] < base[d][None, :]).sum(); pairs += len(l) * len(d)
        out.append((won - wb) / pairs)
    lo, hi = np.quantile(out, [0.05, 0.95])
    return f"{lo:+.3f} to {hi:+.3f}"


if __name__ == "__main__":
    print(pd.DataFrame({"source": source[labelled], "bucket": bucket[labelled], "like": y[labelled]}).pivot_table(index="source", columns="bucket", values="like", aggfunc=lambda s: f"{int(s.sum())}/{len(s)}", margins=False).to_string(), "\n")
    img = {"image": 1.0}
    recipes = {
        "image, likes vs random": (img, {}),
        "+ dislikes weight 1": (img, {"w_dis": 1}),
        "+ dislikes weight 5": (img, {"w_dis": 5}),
        "+ dislikes weight 20": (img, {"w_dis": 20}),
        "dislikes 5, C=1": (img, {"w_dis": 5, "C": 1}),
        "dislikes 5, C=100": (img, {"w_dis": 5, "C": 100}),
        "dislikes 5, 500 negatives": (img, {"w_dis": 5, "n_neg": 500}),
        "dislikes 5, 5000 negatives": (img, {"w_dis": 5, "n_neg": 5000}),
        "dislikes 5 + luna 0.5": ({**img, "luna": 0.5}, {"w_dis": 5}),
        "dislikes 5 + luna 1": ({**img, "luna": 1.0}, {"w_dis": 5}),
        "dislikes 5 + brand one-hot": ({**img, "brand one-hot": 1.0}, {"w_dis": 5}),
        "dislikes 5 + brand text 0.5": ({**img, "brand text": 0.5}, {"w_dis": 5}),
        "dislikes 5 + brand text 1": ({**img, "brand text": 1.0}, {"w_dis": 5}),
        "dislikes 5 + title text 0.5": ({**img, "title text": 0.5}, {"w_dis": 5}),
        "dislikes 5 + title text 1": ({**img, "title text": 1.0}, {"w_dis": 5}),
        "brand text only, dislikes 5": ({"brand text": 1.0}, {"w_dis": 5}),
    }
    rows, base = [], None
    for name, (blocks, kw) in recipes.items():
        p = percentiles(blocks, **kw)
        if name == "+ dislikes weight 5":
            base = p
        r = summary(name, p)
        if base is not None and p is not base:
            r["AUC all vs dislikes 5"] = difference(p, base, np.ones(len(p), bool))
            r["AUC fair vs dislikes 5"] = difference(p, base, fair)
        rows.append(r)
        print(r, flush=True)
    # reference: Luna's own score as the ranker
    luna_p = np.array([((df.target.values[(bucket == bucket[i]) & (y < 0)] >= df.target.values[i]).sum() + 1) / (((bucket == bucket[i]) & (y < 0)).sum() + 1) for i in labelled])
    rows.append(summary("reference: Luna's score", luna_p))
    pd.set_option("display.width", 250)
    print("\n" + pd.DataFrame(rows).set_index("recipe").fillna("").to_string())
    pd.DataFrame(rows).to_csv(DATA / "head_recipe.csv", index=False)
