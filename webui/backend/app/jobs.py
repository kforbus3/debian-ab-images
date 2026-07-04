"""Background job manager: runs a command, captures output, streams it live.

Jobs and their logs are persisted under <output>/jobs so history survives a
web UI restart (logs of finished jobs are read back from disk on demand).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Literal

JobStatus = Literal["running", "success", "failed", "canceled"]


@dataclass
class Job:
    id: str
    type: str
    label: str
    cmd: list[str]
    status: JobStatus = "running"
    returncode: int | None = None
    lines: list[str] = field(default_factory=list)
    started: str = ""
    finished: str = ""
    env: dict[str, str] | None = None
    _proc: asyncio.subprocess.Process | None = None
    _subscribers: list[asyncio.Queue] = field(default_factory=list)
    _logfh: object | None = None

    def public(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "status": self.status,
            "returncode": self.returncode,
            "started": self.started,
            "finished": self.finished,
            "lines": len(self.lines),
        }


class JobManager:
    def __init__(self, state_dir: str | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._counter = 0
        self._state_dir = state_dir
        if state_dir:
            os.makedirs(state_dir, exist_ok=True)
            self._load_index()

    # ------------------------- persistence -------------------------
    def _index_path(self) -> str:
        return os.path.join(self._state_dir, "jobs.json")

    def _log_path(self, job_id: str) -> str:
        return os.path.join(self._state_dir, f"{job_id}.log")

    def _load_index(self) -> None:
        try:
            with open(self._index_path()) as f:
                for j in json.load(f):
                    job = Job(
                        id=j["id"], type=j["type"], label=j["label"], cmd=[],
                        status=j["status"], returncode=j.get("returncode"),
                        started=j.get("started", ""), finished=j.get("finished", ""),
                    )
                    # A job that was "running" when the UI died is dead now.
                    if job.status == "running":
                        job.status = "failed"
                    self._jobs[job.id] = job
                    n = int(job.id.rsplit("-", 1)[-1]) if job.id.rsplit("-", 1)[-1].isdigit() else 0
                    self._counter = max(self._counter, n)
        except (FileNotFoundError, ValueError, KeyError):
            pass

    def _save_index(self) -> None:
        if not self._state_dir:
            return
        tmp = self._index_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump([j.public() for j in self._jobs.values()], f)
        os.replace(tmp, self._index_path())

    def log_text(self, job: Job) -> str:
        """Full log: in-memory for live jobs, from disk for restored ones."""
        if job.lines or not self._state_dir:
            return "\n".join(job.lines)
        try:
            with open(self._log_path(job.id)) as f:
                return f.read().rstrip("\n")
        except OSError:
            return ""

    # --------------------------- lifecycle ---------------------------
    def list(self) -> list[dict]:
        return [j.public() for j in sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def running(self, type: str | None = None) -> Job | None:
        """First running job (optionally of a given type)."""
        for j in self._jobs.values():
            if j.status == "running" and (type is None or j.type == type):
                return j
        return None

    async def start(self, *, type: str, label: str, cmd: list[str], now: str,
                    env: dict[str, str] | None = None) -> Job:
        self._counter += 1
        job = Job(id=f"{type}-{self._counter}", type=type, label=label, cmd=cmd,
                  started=now, env=env)
        self._jobs[job.id] = job
        self._save_index()
        asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: Job) -> None:
        if self._state_dir:
            job._logfh = open(self._log_path(job.id), "w")
        try:
            # Secrets travel in the process environment, never on the command
            # line, so they don't show up in `ps` or the job's persisted cmd.
            proc = await asyncio.create_subprocess_exec(
                *job.cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                env={**os.environ, **(job.env or {})},
            )
        except Exception as exc:  # noqa: BLE001
            await self._emit(job, f"Failed to launch: {exc}")
            job.status = "failed"
            job.returncode = -1
            await self._close(job)
            return

        job._proc = proc
        assert proc.stdout
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            await self._emit(job, raw.decode(errors="replace").rstrip("\n"))
        rc = await proc.wait()
        job.returncode = rc
        if job.status != "canceled":
            job.status = "success" if rc == 0 else "failed"
        await self._close(job)

    async def _emit(self, job: Job, line: str) -> None:
        job.lines.append(line)
        if len(job.lines) > 5000:
            job.lines = job.lines[-5000:]
        if job._logfh:
            job._logfh.write(line + "\n")
        for q in list(job._subscribers):
            await q.put(line)

    async def _close(self, job: Job) -> None:
        from app.orchestrator import now as _now
        job.finished = _now()
        if job._logfh:
            job._logfh.close()
            job._logfh = None
        self._save_index()
        for q in list(job._subscribers):
            await q.put(None)

    async def cancel(self, job: Job) -> None:
        if job._proc and job.status == "running":
            job.status = "canceled"
            try:
                job._proc.terminate()
            except ProcessLookupError:
                pass

    async def subscribe(self, job: Job):
        """Yield existing lines, then live lines until the job ends."""
        q: asyncio.Queue = asyncio.Queue()
        # Replay backlog.
        for line in list(job.lines):
            yield line
        if job.status != "running":
            return
        job._subscribers.append(q)
        try:
            while True:
                line = await q.get()
                if line is None:
                    break
                yield line
        finally:
            if q in job._subscribers:
                job._subscribers.remove(q)


def _state_dir() -> str | None:
    try:
        from app.config import settings
        return os.path.join(settings.output_dir, "jobs")
    except Exception:  # noqa: BLE001
        return None


jobs = JobManager(_state_dir())
