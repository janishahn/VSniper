"""Decode every cover once into a uint8 memmap (N, 320, 224, 3) so training needs no worker processes."""
import numpy as np, pandas as pd, time
from multiprocessing import Pool
from PIL import Image
H, W = 320, 224
def dec(i):
    return np.asarray(Image.open(f"data/img310/{i}.webp").convert("RGB").resize((W, H), Image.BICUBIC))
if __name__ == "__main__":
    ids = pd.read_csv("data/emb_ids.csv").iloc[:, 0].astype(str).tolist()
    mm = np.lib.format.open_memmap("data/pix.npy", mode="w+", dtype=np.uint8, shape=(len(ids), H, W, 3))
    t = time.time()
    with Pool(7) as p:
        for k, a in enumerate(p.imap(dec, ids, chunksize=64)):
            mm[k] = a
    mm.flush(); print("packed", mm.shape, f"{time.time()-t:.0f}s")
