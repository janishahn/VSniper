"""CPU timing of exported encoders. Run where the app runs: python bench.py <model.onnx> [...]
Reports per-image time for decoding + resizing a cover and for the encoder, over thread counts."""
import glob, os, resource, sys, time
import numpy as np, onnxruntime as ort
from PIL import Image

covers = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "covers", "*.webp")))

def prep(path, size):
    im = Image.open(path).convert("RGB").resize((size, size), Image.BICUBIC)
    return ((np.asarray(im, dtype=np.float32) / 255 - 0.5) / 0.5).transpose(2, 0, 1)

print("cpus", os.cpu_count(), "| onnxruntime", ort.__version__, flush=True)
for path in sys.argv[1:]:
    so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    size = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"]).get_inputs()[0].shape[2]
    t = time.perf_counter(); xs = np.stack([prep(c, size) for c in covers]); prep_ms = (time.perf_counter() - t) / len(covers) * 1000
    print(f"\n{os.path.basename(path)} ({os.path.getsize(path) / 1e6:.0f} MB, {size}px) | decode+resize {prep_ms:.1f} ms/image", flush=True)
    for threads in (1, 2, 4, 8):
        so = ort.SessionOptions(); so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL; so.intra_op_num_threads = threads
        t = time.perf_counter(); sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"]); load_s = time.perf_counter() - t
        line = f"  threads={threads} load {load_s:.1f}s"
        for bs in (1, 8):
            x = xs[:bs]
            for _ in range(3): sess.run(None, {"image": x})
            ts = []
            while len(ts) < 12 or sum(ts) < 3.0:
                t = time.perf_counter(); sess.run(None, {"image": x}); ts.append(time.perf_counter() - t)
            line += f" | batch {bs}: {np.median(ts) / bs * 1000:.0f} ms/image"
        print(line, flush=True)
    print(f"  peak memory so far {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f} MB", flush=True)
