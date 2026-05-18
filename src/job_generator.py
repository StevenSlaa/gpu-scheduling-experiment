from __future__ import annotations

import csv
import random
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    user_id: str
    job_type: str
    submit_offset_seconds: int
    duration_seconds: int
    memory_gb: int
    group: str


def generate_jobs(scenario: dict) -> list[JobSpec]:
    rng = random.Random(scenario["random_seed"])
    users = int(scenario["users"])
    window = int(scenario["submission_window_seconds"])
    short_ratio = float(scenario["job_mix"]["short_ratio"])
    jobs: list[JobSpec] = []

    for index in range(users):
        job_type = "short" if rng.random() < short_ratio else "long"
        profile = scenario[f"{job_type}_jobs"]
        user_id = f"user_{index + 1:02d}"
        group = "lab" if index % 4 == 0 else "general"
        jobs.append(
            JobSpec(
                job_id=f"job_{index + 1:03d}",
                user_id=user_id,
                job_type=job_type,
                submit_offset_seconds=rng.randint(0, window),
                duration_seconds=rng.randint(
                    int(profile["duration_seconds_min"]),
                    int(profile["duration_seconds_max"]),
                ),
                memory_gb=rng.randint(
                    int(profile["memory_gb_min"]),
                    int(profile["memory_gb_max"]),
                ),
                group=group,
            )
        )

    return sorted(jobs, key=lambda job: (job.submit_offset_seconds, job.job_id))


def write_jobs_csv(jobs: list[JobSpec], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(jobs[0]).keys()))
        writer.writeheader()
        for job in jobs:
            writer.writerow(asdict(job))
