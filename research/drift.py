"""Does label drift between taste-profile versions limit the student? Frozen mnv3l embeddings, ridge per bucket."""
import numpy as np, pandas as pd, warnings
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from common import load, report
warnings.filterwarnings("ignore")
df = load(); X = np.load("data/emb_mnv3l.npy"); tr = ~df.test.values
raw = np.where(df.judged, df.score, 50 * df.screen_p.fillna(0.5)).astype(float)
# rank-normalise the target inside each bucket x profile version, so versions with shifted score scales line up
rank = pd.Series(raw).groupby([df.bucket, df.prompt_version]).rank(pct=True).values
def run(name, y, keep):
    pred = np.zeros(len(df))
    for b in df.bucket.unique():
        m = (df.bucket == b).values; a = m & tr & keep
        if a.sum() < 50: a = m & tr
        sc = StandardScaler().fit(X[a]); pred[m] = Ridge(alpha=3000).fit(sc.transform(X[a]), y[a]).predict(sc.transform(X[m]))
    return report(name, df, pred)
allrows = np.ones(len(df), bool)
rows = [run("raw score, all train days", raw, allrows), run("rank-in-version, all train days", rank, allrows),
        run("raw, profile v>=6 only", raw, (df.prompt_version >= 6).values), run("rank-in-version, v>=6 only", rank, (df.prompt_version >= 6).values),
        run("raw, v>=9 only", raw, (df.prompt_version >= 9).values), run("raw, judged rows only", raw, df.judged.values)]
pd.set_option("display.width", 250); print(pd.DataFrame(rows).set_index("model").round(3).to_string())
print("train rows:", int(tr.sum()), "| v>=6:", int((tr & (df.prompt_version >= 6)).sum()), "| v>=9:", int((tr & (df.prompt_version >= 9)).sum()))
