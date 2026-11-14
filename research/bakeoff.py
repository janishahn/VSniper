"""Compare encoders. usage: bakeoff.py <tag> [<tag> ...]
A. Taste from your verdicts: hold one like out, train a small head on the other likes against random listings,
   and see where the held-out like ranks among the listings of its bucket.
B. Agreement with Luna: a linear head trained on Luna's scores from earlier days, tested on later days."""
import sys, warnings
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from common import DATA, load_candidates

warnings.filterwarnings("ignore")
cands = load_candidates().set_index("item_id")

def unit(X, mean=None):
    mean = X.mean(0) if mean is None else mean
    X = X - mean
    return X / np.linalg.norm(X, axis=1, keepdims=True), mean

def evaluate(tag, use_second=False, use_wardrobe=False, use_text=False, C=10.0, seed=0):
    z = np.load(DATA / "emb" / f"{tag}.npz")
    df = cands.loc[z["ids"]].reset_index()
    X = z["cover"].astype(np.float64)
    if use_second:  # average the cover with the second photo where there is one
        pos = {i: k for k, i in enumerate(z["second_ids"])}
        has = np.array([i in pos for i in z["ids"]])
        X[has] = (X[has] + z["second"][[pos[i] for i in z["ids"][has]]]) / 2
    X, mean = unit(X)
    W, _ = unit(z["wardrobe"].astype(np.float64), mean)
    Xr = z["cover"].astype(np.float64)
    if use_text:  # append the title embedding to the image embedding
        T = np.load(DATA / "emb" / f"{tag}_text.npz")["text"].astype(np.float64)
        X = np.hstack([X, unit(T)[0]]); Xr = np.hstack([Xr, T])
        W = np.hstack([W, np.zeros((len(W), T.shape[1]))])
    like = (df.feedback == "like").values; dis = (df.feedback == "dislike").values
    rng = np.random.default_rng(seed)
    like_pct, held = [], []  # held: (is_like, percentile rank) for every verdict, each scored by a head that never saw it
    for b in df.bucket.unique():
        m = np.where((df.bucket == b).values)[0]
        L = [i for i in m if like[i]]; D = [i for i in m if dis[i]]
        unl = np.array([i for i in m if not like[i] and not dis[i]])
        neg = rng.choice(unl, min(3000, len(unl)), replace=False)
        extra = W[z["wardrobe_bucket"] == b] if use_wardrobe else np.zeros((0, X.shape[1]))
        def rank_of(i, pos_idx, hard_idx):
            Xt = np.vstack([X[pos_idx], extra, X[hard_idx], X[neg]])
            yt = np.r_[np.ones(len(pos_idx) + len(extra)), np.zeros(len(hard_idx) + len(neg))]
            w = np.r_[np.ones(len(pos_idx) + len(extra)), np.full(len(hard_idx), 20.0), np.ones(len(neg))]
            s = LogisticRegression(C=C, class_weight="balanced", max_iter=3000).fit(Xt, yt, sample_weight=w).decision_function(X[m])
            return float((s >= s[m == i][0]).mean())
        for i in L:
            like_pct.append(rank_of(i, [k for k in L if k != i], []))                 # likes vs random only
            held.append((1, rank_of(i, [k for k in L if k != i], D)))                 # plus the dislikes as hard negatives
        for i in D:
            held.append((0, rank_of(i, L, [k for k in D if k != i])))
    lp = np.array(like_pct); h = np.array(held)
    # B. agreement with Luna
    tr, te = ~df.test.values, df.test.values
    pred = np.zeros(len(df))
    for b in df.bucket.unique():
        m = (df.bucket == b).values; a = m & tr
        sc = StandardScaler().fit(Xr[a])
        best = None
        for alpha in (100, 300, 1000, 3000):
            p = Ridge(alpha=alpha).fit(sc.transform(Xr[a]), df.target.values[a]).predict(sc.transform(Xr[m]))
            v = m & te & df.judged.values
            r = spearmanr(p[v[m]], df.score.values[v]).statistic if v.sum() > 20 else 0
            if best is None or r > best[0]: best = (r, p)
        pred[m] = best[1]
    j = te & df.judged.values
    return {
        "encoder": tag + ("+2nd" if use_second else "") + ("+wardrobe" if use_wardrobe else "") + ("+title" if use_text else ""), "dim": X.shape[1],
        "like median rank %": round(100 * np.median(lp), 1), "like typical rank %": round(100 * float(np.exp(np.mean(np.log(lp)))), 1), "likes in top 1%": f"{(lp <= .01).sum()}/{len(lp)}", "likes in top 5%": f"{(lp <= .05).sum()}/{len(lp)}",
        "like-vs-dislike AUC": round(roc_auc_score(h[:, 0], -h[:, 1]), 2),
        "Luna rho": round(spearmanr(pred[j], df.score.values[j]).statistic, 3),
        "Luna AP80": round(average_precision_score((df.judged.values & (df.score.values >= 80))[te], pred[te]), 3),
    }

if __name__ == "__main__":
    rows = []
    for tag in sys.argv[1:]:
        rows.append(evaluate(tag))
        rows.append(evaluate(tag, use_second=True))
        rows.append(evaluate(tag, use_wardrobe=True))
        if (DATA / "emb" / f"{tag}_text.npz").exists():
            rows.append(evaluate(tag, use_text=True))
            rows.append(evaluate(tag, use_text=True, use_second=True))
    pd.set_option("display.width", 250)
    print(pd.DataFrame(rows).set_index("encoder").to_string())
