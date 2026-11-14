import gzip, json, pathlib
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import average_precision_score

DATA = pathlib.Path(__file__).parent / "data"
TEST_FROM = "2026-11-12"  # train on earlier days, test on these and later


def load_candidates() -> pd.DataFrame:
    """Every candidate from the pop-os snapshot, with Luna's judge score and screen probability."""
    df = pd.DataFrame(json.loads(line) for line in gzip.open(DATA / "candidates.jsonl.gz", "rt"))
    df["item_id"] = df.item_id.astype(str)
    df["screened_out"] = df.explanation.fillna("").str.startswith("Screened out")
    df["judged"] = ~df.screened_out
    df["screen_p"] = df.raw_response.map(lambda r: (r or {}).get("screen_probability"))
    df["day"] = df.created_at.str[:10]
    df["test"] = df.day >= TEST_FROM
    df["has_cover"] = df.item_id.map(lambda i: (DATA / "img310" / f"{i}.webp").exists())
    df["has_second"] = df.item_id.map(lambda i: (DATA / "img2" / f"{i}.webp").exists())
    df["text"] = (df.brand.fillna("") + " | " + df.title.fillna("") + " | " + df.description.fillna("").str[:600]).str.lower()
    # Screened-out listings have no judge score; give them a low stand-in (at most 25).
    df["target"] = np.where(df.judged, df.score, 50 * df.screen_p.fillna(0.5)).astype(float)
    return df


def load(emb_ids="emb_ids.csv") -> pd.DataFrame:
    """Candidates aligned to a saved embedding id list."""
    ids = pd.read_csv(DATA / emb_ids).iloc[:, 0].astype(str)
    return load_candidates().set_index("item_id").loc[ids].reset_index()


def pass_rate_for_recall(score, pos, recall):
    """Smallest share of listings to pass on so that `recall` of positives are kept."""
    thr = np.quantile(score[pos], 1 - recall, method="lower")
    return float((score >= thr).mean())


def report(name, df, pred, mask=None):
    """Score a student `pred` against Luna on test rows. Screened-out rows count as non-alerts."""
    t = df.test.values if mask is None else (df.test.values & mask)
    d, p = df[t], pred[t]
    j = d.judged.values
    row = {"model": name, "n": int(t.sum()), "rho_judged": spearmanr(p[j], d.score.values[j]).statistic}
    for T in (80, 90):
        pos = j & (d.score.values >= T)
        row[f"AP{T}"] = average_precision_score(pos, p)
        row[f"pass@95r_{T}"] = pass_rate_for_recall(p, pos, 0.95)
        row[f"pass@99r_{T}"] = pass_rate_for_recall(p, pos, 0.99)
    k = int((j & (d.score.values >= 90)).sum())
    top = np.argsort(-p)[:k]
    row["top-k mean Luna"] = float(np.where(j, d.score.values, 0)[top].mean())
    row["top-k >=80"] = float((j & (d.score.values >= 80))[top].mean())
    return row
