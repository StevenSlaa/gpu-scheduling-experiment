from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT, load_hardware, load_scenario, load_strategy


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate GPU experiment environment.")
    parser.add_argument("--hardware", default=ROOT / "configs" / "hardware.yaml", type=Path)
    parser.add_argument("--results-root", default=ROOT / "results", type=Path)
    parser.add_argument("--require-mig", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    cuda_available, device_count, torch_description = check_torch_cuda()
    smi_device_count = count_nvidia_smi_devices()
    print(f"CUDA available: {'yes' if cuda_available else 'no'}")
    print(f"Detected devices: {device_count}")
    print(f"PyTorch: {torch_description}")
    if not cuda_available:
        failures.append(
            "PyTorch cannot use CUDA; real gpu_job.py runs will fail, but --dry-run still works. "
            "If nvidia-smi sees a GPU, install a CUDA PyTorch wheel with scripts/install_cuda_torch.ps1"
        )

    nvidia_smi_ok = shutil.which("nvidia-smi") is not None and command_ok(["nvidia-smi", "-L"])
    print(f"nvidia-smi: {'ok' if nvidia_smi_ok else 'missing/failing'}")
    print(f"nvidia-smi devices: {smi_device_count}")
    if not nvidia_smi_ok:
        failures.append("nvidia-smi is unavailable")

    hardware = load_hardware(args.hardware)
    expected_devices = len(hardware["gpus"])
    visible_devices = device_count if cuda_available else smi_device_count
    if visible_devices < expected_devices:
        failures.append(f"Hardware config expects at least {expected_devices} GPU devices, saw {visible_devices}")

    mig_enabled = bool(hardware.get("mig", {}).get("enabled"))
    expected_mig_devices = int(hardware.get("mig", {}).get("total_partitions", 0))
    mig_visible = mig_enabled and device_count >= expected_mig_devices
    print(f"MIG mode: {'enabled in config' if mig_enabled else 'disabled in config'}")
    print(f"MIG devices visible: {'yes' if mig_visible else 'n/a' if not mig_enabled else 'no'}")
    if args.require_mig and not mig_visible:
        failures.append("MIG was required but the expected MIG devices are not visible")

    args.results_root.mkdir(parents=True, exist_ok=True)
    probe = args.results_root / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print("Output directory: ok")
    except OSError as exc:
        failures.append(f"Output directory is not writable: {exc}")

    validate_configs(failures)
    if failures:
        print("Environment validation failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("Environment validation passed")
    return 0


def check_torch_cuda() -> tuple[bool, int, str]:
    try:
        import torch
    except ImportError:
        return False, 0, "not installed"
    version = getattr(torch, "__version__", "unknown")
    cuda_version = getattr(torch.version, "cuda", None)
    return bool(torch.cuda.is_available()), int(torch.cuda.device_count()), f"{version}, torch CUDA {cuda_version}"


def count_nvidia_smi_devices() -> int:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "-L"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0
    return sum(1 for line in completed.stdout.splitlines() if line.strip().startswith("GPU "))


def command_ok(command: list[str]) -> bool:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=10)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def validate_configs(failures: list[str]) -> None:
    for path in (ROOT / "configs" / "strategies").glob("*.yaml"):
        try:
            load_strategy(path)
        except Exception as exc:
            failures.append(f"Invalid strategy config {path.name}: {exc}")
    for path in (ROOT / "configs" / "scenarios").glob("*.yaml"):
        try:
            load_scenario(path)
        except Exception as exc:
            failures.append(f"Invalid scenario config {path.name}: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
