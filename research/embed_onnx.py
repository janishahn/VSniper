"""Embed the evaluation covers with an exported ONNX file, to check that an optimized export keeps its quality.
usage: embed_onnx.py <tag> <variant>   e.g. fashion-siglip int8  ->  data/emb/fashion-siglip.int8.npz"""
import json, sys
import numpy as np, onnxruntime as ort
from common import DATA, load_candidates
from embed import build, Images, eval_ids

tag, variant = sys.argv[1], sys.argv[2]
_, size, mean, std = build(tag)
sess = ort.InferenceSession(str(DATA / "onnx" / f"{tag}.{variant}.onnx"), providers=["CPUExecutionProvider"])
ids = eval_ids(load_candidates())
wardrobe = [w for w in map(json.loads, open(DATA / "taste_samples.jsonl")) if w["kind"] == "wardrobe" and w["storage_path"]]
def run(paths):
    ds = Images(paths, size, mean, std)
    return np.concatenate([sess.run(None, {"image": np.stack([ds[k].numpy() for k in range(a, min(a + 32, len(ds)))])})[0] for a in range(0, len(ds), 32)])
ref = np.load(DATA / "emb" / f"{tag}.npz")
np.savez(DATA / "emb" / f"{tag}.{variant}.npz", cover=run([DATA / "img310" / f"{i}.webp" for i in ids]), ids=np.array(ids),
         second=ref["second"], second_ids=ref["second_ids"], wardrobe=run([DATA / "uploads" / w["storage_path"] for w in wardrobe]),
         wardrobe_bucket=np.array([w["clothing_item"] for w in wardrobe]))
print("saved", tag, variant)
