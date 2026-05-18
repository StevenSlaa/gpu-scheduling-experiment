# GPU Workload Experiment Toolkit

Toolkit for reproducible GPU allocation experiments across queue-based, reservation-based, and MIG/partitioned strategies. The runner generates one deterministic workload per scenario seed and reuses that job list across strategies, so allocation behavior is compared against the same demand profile.

## Structure

```text
configs/                 Hardware, strategy, and scenario YAML files
workloads/gpu_job.py     One real PyTorch CUDA workload
src/                     Runner, schedulers, generator, metrics, config code
scripts/                 CLI entrypoints
results/                 Experiment outputs
```

Each run writes:

- `metadata.json`
- `jobs.csv`
- `gpu_metrics.csv`
- `queue_depth.csv`
- `events.jsonl`
- `summary.json`
- `config_snapshot/`

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Install a PyTorch build that matches the host CUDA driver if the default wheel is not suitable for the machine.

On Windows, if validation says `PyTorch: ...+cpu` or `torch CUDA None` while `nvidia-smi` sees your GPU, replace the CPU-only wheel in the project venv:

```powershell
.\scripts\install_cuda_torch.ps1
```

The helper installs PyTorch from the official CUDA wheel index. The default is `cu126`, because PyTorch wheels bundle their own CUDA runtime and only require a compatible NVIDIA driver. This is usually more stable on an RTX A2000 than the newer CUDA 13.2 wheel.

```powershell
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu126
```

To explicitly try CUDA 13.2 wheels:

```powershell
.\scripts\install_cuda_torch.ps1 -IndexUrl https://download.pytorch.org/whl/cu132
```

## Validate Environment

```bash
python scripts/validate_environment.py
```

The validation checks PyTorch CUDA visibility, `nvidia-smi`, expected devices, output directory writability, and config parsing.

For a local single-GPU RTX A2000 12GB machine, validate against the local profile:

```bash
python scripts/validate_environment.py --hardware configs/hardware_a2000_12gb.yaml
```

## Run One Experiment

```bash
python scripts/run_single_experiment.py --strategy queue --scenario peak_16_users --run-index 1
```

On a single 12GB GPU, use the smaller local scenario and single-GPU queue config:

```bash
python scripts/run_single_experiment.py --strategy configs/strategies/queue_single_gpu.yaml --scenario local_a2000_smoke --hardware configs/hardware_a2000_12gb.yaml
```

## Terminal UI

Start the interactive terminal application:

```bash
python scripts/tui.py
```

The UI lets you select hardware, strategy, and scenario configs, run validation, start experiments, watch live events and queue/GPU status, inspect finished job rows, and regenerate summary CSVs. It defaults to the local A2000-safe profile when those configs are present.

The UI also runs a cleanup before each new experiment and when quitting. Cleanup terminates toolkit-managed `gpu_job.py` and experiment runner processes so stale GPU jobs do not keep running after the interface closes. You can trigger it manually with the `Cleanup Jobs` button or the `c` shortcut.

Manual cleanup from a shell:

```bash
python scripts/cleanup_jobs.py --include-runner
```

For orchestration-only checks that do not allocate GPU memory:

```bash
python scripts/run_single_experiment.py --strategy queue --scenario peak_8_users --dry-run --time-scale 0.001
```

`--time-scale` scales submit offsets and durations. Leave it at the default `1.0` for real measurements.

## Run Matrices

Pilot:

```bash
python scripts/run_full_matrix.py --matrix pilot
```

Main matrix:

```bash
python scripts/run_full_matrix.py --matrix main
```

The default main matrix is:

```text
3 strategies x 2 scenarios x 3 repetitions = 18 runs
```

## Summarize Results

```bash
python scripts/summarize_results.py
```

This writes:

- `results/summary_per_run.csv`
- `results/summary_per_strategy.csv`
- `results/summary_per_scenario.csv`

## Notes

- `workloads/gpu_job.py` performs repeated CUDA matrix multiplications and reserves the requested memory footprint with PyTorch tensors. It is intentionally not a sleep-based workload.
- The queue scheduler uses exclusive full-GPU FIFO slots.
- The reservation scheduler maps `lab` jobs to the reserved pool and all other jobs to the general pool.
- The MIG scheduler treats visible CUDA devices as partitions and rejects jobs whose memory request exceeds `partition_memory_gb`.
