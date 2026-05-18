from __future__ import annotations

import argparse
import math
import sys
import time


def parse_device_index(device: str) -> int:
    if not device.startswith("cuda:"):
        raise ValueError(f"Only cuda devices are supported, got {device}")
    return int(device.split(":", 1)[1])


def run_gpu_job(duration_seconds: int, memory_gb: float, device: str, job_id: str) -> int:
    try:
        import torch
    except ImportError:
        print("PyTorch is required for GPU workload generation", file=sys.stderr)
        return 2

    if not torch.cuda.is_available():
        print("CUDA is not available to PyTorch", file=sys.stderr)
        return 3

    device_index = parse_device_index(device)
    if device_index >= torch.cuda.device_count():
        print(f"Device {device} is not visible to PyTorch", file=sys.stderr)
        return 4

    torch.cuda.set_device(device_index)
    torch_device = torch.device(device)
    target_bytes = int(memory_gb * 1024**3)

    try:
        element_size = torch.empty((), dtype=torch.float16, device=torch_device).element_size()
        reserve_elements = max(1, int(target_bytes * 0.85 / element_size))
        reserve = torch.empty(reserve_elements, dtype=torch.float16, device=torch_device)
        reserve.normal_(mean=0.0, std=0.01)

        # Matrix dimensions are bounded so the loop creates real compute pressure
        # without letting requested memory dictate impractically large GEMMs.
        dim = min(8192, max(1024, int(math.sqrt(max(reserve_elements // 16, 1)))))
        a = torch.randn((dim, dim), device=torch_device, dtype=torch.float16)
        b = torch.randn((dim, dim), device=torch_device, dtype=torch.float16)
        end_at = time.monotonic() + duration_seconds
        iterations = 0

        while time.monotonic() < end_at:
            c = torch.matmul(a, b)
            a = torch.relu(c)
            if iterations % 8 == 0:
                reserve.mul_(1.0001).add_(0.0001)
            iterations += 1
            torch.cuda.synchronize(torch_device)

        print(f"{job_id} completed {iterations} GPU iterations on {device}")
        return 0
    except RuntimeError as exc:
        print(f"{job_id} failed on {device}: {exc}", file=sys.stderr)
        return 5


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one real GPU workload.")
    parser.add_argument("--duration-seconds", type=int, required=True)
    parser.add_argument("--memory-gb", type=float, required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--job-id", required=True)
    args = parser.parse_args()
    return run_gpu_job(args.duration_seconds, args.memory_gb, args.device, args.job_id)


if __name__ == "__main__":
    raise SystemExit(main())
