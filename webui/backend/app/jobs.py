"""Background job manager: runs a command, captures output, streams it live."""

from __future__ import annotations

import asyncio
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
    _proc: asyncio.subprocess.Process | None = None
    _subscribers: list[asyncio.Queue] = field(default_factory=list)

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
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._counter = 0

    def list(self) -> list[dict]:
        return [j.public() for j in sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)]

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def start(self, *, type: str, label: str, cmd: list[str], now: str) -> Job:
        self._counter += 1
        job = Job(id=f"{type}-{self._counter}", type=type, label=label, cmd=cmd, started=now)
        self._jobs[job.id] = job
        asyncio.create_task(self._run(job))
        return job

    async def _run(self, job: Job) -> None:
        try:
            proc = await asyncio.create_subprocess_exec(
                *job.cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
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
        for q in list(job._subscribers):
            await q.put(line)

    async def _close(self, job: Job) -> None:
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


jobs = JobManager()
