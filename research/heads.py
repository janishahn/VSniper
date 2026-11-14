"""Frozen-embedding students: fit small heads on Nov 6-11, score against Luna on Nov 12-14."""
import sys, numpy as np, pandas as pd, warnings
from sklearn.linear_model import Ridge
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import StandardScaler
from common import load, report
warnings.filterwarnings("ignore")
df = load()
y = np.where(df.judged, df.score, 50 * df.screen_p.fillna(0.5)).astype(float)  # screened-out: pseudo score <= 25
tr, te = ~df.test.values, df.test.values
rows = []

def per_bucket(fit_predict, train_mask=None):
    pred = np.zeros(len(df))
    for b in df.bucket.unique():
        m = (df.bucket == b).values
        a = m & tr if train_mask is None else m & tr & train_mask
        pred[m] = fit_predict(a, m)
    return pred

# Reference: Luna's own search-card screen (cover + title), same test rows.
rows.append(report("Luna screen (cover+title)", df, df.screen_p.fillna(0).values))

def ridge_on(X, alpha):
    def f(a, m):
        sc = StandardScaler().fit(X[a]); r = Ridge(alpha=alpha).fit(sc.transform(X[a]), y[a]); return r.predict(sc.transform(X[m]))
    return f

tf = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=3, sublinear_tf=True, max_features=200_000).fit(df.text[tr])
T = tf.transform(df.text)
def text_fp(a, m):
    return Ridge(alpha=3.0).fit(T[a], y[a]).predict(T[m])
p_text = per_bucket(text_fp)
rows.append(report("text only (tf-idf ridge)", df, p_text))

preds = {}
for name in sys.argv[1:]:
    X = np.load(f"data/emb_{name}.npy")
    best = None
    for alpha in (300, 1000, 3000, 10000):
        p = per_bucket(ridge_on(X, alpha)); r = report(f"{name} ridge a={alpha}", df, p)
        if best is None or r["AP80"] > best[1]["AP80"]: best = (p, r)
    preds[name] = best[0]; rows.append(best[1])
    z = lambda v: (v - v[tr].mean()) / v[tr].std()
    rows.append(report(f"{name} + text", df, z(best[0]) + z(p_text)))
    np.save(f"data/pred_{name}.npy", best[0])
np.save("data/pred_text.npy", p_text)
out = pd.DataFrame(rows).set_index("model")
pd.set_option("display.width", 250)
print(out.round(3).to_string())
t = df[te]
print("\ntest rows", len(t), "| judged", int(t.judged.sum()), "| Luna>=80:", int((t.judged & (t.score >= 80)).sum()), "| >=90:", int((t.judged & (t.score >= 90)).sum()), "| >=95:", int((t.judged & (t.score >= 95)).sum()), "| Luna screen pass rate:", round(t.judged.mean(), 3))
