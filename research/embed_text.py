"""Embed each listing's text with an encoder's text tower. usage: embed_text.py <tag> [--all]
Writes data/emb/<tag>_text.npz with `text` (brand and title) and `brand` (brand alone). --all covers every listing with a cover."""
import sys
import numpy as np, torch, open_clip
from common import DATA, load_candidates
from embed import ENCODERS, eval_ids

tag = sys.argv[1]
_, name, pretrained = ENCODERS[tag]
model = open_clip.create_model(name, pretrained=pretrained).eval().to("mps")
tokenizer = open_clip.get_tokenizer(name)
df = load_candidates()
ids = df[df.has_cover].item_id.tolist() if "--all" in sys.argv else eval_ids(df)
df = df.set_index("item_id").loc[ids]
brand = df.brand.fillna("").str.strip().str.lower()

def encode(texts):
    out = []
    with torch.no_grad():
        for a in range(0, len(texts), 256):
            out.append(model.encode_text(tokenizer(texts[a:a + 256]).to("mps")).float().cpu().numpy())
    return np.concatenate(out)

names = sorted(set(brand))
by_name = dict(zip(names, encode(names)))
text = encode((brand + " " + df.title.fillna("").str.lower()).str.strip().tolist())
np.savez(DATA / "emb" / f"{tag}{'_all' if '--all' in sys.argv else ''}_text.npz", text=text, brand=np.stack([by_name[b] for b in brand]), ids=np.array(ids))
print(tag, "text", text.shape, "brands", len(names))
