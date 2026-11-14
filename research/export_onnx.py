"""Export an encoder's image tower to ONNX (float32 and int8) and check both against PyTorch. usage: export_onnx.py <tag>"""
import sys, warnings
import numpy as np, torch, onnxruntime as ort
from onnxruntime.quantization import quantize_dynamic, QuantType
from PIL import Image
from common import DATA
from embed import build, Images, eval_ids, load_candidates

warnings.filterwarnings("ignore")
tag = sys.argv[1]
model, size, mean, std = build(tag)
if tag.startswith("mobileclip"):  # fold the training-time branches into single convolutions
    from timm.utils import reparameterize_model
    model = reparameterize_model(model).eval()
out = DATA / "onnx"; out.mkdir(exist_ok=True)
fp32, int8 = out / f"{tag}.onnx", out / f"{tag}.int8.onnx"
x = torch.randn(1, 3, size, size)
torch.onnx.export(model, x, fp32, input_names=["image"], output_names=["embedding"], dynamic_axes={"image": {0: "n"}, "embedding": {0: "n"}}, opset_version=17, dynamo=False)
# Dynamic int8 for the transformer's matrix products only; quantized convolutions are slow on CPUs without VNNI.
quantize_dynamic(fp32, int8, weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul", "Gemm"])
ids = eval_ids(load_candidates())[:64]
batch = torch.stack([Images([DATA / "img310" / f"{i}.webp" for i in ids], size, mean, std)[k] for k in range(len(ids))])
with torch.no_grad(): ref = model(batch).numpy()
cos = lambda a, b: float(np.mean(np.sum(a * b, 1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))))
for path in (fp32, int8):
    e = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]).run(None, {"image": batch.numpy()})[0]
    print(f"{path.name}: {path.stat().st_size / 1e6:.0f} MB, input {size}px, dim {e.shape[1]}, mean cosine to PyTorch {cos(ref, e):.4f}")
