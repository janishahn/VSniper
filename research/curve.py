"""Learning curve and random-split check with frozen embeddings. usage: curve.py <emb tag>"""
import sys, numpy as np, pandas as pd, warnings
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from scipy.stats import spearmanr
from common import load
warnings.filterwarnings("ignore")
df = load(); X = np.load(f"data/emb_{sys.argv[1]}.npy"); tr = ~df.test.values; te = df.test.values & df.judged.values
y = np.where(df.judged, df.score, 50 * df.screen_p.fillna(0.5)).astype(float)
rng = np.random.default_rng(0)
def fit_rho(train_mask, test_mask, alpha=3000):
    pred = np.zeros(len(df))
    for b in df.bucket.unique():
        m = (df.bucket == b).values; a = m & train_mask
        sc = StandardScaler().fit(X[a]); pred[m] = Ridge(alpha=alpha).fit(sc.transform(X[a]), y[a]).predict(sc.transform(X[m]))
    return spearmanr(pred[test_mask], df.score.values[test_mask]).statistic
idx = np.where(tr)[0]
print("learning curve (time split, rho vs Luna judge on held-out days):")
for n in (1000, 2500, 5000, 10000, len(idx)):
    rs = []
    for _ in range(3 if n < len(idx) else 1):
        m = np.zeros(len(df), bool); m[rng.choice(idx, n, replace=False)] = True
        rs.append(max(fit_rho(m, te, a) for a in (300, 1000, 3000)))
    print(f"  train rows {n:6d}: rho={np.mean(rs):.3f}")
perm = rng.permutation(len(df)); cut = int(0.8 * len(df))
m = np.zeros(len(df), bool); m[perm[:cut]] = True
print("random 80/20 split (same days and profile versions in train and test): rho=%.3f" % fit_rho(m, ~m & df.judged.values))
