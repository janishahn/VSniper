"""Embed the evaluation images with one encoder. usage: embed.py <tag> [--all]
Writes data/emb/<tag>.npz with cover, second-photo and wardrobe embeddings. --all embeds every cover, not the sample."""
import json, sys, re
import numpy as np, pandas as pd, torch, torchvision, timm, open_clip
from PIL import Image
from common import DATA, load_candidates

ENCODERS = {  # tag -> (library, name, pretrained)
    "siglip-b": ("timm", "vit_base_patch16_siglip_224.webli", None),
    "siglip2-b": ("timm", "vit_base_patch16_siglip_224.v2_webli", None),
    "fashion-siglip": ("open_clip", "hf-hub:Marqo/marqo-fashionSigLIP", None),
    "mobileclip2-s0": ("open_clip", "MobileCLIP2-S0", "dfndr2b"),
    "mobileclip2-s2": ("open_clip", "MobileCLIP2-S2", "dfndr2b"),
    "dinov3-s": ("timm", "vit_small_patch16_dinov3.lvd1689m", None),
    "mnv3l": ("torchvision", "mobilenet_v3_large", None),
}

def build(tag):
    """Return (image encoder module, input side length, mean, std). Images are squashed to a square."""
    lib, name, pretrained = ENCODERS[tag]
    if lib == "torchvision":
        m = torchvision.models.mobilenet_v3_large(weights=torchvision.models.MobileNet_V3_Large_Weights.IMAGENET1K_V2)
        m.classifier[-1] = torch.nn.Identity()
        return m.eval(), 224, (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
    if lib == "timm":
        m = timm.create_model(name, pretrained=True, num_classes=0)
        cfg = timm.data.resolve_data_config({}, model=m)
        return m.eval(), min(cfg["input_size"][1], 256), cfg["mean"], cfg["std"]
    model = open_clip.create_model(name, pretrained=pretrained)
    cfg = model.visual.preprocess_cfg
    size = cfg["size"] if isinstance(cfg["size"], int) else cfg["size"][0]
    class Tower(torch.nn.Module):
        def __init__(self): super().__init__(); self.m = model
        def forward(self, x): return self.m.encode_image(x)
    return Tower().eval(), size, cfg["mean"], cfg["std"]

class Images(torch.utils.data.Dataset):
    def __init__(self, paths, size, mean, std):
        self.paths, self.size = paths, size
        self.mean, self.std = torch.tensor(mean)[:, None, None], torch.tensor(std)[:, None, None]
    def __len__(self): return len(self.paths)
    def __getitem__(self, k):
        im = Image.open(self.paths[k]).convert("RGB").resize((self.size, self.size), Image.BICUBIC)
        x = torch.from_numpy(np.asarray(im).copy()).permute(2, 0, 1).float() / 255
        return (x - self.mean) / self.std

def eval_ids(df):
    """All listings with a verdict plus a fixed random sample of up to 3,500 covers per bucket."""
    path = DATA / "eval_ids.csv"
    if not path.exists():
        rng = np.random.default_rng(0)
        have = df[df.has_cover]
        ids = set(have[have.feedback != "unknown"].item_id)
        for _, g in have.groupby("bucket"):
            ids |= set(rng.choice(g.item_id.values, min(3500, len(g)), replace=False))
        pd.Series(sorted(ids)).to_csv(path, index=False)
    return pd.read_csv(path).iloc[:, 0].astype(str).tolist()

if __name__ == "__main__":
    tag = sys.argv[1]
    df = load_candidates()
    ids = df[df.has_cover].item_id.tolist() if "--all" in sys.argv else eval_ids(df)
    second = [i for i in ids if (DATA / "img2" / f"{i}.webp").exists()]
    wardrobe = [json.loads(l) for l in open(DATA / "taste_samples.jsonl")]
    wardrobe = [w for w in wardrobe if w["kind"] == "wardrobe" and w["storage_path"] and (DATA / "uploads" / w["storage_path"]).exists()]
    model, size, mean, std = build(tag)
    model = model.to("mps")
    def run(paths):
        dl = torch.utils.data.DataLoader(Images(paths, size, mean, std), batch_size=64, num_workers=4)
        out = []
        with torch.no_grad():
            for x in dl: out.append(model(x.to("mps")).float().cpu().numpy())
        return np.concatenate(out)
    (DATA / "emb").mkdir(exist_ok=True)
    np.savez(DATA / "emb" / f"{tag}{'_all' if '--all' in sys.argv else ''}.npz",
             cover=run([DATA / "img310" / f"{i}.webp" for i in ids]), ids=np.array(ids),
             second=run([DATA / "img2" / f"{i}.webp" for i in second]), second_ids=np.array(second),
             wardrobe=run([DATA / "uploads" / w["storage_path"] for w in wardrobe]), wardrobe_bucket=np.array([w["clothing_item"] for w in wardrobe]))
    print(tag, "size", size, "covers", len(ids), "second", len(second), "wardrobe", len(wardrobe), "params(M)", round(sum(p.numel() for p in model.parameters()) / 1e6, 1), flush=True)
