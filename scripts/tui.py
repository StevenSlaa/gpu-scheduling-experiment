from __future__ import annotations

import asyncio
import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config_loader import ROOT
from src.experiment_runner import T_WAIT_SECONDS

try:
    from rich.text import Text
    from textual import on
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, ScrollableContainer, Vertical
    from textual.widgets import (
        Button, DataTable, Footer, Header, Input, Label,
        RichLog, Rule, Select, Static, Switch,
    )
except ImportError as exc:
    raise SystemExit(
        "Terminal UI requires Textual ≥ 0.86.  Run: pip install -r requirements.txt"
    ) from exc


RESULTS_ROOT = ROOT / "results"

# ── colour tokens used in Rich markup ──────────────────────────────────────────
_OK   = "green3"
_WARN = "yellow3"
_ERR  = "red3"
_DIM  = "grey50"
_ACC  = "dodger_blue2"
_MAG  = "magenta"

STATUS_ICONS: dict[str, tuple[str, str]] = {
    "completed": ("✓", _OK),
    "failed":    ("✗", _ERR),
    "rejected":  ("⊘", _MAG),
    "running":   ("●", _WARN),
    "submitted": ("→", "cyan"),
    "scheduled": ("○", _DIM),
}

EVENT_COLORS: dict[str, str] = {
    "job_submitted":       "cyan",
    "job_started":         _OK,
    "job_finished":        _ACC,
    "job_failed_to_start": _ERR,
    "job_rejected":        _MAG,
    "job_cancelled":       "orange3",
}


class ExperimentTui(App):
    CSS = """
    Screen {
        background: #0d1117;
        color: #c9d1d9;
    }

    /* ── outer frame ─────────────────────────────────── */
    #layout { height: 1fr; }

    /* ── controls sidebar ────────────────────────────── */
    #controls {
        width: 38;
        min-width: 36;
        background: #0d1117;
        border-right: solid #21262d;
        padding: 0 1;
    }

    #ctrl-scroll { height: 1fr; }

    .ctrl-title {
        text-style: bold;
        color: #58a6ff;
        margin-top: 1;
    }

    Select { margin-bottom: 1; }

    /* labelled input rows */
    .opt-row   { height: 3; margin-bottom: 0; }
    .opt-lbl   { width: 14; padding: 1 1 0 0; color: #8b949e; }
    .opt-inp   { width: 1fr; }

    /* dry-run row reuses opt-row */
    .dr-lbl    { padding: 1 1 0 0; color: #8b949e; }

    Button { width: 100%; margin-bottom: 1; }

    /* ── top panels ──────────────────────────────────── */
    #top-panels { height: 11; }

    #status {
        width: 1fr;
        height: 1fr;
        border: solid #21262d;
        padding: 0 1;
        background: #0d1117;
    }

    #gpu {
        width: 52;
        height: 1fr;
        border: solid #21262d;
        padding: 0 1;
        background: #0d1117;
    }

    /* ── jobs table ──────────────────────────────────── */
    #jobs {
        height: 14;
        border: solid #21262d;
    }

    DataTable > .datatable--header {
        background: #161b22;
        color: #8b949e;
        text-style: bold;
    }

    DataTable > .datatable--cursor { background: #1f6feb; }

    /* ── event log ───────────────────────────────────── */
    #log {
        height: 1fr;
        border: solid #21262d;
        background: #0d1117;
    }
    """

    BINDINGS = [
        ("q", "quit",      "Quit"),
        ("r", "refresh",   "Refresh"),
        ("s", "summarize", "Summarize"),
        ("a", "analyse",   "Analyse"),
        ("c", "cleanup",   "Cleanup"),
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
                with ScrollableContainer(id="ctrl-scroll"):
                    yield Label("HARDWARE", classes="ctrl-title")
                    yield Select(options=self.config_options("hardware"),   id="hardware", prompt="Select hardware")
                    yield Label("STRATEGY", classes="ctrl-title")
                    yield Select(options=self.config_options("strategies"), id="strategy", prompt="Select strategy")
                    yield Label("SCENARIO", classes="ctrl-title")
                    yield Select(options=self.config_options("scenarios"),  id="scenario", prompt="Select scenario")
                    yield Rule()
                    yield Label("RUN OPTIONS", classes="ctrl-title")
                    with Horizontal(classes="opt-row"):
                        yield Label("Run index",   classes="opt-lbl")
                        yield Input(value="1",   id="run-index",       classes="opt-inp")
                    with Horizontal(classes="opt-row"):
                        yield Label("Time scale",  classes="opt-lbl")
                        yield Input(value="1.0", id="time-scale",      classes="opt-inp")
                    with Horizontal(classes="opt-row"):
                        yield Label("Interval (s)", classes="opt-lbl")
                        yield Input(value="1.0", id="metrics-interval", classes="opt-inp")
                    with Horizontal(classes="opt-row"):
                        yield Label("Dry run",     classes="dr-lbl")
                        yield Switch(value=False, id="dry-run")
                    yield Rule()
                    yield Button("▶  Run Experiment",  id="run",      variant="success")
                    yield Button("   Validate Env",    id="validate", variant="primary")
                    yield Button("   Summarize",       id="summarize", variant="primary")
                    yield Button("   Analyse Results", id="analyse",  variant="primary")
                    yield Button("   Refresh View",    id="refresh")
                    yield Button("   Cleanup Jobs",    id="cleanup",  variant="warning")
            with Vertical(id="main"):
                with Horizontal(id="top-panels"):
                    yield Static(id="status", markup=True)
                    yield Static(id="gpu",    markup=True)
                yield DataTable(id="jobs")
                yield RichLog(id="log", highlight=True, markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "GPU Scheduling Experiment"
        self.sub_title = "schedule · monitor · analyse"
        self._init_table()
        self._set_defaults()
        self.set_interval(1.0, self._tick)
        self._show_idle()
        self.query_one("#log", RichLog).write(
            f"[{_DIM}]Ready.[/]  Configure a run and press [bold]▶ Run Experiment[/bold], "
            f"or [bold]r[/bold] to load the latest result."
        )

    # ── table setup ─────────────────────────────────────────────────────────────

    def _init_table(self) -> None:
        t = self.query_one("#jobs", DataTable)
        t.cursor_type = "row"
        t.add_columns("Job", "User", "Grp", "Type", "Mem GB", "Wait s", "Runtime s", "Status", "Device")

    # ── config helpers ───────────────────────────────────────────────────────────

    def config_options(self, kind: str) -> list[tuple[str, str]]:
        if kind == "hardware":
            paths = sorted((ROOT / "configs").glob("hardware*.yaml"))
        else:
            paths = sorted((ROOT / "configs" / kind).glob("*.yaml"))
        return [(p.stem, p.relative_to(ROOT).as_posix()) for p in paths]

    def _set_defaults(self) -> None:
        self._pick("hardware", "configs/hardware_a2000_12gb.yaml",      "configs/hardware.yaml")
        self._pick("strategy", "configs/strategies/queue_single_gpu.yaml", "configs/strategies/queue.yaml")
        self._pick("scenario", "configs/scenarios/local_a2000_smoke.yaml", "configs/scenarios/peak_8_users.yaml")

    def _pick(self, wid: str, preferred: str, fallback: str) -> None:
        sel = self.query_one(f"#{wid}", Select)
        kind = {"hardware": "hardware", "strategy": "strategies", "scenario": "scenarios"}[wid]
        opts = {v for _, v in self.config_options(kind)}
        for choice in (preferred, fallback):
            if choice in opts:
                sel.value = choice
                return
        if opts:
            sel.value = sorted(opts)[0]

    # ── button handlers ──────────────────────────────────────────────────────────

    @on(Button.Pressed, "#run")
    async def _btn_run(self) -> None:
        if self.process and self.process.returncode is None:
            self.query_one("#log", RichLog).write(f"[{_WARN}]A run is already active.[/]")
            return
        await self._do_cleanup(include_runner=True)
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "run_single_experiment.py"),
            "--hardware",                  self._path("hardware"),
            "--strategy",                  self._path("strategy"),
            "--scenario",                  self._path("scenario"),
            "--run-index",                 self._val("run-index", "1"),
            "--time-scale",                self._val("time-scale", "1.0"),
            "--metrics-interval-seconds",  self._val("metrics-interval", "1.0"),
        ]
        if self.query_one("#dry-run", Switch).value:
            cmd.append("--dry-run")
        self.last_event_count = 0
        self.active_result_dir = None
        self.run_started_at = time.time()
        log = self.query_one("#log", RichLog)
        log.clear()
        log.write(f"[{_DIM}]$ {' '.join(cmd)}[/]")
        self.process = await asyncio.create_subprocess_exec(
            *cmd, cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.set_timer(0.3, self._find_run_dir)
        asyncio.create_task(self._stream("stdout", self.process.stdout))
        asyncio.create_task(self._stream("stderr", self.process.stderr))
        asyncio.create_task(self._await_process())

    @on(Button.Pressed, "#validate")
    async def _btn_validate(self) -> None:
        await self._short([sys.executable, str(ROOT / "scripts" / "validate_environment.py"),
                           "--hardware", self._path("hardware")])

    @on(Button.Pressed, "#cleanup")
    async def _btn_cleanup(self) -> None:
        await self._do_cleanup(include_runner=True)

    @on(Button.Pressed, "#summarize")
    async def _btn_summarize(self) -> None:
        await self.action_summarize()

    @on(Button.Pressed, "#analyse")
    async def _btn_analyse(self) -> None:
        await self.action_analyse()

    @on(Button.Pressed, "#refresh")
    def _btn_refresh(self) -> None:
        self.action_refresh()

    async def action_summarize(self) -> None:
        await self._short([sys.executable, str(ROOT / "scripts" / "summarize_results.py")])
        self._load_latest()

    async def action_analyse(self) -> None:
        await self._short([sys.executable, str(ROOT / "scripts" / "analyse_results.py")])

    def action_refresh(self) -> None:
        self._load_latest()
        self._tick()

    async def action_cleanup(self) -> None:
        await self._do_cleanup(include_runner=True)

    async def action_quit(self) -> None:
        await self._do_cleanup(include_runner=True)
        self.exit()

    # ── subprocess helpers ───────────────────────────────────────────────────────

    async def _do_cleanup(self, include_runner: bool) -> None:
        if self.process and self.process.returncode is None:
            self.query_one("#log", RichLog).write(f"[{_WARN}]Stopping active runner…[/]")
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        cmd = [sys.executable, str(ROOT / "scripts" / "cleanup_jobs.py")]
        if include_runner:
            cmd.append("--include-runner")
        await self._short(cmd)

    async def _short(self, cmd: list[str]) -> None:
        log = self.query_one("#log", RichLog)
        log.write(f"[{_DIM}]$ {' '.join(cmd)}[/]")
        p = await asyncio.create_subprocess_exec(
            *cmd, cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await p.communicate()
        for line in out.decode(errors="replace").splitlines():
            log.write(line)
        for line in err.decode(errors="replace").splitlines():
            log.write(f"[{_ERR}]{line}[/]")
        rc_col = _OK if p.returncode == 0 else _ERR
        log.write(f"[{rc_col}]exit {p.returncode}[/]")

    async def _stream(self, name: str, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while line := await stream.readline():
            text = line.decode(errors="replace").rstrip()
            if not text:
                continue
            markup = f"[{_ERR}]{text}[/]" if name == "stderr" else text
            self.query_one("#log", RichLog).write(markup)
            maybe = Path(text)
            if maybe.exists() and maybe.is_dir():
                self.active_result_dir = maybe

    async def _await_process(self) -> None:
        if not self.process:
            return
        rc = await self.process.wait()
        col = _OK if rc == 0 else _ERR
        self.query_one("#log", RichLog).write(f"[{col}]Run finished — exit {rc}[/]")
        self._find_run_dir()
        self._tick()

    def _find_run_dir(self) -> None:
        if self.active_result_dir and self.active_result_dir.exists():
            return
        cands = _latest_dirs(min_mtime=self.run_started_at)
        if cands:
            self.active_result_dir = cands[0]

    # ── live view ────────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if not self.active_result_dir:
            self._show_idle()
            return
        self._update_status()
        self._update_events()
        self._update_jobs_table()

    def _show_idle(self) -> None:
        hw = self._sel_stem("hardware")
        st = self._sel_stem("strategy")
        sc = self._sel_stem("scenario")
        ri = self._val("run-index", "1")
        ts = self._val("time-scale", "1.0")
        iv = self._val("metrics-interval", "1.0")
        dr = "yes" if self.query_one("#dry-run", Switch).value else "no"
        self.query_one("#status", Static).update(
            f"\n [{_DIM}]No active experiment — ready to run.[/]\n\n"
            f"  [dim]Hardware[/]   [bold]{hw}[/]\n"
            f"  [dim]Strategy[/]   [bold]{st}[/]\n"
            f"  [dim]Scenario[/]   [bold]{sc}[/]\n\n"
            f"  [dim]Run index[/]  {ri}   "
            f"[dim]Time scale[/]  {ts}   "
            f"[dim]Interval[/]  {iv} s   "
            f"[dim]Dry run[/]  [{_WARN if dr == 'yes' else _DIM}]{dr}[/]\n\n"
            f"  Press [bold]▶ Run Experiment[/bold] to start,"
            f" or [bold]r[/bold] to load a previous result."
        )
        self.query_one("#gpu", Static).update(f"\n [{_DIM}]GPU metrics unavailable.[/]")

    def _update_status(self) -> None:
        d = self.active_result_dir
        if not d:
            return
        meta  = _rjson(d / "metadata.json")
        summ  = _rjson(d / "summary.json")
        jrows = _rcsv(d / "jobs.csv")
        gpus  = _last_gpu_per_device(d / "gpu_metrics.csv")

        counts = _count(jrows)
        total  = int(summ.get("total_jobs",  0) or 0) or len(jrows)
        done   = int(summ.get("completed_jobs", counts["completed"]))
        failed = int(summ.get("failed_jobs",    counts["failed"]))
        reject = int(summ.get("rejected_jobs",  counts["rejected"]))

        strategy = meta.get("strategy", "?")
        scenario = meta.get("scenario", "?")
        run_idx  = meta.get("run_index", "?")
        started  = meta.get("started_at", "")
        ended    = meta.get("ended_at")
        is_live  = bool(self.process and self.process.returncode is None)

        state_tag = (f"[{_WARN}]● RUNNING[/]" if is_live
                     else f"[{_OK}]✓ DONE[/]"  if ended
                     else f"[{_DIM}]○ idle[/]")

        # ── three-colour progress bar: green=done  yellow=running  grey=waiting ──
        N = 22
        done_f = int(N * done / total) if total else 0
        run_f  = min(int(N * counts["running"] / total) if total else 0, N - done_f)
        prog_bar = (f"[{_OK}]{'█' * done_f}[/]"
                    f"[{_WARN}]{'▓' * run_f}[/]"
                    f"[{_DIM}]{'░' * (N - done_f - run_f)}[/]")
        pct = int((done + counts["running"]) / total * 100) if total else 0

        # ── ETA / end-time ──────────────────────────────────────────────────────
        esecs = _elapsed_secs(started)
        if is_live and done > 0 and total > done:
            rem = max(0, int(esecs * (total - done) / done))
            h, r = divmod(rem, 3600)
            m, s = divmod(r, 60)
            try:
                s_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                eta_clock = (s_dt + timedelta(seconds=esecs + rem)).strftime("%H:%M:%S")
            except Exception:
                eta_clock = "?"
            timing_line = (f"  [{_DIM}]ETA[/]   [{_WARN}]~{h:02d}:{m:02d}:{s:02d} remaining[/]"
                           f"  [dim]·[/]  done ≈ [bold]{eta_clock}[/]")
        elif is_live:
            timing_line = f"  [{_DIM}]ETA[/]   [{_DIM}]calculating…[/]"
        else:
            timing_line = f"  [{_DIM}]Ended[/]  [bold]{_ts(ended)}[/]" if ended else ""

        # ── currently running jobs ───────────────────────────────────────────────
        running_rows = [r for r in jrows if r.get("status") == "running"]
        if running_rows:
            parts = [
                f"[bold]{r['job_id']}[/] [{_DIM}]{r['job_type']} {r['requested_memory_gb']}GB[/]"
                f" [dim]→[/] [{_ACC}]{r.get('assigned_device', '?')}[/]"
                for r in running_rows[:3]
            ]
            extra = f" [{_DIM}]+{len(running_rows)-3} more[/]" if len(running_rows) > 3 else ""
            lbl   = "Now" if len(running_rows) == 1 else f"Now ×{len(running_rows)}"
            now_line = f"  [{_DIM}]{lbl}[/]  {'  [dim]·[/]  '.join(parts)}{extra}"
        elif done == total and total > 0:
            now_line = f"  [{_OK}]All {total} jobs finished.[/]"
        else:
            now_line = ""

        # ── wait stats ───────────────────────────────────────────────────────────
        med_w  = summ.get("median_wait")
        p95_w  = summ.get("p95_wait")
        t_exc  = summ.get("t_wait_exceeded_count")
        t_pct  = summ.get("t_wait_exceeded_pct")
        t_adeq = summ.get("t_wait_adequate")
        wait_lines: list[str] = []
        if med_w is not None:
            mf = float(med_w)
            pf = float(p95_w) if p95_w is not None else None
            mc = _OK if mf < T_WAIT_SECONDS else _ERR
            pc = _OK if (pf or 0) < T_WAIT_SECONDS else _WARN
            pstr = f"{pf:.1f}" if pf is not None else "─"
            wait_lines.append(
                f"  [{_DIM}]Wait[/]  median [{mc}]{mf:.1f} s[/]  [dim]·[/]  p95 [{pc}]{pstr} s[/]"
            )
            if t_exc is not None:
                ec   = _OK if t_exc == 0 else _WARN if t_exc < 3 else _ERR
                adeq = f"[{_OK}]✓ within T_wait[/]" if t_adeq else f"[{_ERR}]⚠ exceeds {T_WAIT_SECONDS} s[/]"
                wait_lines.append(
                    f"        T_wait exceeded  [{ec}]{t_exc} ({t_pct:.1f}%)[/]  {adeq}"
                )

        lines = [
            f" [{_ACC}]◆ {strategy}[/]  [dim]›[/]  [bold]{scenario}[/]  [dim]›[/]  run {run_idx}",
            f"  [{_DIM}]Started[/]  {_ts(started)}   [{_DIM}]Elapsed[/]  [bold]{_elapsed(started)}[/]   {state_tag}",
            timing_line,
            "",
            f"  {prog_bar}  [bold]{done}[/][dim]/{total}[/]  [{_DIM}]{pct}%[/]   "
            f"[{_OK}]✓{done}[/] [{_WARN}]●{counts['running']}[/] "
            f"[{_ERR}]✗{failed}[/] [{_MAG}]⊘{reject}[/] [{_DIM}]○{counts['queued']}[/]",
            now_line,
            *wait_lines,
        ]

        self.query_one("#status", Static).update("\n".join(lines))
        self._update_gpu(gpus)

    def _update_gpu(self, gpus: dict[str, dict[str, str]]) -> None:
        if not gpus:
            self.query_one("#gpu", Static).update(f"\n [{_DIM}]No GPU data yet.[/]")
            return
        lines: list[str] = [f" [{_DIM}]GPU Metrics[/]", ""]
        total_pwr  = 0.0
        active_cnt = 0
        for device in sorted(gpus):
            row    = gpus[device]
            util   = _pf(row.get("gpu_util_percent"))
            mem_u  = _pf(row.get("memory_used_mb"))
            mem_t  = _pf(row.get("memory_total_mb"))
            pwr_f  = _pf(row.get("power_watts"))
            temp_f = _pf(row.get("temperature_c"))
            temp_c = (_ERR if (temp_f or 0) > 80 else _WARN if (temp_f or 0) > 65 else _OK)
            mem_pct = (mem_u / mem_t * 100) if mem_u and mem_t else None
            gu = f"{mem_u/1024:.0f}" if mem_u else "?"
            gt = f"{mem_t/1024:.0f}" if mem_t else "?"
            pwr_s = f"{pwr_f:.0f}W" if pwr_f else "?W"
            temp_s = f"{temp_f:.0f}°" if temp_f else "?°"
            if pwr_f:
                total_pwr += pwr_f
            if (util or 0) > 1:
                active_cnt += 1
            # one compact line per GPU
            lines.append(
                f" [{_ACC}]{device}[/]  [{temp_c}]{temp_s}[/]  [{_DIM}]{pwr_s}[/]  "
                f"{_bar(util, 9)} [{_uc(util)}]{util:.0f}%[/]  "
                f"{_bar(mem_pct, 9)} [{_DIM}]{gu}/{gt}GB[/]"
                if util is not None else
                f" [{_ACC}]{device}[/]  [{temp_c}]{temp_s}[/]  [{_DIM}]{pwr_s}[/]  "
                f"{_bar(None, 9)} [dim]n/a[/]  "
                f"{_bar(mem_pct, 9)} [{_DIM}]{gu}/{gt}GB[/]"
            )
        lines += [
            "",
            f" [{_DIM}]Total  {total_pwr:.0f} W   {active_cnt}/{len(gpus)} GPU{'s' if len(gpus)!=1 else ''} active[/]",
        ]
        self.query_one("#gpu", Static).update("\n".join(lines))

    def _update_events(self) -> None:
        if not self.active_result_dir:
            return
        ep = self.active_result_dir / "events.jsonl"
        if not ep.exists():
            return
        lines = ep.read_text(encoding="utf-8").splitlines()
        log = self.query_one("#log", RichLog)
        for raw in lines[self.last_event_count:]:
            try:
                ev = json.loads(raw)
            except json.JSONDecodeError:
                log.write(raw)
                continue
            event = ev.get("event", "?")
            color = EVENT_COLORS.get(event, _DIM)
            ts    = ev.get("time", "")
            parts = "  ".join(
                f"[{_DIM}]{k}[/]=[bold]{v}[/bold]"
                for k, v in ev.items() if k not in {"time", "event"}
            )
            log.write(f"[{_DIM}]{ts}[/]  [{color}]{event}[/]  {parts}")
        self.last_event_count = len(lines)

    def _update_jobs_table(self) -> None:
        if not self.active_result_dir:
            return
        jp = self.active_result_dir / "jobs.csv"
        if not jp.exists():
            return
        rows = _rcsv(jp)
        t = self.query_one("#jobs", DataTable)
        t.clear()
        for row in rows:
            status = row.get("status", "")
            icon, sc = STATUS_ICONS.get(status, ("?", _DIM))
            sc_cell = Text()
            sc_cell.append(f"{icon} ", style=sc)
            sc_cell.append(status,     style=sc)

            wait = row.get("wait_time_seconds", "")
            wc = Text()
            if wait:
                wf = float(wait)
                wcolor = _ERR if wf > T_WAIT_SECONDS else _WARN if wf > 30 else _OK
                wc.append(f"{wf:.1f}", style=wcolor)
            else:
                wc.append("─", style=_DIM)

            rt = row.get("runtime_seconds", "")
            rt_cell = Text(rt if rt else "─", style="" if rt else _DIM)

            dev = row.get("assigned_device", "")
            dev_cell = Text(dev if dev else "─", style="" if dev else _DIM)

            t.add_row(
                row.get("job_id",               ""),
                row.get("user_id",               ""),
                row.get("group",                 ""),
                row.get("job_type",              ""),
                row.get("requested_memory_gb",   ""),
                wc,
                rt_cell,
                sc_cell,
                dev_cell,
            )

    def _load_latest(self) -> None:
        cands = _latest_dirs()
        if cands:
            self.active_result_dir = cands[0]
            self.last_event_count = 0
            self._tick()

    def _path(self, wid: str) -> str:
        return str(ROOT / str(self.query_one(f"#{wid}", Select).value))

    def _sel_stem(self, wid: str) -> str:
        try:
            return Path(str(self.query_one(f"#{wid}", Select).value)).stem
        except Exception:
            return "?"

    def _val(self, wid: str, default: str) -> str:
        v = self.query_one(f"#{wid}", Input).value.strip()
        return v or default


# ── module-level helpers ─────────────────────────────────────────────────────────

def _bar(value: float | None, width: int = 20) -> str:
    if value is None:
        return f"[{_DIM}]{'─' * width}[/]"
    c = max(0.0, min(100.0, value))
    f = int(width * c / 100)
    col = _ERR if c >= 85 else _WARN if c >= 60 else _OK
    return f"[{col}]{'█' * f}[/][{_DIM}]{'░' * (width - f)}[/]"

def _uc(v: float | None) -> str:
    return _DIM if v is None else _ERR if v >= 85 else _WARN if v >= 60 else _OK

def _ps(v: float | None) -> str:
    return "  n/a" if v is None else f"{v:5.1f}%"

def _pf(value: str | float | None) -> float | None:
    if value in (None, "", "unavailable", "?"):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def _ts(ts: str) -> str:
    if not ts:
        return "─"
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts

def _elapsed_secs(started_at: str) -> int:
    if not started_at:
        return 0
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return max(0, int((datetime.now(tz=start.tzinfo) - start).total_seconds()))
    except Exception:
        return 0


def _elapsed(started_at: str) -> str:
    if not started_at:
        return "─"
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        secs  = int((datetime.now(tz=start.tzinfo) - start).total_seconds())
        h, r  = divmod(secs, 3600)
        m, s  = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        return "─"

def _last_gpu_per_device(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    out: dict[str, dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row.get("device", "?")] = row
    return out

def _latest_dirs(min_mtime: float = 0.0) -> list[Path]:
    if not RESULTS_ROOT.exists():
        return []
    return sorted(
        [p for p in RESULTS_ROOT.iterdir()
         if p.is_dir() and (p / "metadata.json").exists()
         and p.stat().st_mtime >= min_mtime],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

def _rjson(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def _rcsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))

def _count(rows: list[dict[str, str]]) -> dict[str, int]:
    c = {"completed": 0, "failed": 0, "running": 0, "queued": 0, "rejected": 0}
    for row in rows:
        s = row.get("status", "")
        if   s == "completed":                c["completed"] += 1
        elif s == "failed":                   c["failed"]    += 1
        elif s == "running":                  c["running"]   += 1
        elif s == "rejected":                 c["rejected"]  += 1
        elif s in {"scheduled", "submitted"}: c["queued"]    += 1
    return c


if __name__ == "__main__":
    ExperimentTui().run()
