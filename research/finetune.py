"""End-to-end fine-tune of MobileNetV3 on Luna scores. usage: finetune.py <small|large> <epochs> <tag> [--aux]"""
import sys, time, math, numpy as np, torch, torchvision
import torch.nn.functional as F
from common import load, report

dev = "mps"
MEAN = torch.tensor((0.485, 0.456, 0.406), device=dev)[None, :, None, None]
STD = torch.tensor((0.229, 0.224, 0.225), device=dev)[None, :, None, None]

def to_input(u8, train):
    x = (u8.to(dev).permute(0, 3, 1, 2).float() / 255 - MEAN) / STD
    if not train: return x
    n = x.shape[0]  # random zoom (0.75-1), shift and horizontal flip, on the GPU
    s = torch.empty(n, device=dev).uniform_(0.75, 1.0)
    flip = torch.where(torch.rand(n, device=dev) < 0.5, -1.0, 1.0)
    theta = torch.zeros(n, 2, 3, device=dev)
    theta[:, 0, 0] = s * flip; theta[:, 1, 1] = s
    theta[:, :, 2] = (torch.rand(n, 2, device=dev) * 2 - 1) * (1 - s)[:, None]
    return F.grid_sample(x, F.affine_grid(theta, x.shape, align_corners=False), mode="bilinear", padding_mode="reflection", align_corners=False)

if __name__ == "__main__":
    arch, epochs, tag = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    aux = "--aux" in sys.argv  # also learn Luna's screen probability on a second set of outputs
    torch.manual_seed(0); rng = np.random.default_rng(0)
    df = load()
    pix = np.load("data/pix.npy", mmap_mode="r")
    buckets = sorted(df.bucket.unique()); nb = len(buckets)
    bidx = torch.from_numpy(df.bucket.map(buckets.index).values)
    y = torch.from_numpy((np.where(df.judged, df.score, 50 * df.screen_p.fillna(0.5)) / 100).astype("float32"))
    sp = torch.from_numpy(df.screen_p.fillna(-1).values.astype("float32"))
    tr = np.where(~df.test.values)[0]
    M = torchvision.models
    net = M.mobilenet_v3_large(weights=M.MobileNet_V3_Large_Weights.IMAGENET1K_V2) if arch == "large" else M.mobilenet_v3_small(weights=M.MobileNet_V3_Small_Weights.IMAGENET1K_V1)
    net.classifier[-1] = torch.nn.Linear(net.classifier[-1].in_features, nb * (2 if aux else 1))
    net = net.to(dev)
    bs = 64; steps = len(tr) // bs
    opt = torch.optim.AdamW([{"params": net.features.parameters(), "lr": 3e-4}, {"params": net.classifier.parameters(), "lr": 1e-3}], weight_decay=0.05)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[3e-4, 1e-3], total_steps=epochs * steps, pct_start=0.15)
    def predict():
        net.eval(); out = []
        with torch.no_grad():
            for a in range(0, len(df), 256):
                o = net(to_input(torch.from_numpy(pix[a:a + 256]), False))
                out.append(o.gather(1, bidx[a:a + 256].to(dev)[:, None]).squeeze(1).cpu().numpy())
        net.train(); return np.concatenate(out)
    for ep in range(epochs):
        t, tot = time.time(), 0.0
        perm = rng.permutation(tr)
        for step in range(steps):
            k = np.sort(perm[step * bs:(step + 1) * bs])
            o = net(to_input(torch.from_numpy(pix[k]), True))
            b = bidx[k].to(dev)[:, None]
            loss = F.binary_cross_entropy_with_logits(o.gather(1, b).squeeze(1), y[k].to(dev))
            if aux:
                s = sp[k].to(dev); has = s >= 0
                loss = loss + 0.5 * F.binary_cross_entropy_with_logits(o.gather(1, b + nb).squeeze(1)[has], s[has])
            opt.zero_grad(); loss.backward(); opt.step(); sched.step(); tot += loss.item()
            if step % 100 == 0: print(f"  ep{ep+1} step {step}/{steps} {time.time()-t:.0f}s", flush=True)
        p = predict(); r = report(f"{tag} ep{ep+1}", df, p)
        print(f"ep{ep+1} loss={tot/steps:.4f} {time.time()-t:.0f}s", {k: round(v, 3) for k, v in r.items() if k != "model"}, flush=True)
    np.save(f"data/pred_{tag}.npy", p)
    torch.save(net.state_dict(), f"data/{tag}.pt")
