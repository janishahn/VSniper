"""Compare saved student predictions against Luna on the held-out days. usage: ft_eval.py <pred tags...>"""
import sys, numpy as np, pandas as pd
from common import load, report
df = load(); tr = ~df.test.values
z = lambda v: (v - v[tr].mean()) / v[tr].std()
text = np.load("data/pred_text.npy")
rows = [report("Luna screen (cover+title)", df, df.screen_p.fillna(0).values)]
for tag in sys.argv[1:]:
    p = np.load(f"data/pred_{tag}.npy")
    rows += [report(tag, df, p), report(f"{tag} + text", df, z(p) + z(text))]
pd.set_option("display.width", 250)
print(pd.DataFrame(rows).set_index("model").round(3).to_string())
