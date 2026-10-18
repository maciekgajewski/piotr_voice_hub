"""T-003: onnxruntime-gpu CUDAExecutionProvider on this GPU (Piper's runtime)."""

import sys
import time

import numpy as np
import onnx
import onnxruntime as ort
from gpu_info import GpuMemorySampler
from onnx import TensorProto, helper

SIZE = 512
CUDA = "CUDAExecutionProvider"


def build_model() -> bytes:
    a = helper.make_tensor_value_info("a", TensorProto.FLOAT, [SIZE, SIZE])
    b = helper.make_tensor_value_info("b", TensorProto.FLOAT, [SIZE, SIZE])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [SIZE, SIZE])
    nodes = [
        helper.make_node("MatMul", ["a", "b"], ["m"]),
        helper.make_node("Relu", ["m"], ["y"]),
    ]
    graph = helper.make_graph(nodes, "smoke", [a, b], [y])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model.SerializeToString()


def main() -> int:
    sampler = GpuMemorySampler()
    print(f"GPU: {sampler.describe()}")
    print(f"onnxruntime {ort.__version__}, available providers: {ort.get_available_providers()}")

    options = ort.SessionOptions()
    options.log_severity_level = 1
    session = ort.InferenceSession(build_model(), options, providers=[CUDA])
    active = session.get_providers()
    print(f"Session providers: {active}")
    assert active[0] == CUDA, f"CUDA EP not active (fell back to {active})"

    rng = np.random.default_rng(0)
    a = rng.standard_normal((SIZE, SIZE), dtype=np.float32)
    b = rng.standard_normal((SIZE, SIZE), dtype=np.float32)
    start = time.perf_counter()
    (y,) = session.run(None, {"a": a, "b": b})
    first = time.perf_counter() - start
    start = time.perf_counter()
    session.run(None, {"a": a, "b": b})
    second = time.perf_counter() - start
    expected = np.maximum(a @ b, 0)
    # Relative to the output scale: cuBLAS may use TF32, so elementwise rtol is too strict.
    error = float(np.max(np.abs(y - expected)) / np.max(np.abs(expected)))
    print(f"Max error relative to output scale: {error:.2e}")
    assert error < 1e-2, f"CUDA result differs from numpy (relative error {error:.2e})"
    print(f"Run on {CUDA}: first {first * 1000:.1f} ms, second {second * 1000:.1f} ms; result OK")
    used = sampler.used_mib()
    print(f"Device VRAM used: {used} MiB (before session: {sampler.baseline_mib} MiB)")
    print("ORT SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
