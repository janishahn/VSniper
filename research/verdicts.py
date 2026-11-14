import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score, average_precision_score
from common import load, pass_rate_for_recall
df = load(); tr = ~df.test.values
z = lambda v: (v - v[tr].mean()) / v[tr].std()
ft = np.load("data/pred_ft_large.npy"); text = np.load("data/pred_text.npy")
cands = {"Luna screen": df.screen_p.fillna(0).values, "MobileNetV3-L fine-tuned": ft, "MobileNetV3-L + text": z(ft) + z(text)}
t = np.where(df.test.values)[0]; rng = np.random.default_rng(0)
j = df.judged.values; sc = df.score.values
print(f"bootstrap 95% intervals on the held-out days (n={len(t)}, Luna>=80: {int((j & (sc >= 80))[t].sum())}):")
for name, p in cands.items():
    rho, ap, pr = [], [], []
    for _ in range(300):
        b = rng.choice(t, len(t)); jb = j[b]; pos = jb & (sc[b] >= 80)
        rho.append(spearmanr(p[b][jb], sc[b][jb]).statistic); ap.append(average_precision_score(pos, p[b])); pr.append(pass_rate_for_recall(p[b], pos, 0.95))
    q = lambda v: "{:.2f}-{:.2f}".format(*np.quantile(v, [.025, .975]))
    print(f"  {name:26s} rho {q(rho)} | AP(Luna>=80) {q(ap)} | share passed to keep 95% of Luna>=80: {q(pr)}")
al = t[(j & (sc >= 95))[t]]
for name, p in cands.items():
    pct = sorted((p[t] >= p[i]).mean() for i in al)
    print(f"  Luna's {len(al)} held-out alerts sit in {name}'s top: " + ", ".join(f"{x:.1%}" for x in pct))
fb = df[df.feedback != "unknown"]
like = (fb.feedback == "like").values
print(f"\nuser verdicts with an image: {len(fb)} (like {like.sum()} / dislike {(~like).sum()}); in held-out days: {int(fb.test.sum())}")
print("  AUC like-vs-dislike  Luna judge score: {:.2f} | Luna screen: {:.2f} | student (trained on most of these days, so optimistic): {:.2f}".format(
    roc_auc_score(like, fb.score), roc_auc_score(like, fb.screen_p.fillna(0)), roc_auc_score(like, ft[fb.index])))
