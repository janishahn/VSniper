"""Embed each listing's brand and title with an encoder's text tower. usage: embed_text.py <tag>"""
import sys
import numpy as np, torch, open_clip
from common import DATA, load_candidates
from embed import ENCODERS, eval_ids

tag = sys.argv[1]
_, name, pretrained = ENCODERS[tag]
model = open_clip.create_model(name, pretrained=pretrained).eval().to("mps")
tokenizer = open_clip.get_tokenizer(name)
df = load_candidates().set_index("item_id")
ids = eval_ids(df.reset_index())
texts = [f"{df.brand[i] or ''} {df.title[i] or ''}".strip().lower() for i in ids]
out = []
with torch.no_grad():
    for a in range(0, len(texts), 256):
        out.append(model.encode_text(tokenizer(texts[a:a + 256]).to("mps")).float().cpu().numpy())
np.savez(DATA / "emb" / f"{tag}_text.npz", text=np.concatenate(out), ids=np.array(ids))
print(tag, "text", np.concatenate(out).shape)
