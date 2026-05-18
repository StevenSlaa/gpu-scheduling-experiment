from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT

try:
    from textual import on
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Log, Select, Static
except ImportError as exc:  # pragma: no cover - user-facing fallback
    raise SystemExit(
        "The terminal UI requires Textual. Install dependencies with: pip install -r requirements.txt"
    ) from exc


RESULTS_ROOT = ROOT / "results"


class ExperimentTui(App):
    CSS = """
    Screen {
        background: #101418;
        color: #d8dee9;
    }

    #layout {
        height: 1fr;
    }

    #controls {
        width: 36;
        min-width: 34;
        padding: 1;
        border: solid #4c566a;
        background: #151b22;
    }

    #main {
        width: 1fr;
        padding: 1;
    }

    .section-title {
        margin-top: 1;
        color: #88c0d0;
        text-style: bold;
    }

    Select, Input {
        margin-bottom: 1;
    }

    Button {
        margin-top: 1;
        width: 100%;
    }

    #status {
        height: 7;
        border: solid #4c566a;
        padding: 1;
        margin-bottom: 1;
        background: #151b22;
    }

    #gpu {
        height: 5;
        border: solid #4c566a;
        padding: 1;
        margin-bottom: 1;
        background: #151b22;
    }

    #jobs {
        height: 12;
        border: solid #4c566a;
        margin-bottom: 1;
    }

    #log {
        height: 1fr;
        border: solid #4c566a;
        background: #0b0f14;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("s", "summarize", "Summarize"),
        ("c", "cleanup", "Cleanup"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.process: asyncio.subprocess.Process | None = None
        self.active_result_dir: Path | None = None
        self.last_event_count = 0
        self.run_started_at = 0.0

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="layout"):
            with Vertical(id="controls"):
                yield Label("Configuration", classes="section-title")
                yield Select(options=self.config_options("hardware"), id="hardware", prompt="Hardware")
                yield Select(options=self.config_options("strategies"), id="strategy", prompt="Strategy")
                yield Select(options=self.config_options("scenarios"), id="scenario", prompt="Scenario")
                yield Label("Run", classes="section-title")
                yield Input(value="1", placeholder="Run index", id="run-index")
                yield Input(value="1.0", placeholder="Time scale", id="time-scale")
                yield Input(value="1.0", placeholder="Metrics interval seconds", id="metrics-interval")
                yield Checkbox("Dry run", id="dry-run")
                yield Button("Run Experiment", id="run", variant="success")
                yield Button("Cleanup Jobs", id="cleanup", variant="warning")
                yield Button("Validate Environment", id="validate", variant="primary")
                yield Button("Summarize Results", id="summarize", variant="primary")
                yield Button("Refresh Results", id="refresh")
            with Vertical(id="main"):
                yield Static("No active run.", id="status")
                yield Static("GPU metrics unavailable.", id="gpu")
                yield DataTable(id="jobs")
                yield Log(id="log", highlight=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "GPU Experiment Toolkit"
        self.sub_title = "run, monitor, summarize"
        self.init_tables()
        self.set_default_selects()
        self.set_interval(1.0, self.refresh_live_view)
        self.query_one(Log).write_line("Ready. Choose configs and start an experiment.")

    def init_tables(self) -> None:
        table = self.query_one("#jobs", DataTable)
        table.cursor_type = "row"
        table.add_columns("Job", "User", "Type", "Mem", "Wait", "Runtime", "Status", "Device")

    def config_options(self, kind: str) -> list[tuple[str, str]]:
        if kind == "hardware":
            paths = sorted((ROOT / "configs").glob("hardware*.yaml"))
        else:
            paths = sorted((ROOT / "configs" / kind).glob("*.yaml"))
        return [(path.stem, path.relative_to(ROOT).as_posix()) for path in paths]

    def set_default_selects(self) -> None:
        self.set_select_value("hardware", "configs/hardware_a2000_12gb.yaml", fallback="configs/hardware.yaml")
        self.set_select_value("strategy", "configs/strategies/queue_single_gpu.yaml", fallback="configs/strategies/queue.yaml")
        self.set_select_value("scenario", "configs/scenarios/local_a2000_smoke.yaml", fallback="configs/scenarios/peak_8_users.yaml")

    def set_select_value(self, widget_id: str, preferred: str, fallback: str) -> None:
        select = self.query_one(f"#{widget_id}", Select)
        config_kind = {
            "hardware": "hardware",
            "strategy": "strategies",
            "scenario": "scenarios",
        }[widget_id]
        options = {value for _, value in self.config_options(config_kind)}
        if preferred in options:
            select.value = preferred
        elif fallback in options:
            select.value = fallback
        elif options:
            select.value = sorted(options)[0]

    @on(Button.Pressed, "#run")
    async def run_experiment(self) -> None:
        if self.process and self.process.returncode is None:
            self.query_one(Log).write_line("A run is already active.")
            return

        await self.cleanup_jobs(include_runner=True)
        command = [
            sys.executable,
            str(ROOT / "scripts" / "run_single_experiment.py"),
            "--hardware",
            self.selected_path("hardware"),
            "--strategy",
            self.selected_path("strategy"),
            "--scenario",
            self.selected_path("scenario"),
            "--run-index",
            self.input_value("run-index", "1"),
            "--time-scale",
            self.input_value("time-scale", "1.0"),
            "--metrics-interval-seconds",
            self.input_value("metrics-interval", "1.0"),
        ]
        if self.query_one("#dry-run", Checkbox).value:
            command.append("--dry-run")

        self.last_event_count = 0
        self.active_result_dir = None
        self.run_started_at = time.time()
        self.query_one(Log).clear()
        self.query_one(Log).write_line("Starting: " + " ".join(command))
        self.process = await asyncio.create_subprocess_exec(
            *command,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.set_timer(0.2, self.find_active_result_dir)
        asyncio.create_task(self.stream_process("stdout", self.process.stdout))
        asyncio.create_task(self.stream_process("stderr", self.process.stderr))
        asyncio.create_task(self.wait_for_process())

    @on(Button.Pressed, "#validate")
    async def validate_environment(self) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "validate_environment.py"),
            "--hardware",
            self.selected_path("hardware"),
        ]
        await self.run_short_command(command)

    @on(Button.Pressed, "#cleanup")
    async def cleanup_jobs_button(self) -> None:
        await self.cleanup_jobs(include_runner=True)

    @on(Button.Pressed, "#summarize")
    async def summarize_results_button(self) -> None:
        await self.action_summarize()

    @on(Button.Pressed, "#refresh")
    def refresh_results_button(self) -> None:
        self.action_refresh()

    async def action_summarize(self) -> None:
        await self.run_short_command([sys.executable, str(ROOT / "scripts" / "summarize_results.py")])
        self.load_latest_completed_run()

    def action_refresh(self) -> None:
        self.load_latest_completed_run()
        self.refresh_live_view()

    async def action_cleanup(self) -> None:
        await self.cleanup_jobs(include_runner=True)

    async def action_quit(self) -> None:
        await self.cleanup_jobs(include_runner=True)
        self.exit()

    async def cleanup_jobs(self, include_runner: bool) -> None:
        if self.process and self.process.returncode is None:
            self.query_one(Log).write_line("Stopping active experiment runner.")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        command = [sys.executable, str(ROOT / "scripts" / "cleanup_jobs.py")]
        if include_runner:
            command.append("--include-runner")
        await self.run_short_command(command)

    async def run_short_command(self, command: list[str]) -> None:
        self.query_one(Log).write_line("$ " + " ".join(command))
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        for line in stdout.decode(errors="replace").splitlines():
            self.query_one(Log).write_line(line)
        for line in stderr.decode(errors="replace").splitlines():
            self.query_one(Log).write_line("[stderr] " + line)
        self.query_one(Log).write_line(f"Exit code: {process.returncode}")

    async def stream_process(self, name: str, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while line := await stream.readline():
            text = line.decode(errors="replace").rstrip()
            if text:
                self.query_one(Log).write_line(f"[{name}] {text}")
                maybe_path = Path(text)
                if maybe_path.exists() and maybe_path.is_dir():
                    self.active_result_dir = maybe_path

    async def wait_for_process(self) -> None:
        if not self.process:
            return
        return_code = await self.process.wait()
        self.query_one(Log).write_line(f"Run finished with exit code {return_code}")
        self.find_active_result_dir()
        self.refresh_live_view()

    def find_active_result_dir(self) -> None:
        if self.active_result_dir and self.active_result_dir.exists():
            return
        candidates = latest_result_dirs(min_mtime=self.run_started_at)
        if candidates:
            self.active_result_dir = candidates[0]

    def refresh_live_view(self) -> None:
        if not self.active_result_dir:
            self.query_one("#status", Static).update("No active run.")
            return
        self.update_status()
        self.append_new_events()
        self.update_jobs_table()

    def update_status(self) -> None:
        result_dir = self.active_result_dir
        if not result_dir:
            return
        metadata = read_json(result_dir / "metadata.json")
        summary = read_json(result_dir / "summary.json")
        queue_tail = last_csv_row(result_dir / "queue_depth.csv")
        gpu_tail = last_csv_row(result_dir / "gpu_metrics.csv")
        job_rows = read_csv(result_dir / "jobs.csv")
        live_counts = count_job_statuses(job_rows)
        completed_jobs = summary.get("completed_jobs", live_counts["completed"])
        failed_jobs = summary.get("failed_jobs", live_counts["failed"])
        total_jobs = summary.get("total_jobs", len(job_rows) or "?")
        status_lines = [
            f"Result: {result_dir.name}",
            f"Strategy: {metadata.get('strategy', '?')}   Scenario: {metadata.get('scenario', '?')}   Run: {metadata.get('run_index', '?')}",
            f"Started: {metadata.get('started_at', '?')}",
            f"Ended: {metadata.get('ended_at') or 'running'}",
            f"Jobs: {completed_jobs}/{total_jobs} completed   Running: {live_counts['running']}   Queued: {live_counts['queued']}   Failed: {failed_jobs}",
            f"Wait median/p95: {summary.get('median_wait', '?')} / {summary.get('p95_wait', '?')}",
            f"Queue depth: {queue_tail.get('queue_depth', '?')}   Running: {queue_tail.get('running_jobs', '?')}",
        ]
        self.query_one("#status", Static).update("\n".join(status_lines))
        self.update_gpu_panel(gpu_tail)

    def update_gpu_panel(self, gpu_tail: dict[str, str]) -> None:
        util = parse_float(gpu_tail.get("gpu_util_percent"))
        memory_used = parse_float(gpu_tail.get("memory_used_mb"))
        memory_total = parse_float(gpu_tail.get("memory_total_mb"))
        power = gpu_tail.get("power_watts", "?")
        temp = gpu_tail.get("temperature_c", "?")
        memory_percent = (memory_used / memory_total * 100) if memory_used is not None and memory_total else None
        lines = [
            f"GPU util  {percent_bar(util)} {format_percent(util)}",
            f"VRAM      {percent_bar(memory_percent)} {format_memory(memory_used, memory_total)}",
            f"Power {power} W   Temp {temp} C   Device {gpu_tail.get('device', '?')}",
        ]
        self.query_one("#gpu", Static).update("\n".join(lines))

    def append_new_events(self) -> None:
        if not self.active_result_dir:
            return
        events_path = self.active_result_dir / "events.jsonl"
        if not events_path.exists():
            return
        lines = events_path.read_text(encoding="utf-8").splitlines()
        for line in lines[self.last_event_count :]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.query_one(Log).write_line(line)
                continue
            details = " ".join(f"{key}={value}" for key, value in event.items() if key not in {"time", "event"})
            self.query_one(Log).write_line(f"{event.get('time')} {event.get('event')} {details}")
        self.last_event_count = len(lines)

    def update_jobs_table(self) -> None:
        if not self.active_result_dir:
            return
        jobs_path = self.active_result_dir / "jobs.csv"
        if not jobs_path.exists():
            return
        rows = read_csv(jobs_path)
        table = self.query_one("#jobs", DataTable)
        table.clear()
        for row in rows:
            table.add_row(
                row["job_id"],
                row["user_id"],
                row["job_type"],
                row["requested_memory_gb"],
                row["wait_time_seconds"],
                row["runtime_seconds"],
                row["status"],
                row["assigned_device"],
            )

    def load_latest_completed_run(self) -> None:
        candidates = latest_result_dirs()
        if candidates:
            self.active_result_dir = candidates[0]
            self.last_event_count = 0
            self.refresh_live_view()

    def selected_path(self, widget_id: str) -> str:
        value = self.query_one(f"#{widget_id}", Select).value
        return str(ROOT / str(value))

    def input_value(self, widget_id: str, default: str) -> str:
        value = self.query_one(f"#{widget_id}", Input).value.strip()
        return value or default


def latest_result_dirs(min_mtime: float = 0.0) -> list[Path]:
    if not RESULTS_ROOT.exists():
        return []
    return sorted(
        [
            path
            for path in RESULTS_ROOT.iterdir()
            if path.is_dir() and (path / "metadata.json").exists() and path.stat().st_mtime >= min_mtime
        ],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def last_csv_row(path: Path) -> dict[str, str]:
    rows = read_csv(path)
    return rows[-1] if rows else {}


def count_job_statuses(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"completed": 0, "failed": 0, "running": 0, "queued": 0}
    for row in rows:
        status = row.get("status", "")
        if status == "completed":
            counts["completed"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "running":
            counts["running"] += 1
        elif status in {"scheduled", "submitted"}:
            counts["queued"] += 1
    return counts


def parse_float(value: str | None) -> float | None:
    if value in (None, "", "unavailable", "?"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def percent_bar(value: float | None, width: int = 30) -> str:
    if value is None:
        return "[" + ("." * width) + "]"
    clamped = max(0.0, min(100.0, value))
    filled = int(round(width * clamped / 100))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:5.1f}%"


def format_memory(used: float | None, total: float | None) -> str:
    if used is None or total is None:
        return "n/a"
    return f"{used:.0f}/{total:.0f} MB"


if __name__ == "__main__":
    ExperimentTui().run()
