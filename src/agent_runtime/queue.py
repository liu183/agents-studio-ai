"""Lease-based ``GenerationQueue`` (dual-channel image/video).

Mirrors the design in ``docs/ARCHITECTURE.md s6``:

    GenerationQueue
    ├── image_channel   (concurrency=N1, RPM=R1)
    ├── video_channel   (concurrency=N2, RPM=R2)
    └── shared_dlq      (failed tasks)

Per the 09-video-generator SKILL: image and video MUST go through independent
channels because they have very different concurrency / cost / latency
profiles (image: 4 / 20 RPM; video: 2 / 4 RPM).

Lease-based scheduling
----------------------
Every claimed task gets a *lease* with a deadline. A worker that crashes
before completing leaves an expired lease behind; ``recover_expired`` then
promotes the task back to ``pending`` for a peer to retry. This is what makes
the queue **resumable across crashes** -- we persist the queue snapshot in
``project.json`` after every state change, so a fresh process can pick up
mid-batch by calling ``recover_expired`` at startup.

Concurrency model
-----------------
We use one ``ThreadPoolExecutor`` per channel. The executors here are
I/O-bound (HTTP calls to provider APIs), so threads are sufficient and let us
stay on the standard library. The dispatcher loop is single-threaded and
holds a single lock around all queue mutations -- simple and obviously
correct, plenty fast for the hundreds of tasks per episode that we expect.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

# --------------------------------------------------------------------------- #
# domain model
# --------------------------------------------------------------------------- #
PENDING = "pending"
LEASED = "leased"  # claimed by a worker; lease has a deadline
RUNNING = "running"  # handler is executing
SUCCEEDED = "succeeded"
FAILED = "failed"  # transient failure; will retry until exhausted
DLQ = "dlq"  # exhausted retries / permanent failure

CHANNEL_IMAGE = "image"
CHANNEL_VIDEO = "video"
CHANNEL_TTS = "tts"
CHANNELS = (CHANNEL_IMAGE, CHANNEL_VIDEO, CHANNEL_TTS)


@dataclass
class Task:
    """A unit of generation work routed through one channel."""

    id: str
    channel: str
    kind: str  # e.g. "character_image", "video_clip", "tts_audio"
    payload: Dict[str, Any] = field(default_factory=dict)
    state: str = PENDING
    attempts: int = 0
    max_attempts: int = 3
    lease_deadline: float = 0.0  # absolute epoch seconds, 0 == no lease
    last_error: str = ""
    cost: float = 0.0
    result_meta: Dict[str, Any] = field(default_factory=dict)
    submitted_at: float = field(default_factory=lambda: time.time())
    completed_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "channel": self.channel, "kind": self.kind,
            "payload": self.payload, "state": self.state, "attempts": self.attempts,
            "max_attempts": self.max_attempts, "lease_deadline": self.lease_deadline,
            "last_error": self.last_error, "cost": self.cost,
            "result_meta": self.result_meta, "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Task":
        return cls(**data)


@dataclass
class ChannelConfig:
    name: str
    concurrency: int = 2
    rpm: int = 0  # 0 == unlimited
    lease_seconds: float = 300.0


# --------------------------------------------------------------------------- #
# rate limiter (per-channel token bucket, requests-per-minute)
# --------------------------------------------------------------------------- #
class _RateLimiter:
    """Simple sliding-window RPM gate.

    Records the timestamps of the last ``rpm`` requests; ``acquire`` blocks
    until a slot opens. ``rpm == 0`` disables limiting.
    """

    def __init__(self, rpm: int, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], float] = time.time):
        self.rpm = rpm
        self._timestamps: Deque[float] = deque()
        self._lock = threading.Lock()
        self._sleep = sleep
        self._now = now

    def acquire(self) -> None:
        if self.rpm <= 0:
            return
        while True:
            with self._lock:
                now = self._now()
                cutoff = now - 60.0
                while self._timestamps and self._timestamps[0] < cutoff:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.rpm:
                    self._timestamps.append(now)
                    return
                wait = 60.0 - (now - self._timestamps[0])
            if wait > 0:
                self._sleep(min(wait, 1.0))


# --------------------------------------------------------------------------- #
# the queue
# --------------------------------------------------------------------------- #
TaskHandler = Callable[[Task], Dict[str, Any]]
PersistHook = Callable[["GenerationQueue"], None]


class GenerationQueue:
    """In-process, lease-based dual-channel queue."""

    def __init__(
        self,
        channels: Optional[Dict[str, ChannelConfig]] = None,
        on_change: Optional[PersistHook] = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ):
        self.channels: Dict[str, ChannelConfig] = channels or {
            CHANNEL_IMAGE: ChannelConfig(CHANNEL_IMAGE, concurrency=4, rpm=20),
            CHANNEL_VIDEO: ChannelConfig(CHANNEL_VIDEO, concurrency=2, rpm=4),
            CHANNEL_TTS: ChannelConfig(CHANNEL_TTS, concurrency=4, rpm=60),
        }
        self.tasks: Dict[str, Task] = {}
        self.dlq: List[str] = []
        self._handlers: Dict[str, TaskHandler] = {}
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._now = now
        self._sleep = sleep
        self._on_change = on_change
        self._limiters: Dict[str, _RateLimiter] = {
            ch: _RateLimiter(cfg.rpm, sleep=sleep, now=now)
            for ch, cfg in self.channels.items()
        }
        # event log (only kept in-memory by default; persisted form is small)
        self.history: List[Dict[str, Any]] = []

    # ---- registration ------------------------------------------------------
    def register_handler(self, channel: str, handler: TaskHandler) -> None:
        if channel not in self.channels:
            raise ValueError(f"unknown channel: {channel}")
        self._handlers[channel] = handler

    # ---- enqueue / submit --------------------------------------------------
    def enqueue(self, channel: str, kind: str, payload: Dict[str, Any],
                max_attempts: int = 3, task_id: Optional[str] = None) -> Task:
        if channel not in self.channels:
            raise ValueError(f"unknown channel: {channel}")
        with self._cond:
            tid = task_id or f"{channel[:1]}-{uuid.uuid4().hex[:8]}"
            task = Task(id=tid, channel=channel, kind=kind, payload=payload,
                        max_attempts=max_attempts)
            self.tasks[tid] = task
            self._cond.notify_all()
            self._record("task.enqueued", task)
        self._persist()
        return task

    # ---- claim / lease -----------------------------------------------------
    def _claim(self, channel: str) -> Optional[Task]:
        cfg = self.channels[channel]
        with self._lock:
            for task in self.tasks.values():
                if task.channel == channel and task.state == PENDING:
                    task.state = LEASED
                    task.lease_deadline = self._now() + cfg.lease_seconds
                    task.attempts += 1
                    self._record("task.leased", task)
                    return task
        return None

    def extend_lease(self, task_id: str, seconds: Optional[float] = None) -> None:
        with self._lock:
            task = self.tasks.get(task_id)
            if not task or task.state not in (LEASED, RUNNING):
                return
            seconds = seconds or self.channels[task.channel].lease_seconds
            task.lease_deadline = self._now() + seconds

    def recover_expired(self) -> int:
        """Return any LEASED/RUNNING task whose lease deadline has passed back
        to PENDING. Called on startup and periodically by the dispatcher."""
        recovered = 0
        with self._cond:
            now = self._now()
            for task in self.tasks.values():
                if task.state in (LEASED, RUNNING) and task.lease_deadline < now:
                    task.state = PENDING
                    task.last_error = task.last_error or "lease expired"
                    self._record("task.lease_expired", task)
                    recovered += 1
            if recovered:
                self._cond.notify_all()
        if recovered:
            self._persist()
        return recovered

    # ---- complete ----------------------------------------------------------
    def _succeed(self, task: Task, cost: float, meta: Dict[str, Any]) -> None:
        with self._cond:
            task.state = SUCCEEDED
            task.cost = cost
            task.result_meta = meta
            task.completed_at = self._now()
            task.lease_deadline = 0.0
            self._record("task.succeeded", task)
            self._cond.notify_all()
        self._persist()

    def _fail(self, task: Task, error: str) -> None:
        with self._cond:
            task.last_error = error
            if task.attempts >= task.max_attempts:
                task.state = DLQ
                if task.id not in self.dlq:
                    self.dlq.append(task.id)
                task.completed_at = self._now()
                self._record("task.dlq", task)
            else:
                task.state = PENDING  # available for retry by any worker
                task.lease_deadline = 0.0
                self._record("task.failed", task)
            self._cond.notify_all()
        self._persist()

    # ---- dispatcher / drain ------------------------------------------------
    def drain(self, timeout: float = 0.0) -> Dict[str, Any]:
        """Run pending tasks across all channels until none remain.

        Each channel runs its own ``ThreadPoolExecutor``. The main thread
        coordinates: it watches all channels for terminal states, recovers
        expired leases, and returns a summary when everything is settled.
        ``timeout > 0`` aborts with a timeout if work outruns the budget.
        """
        for ch, cfg in self.channels.items():
            if ch not in self._handlers:
                continue
        # spawn pools lazily; one pool per channel
        pools: Dict[str, ThreadPoolExecutor] = {}
        active: Dict[str, List[Future]] = {ch: [] for ch in self.channels}
        deadline = self._now() + timeout if timeout > 0 else 0.0

        try:
            for ch, cfg in self.channels.items():
                if ch in self._handlers:
                    pools[ch] = ThreadPoolExecutor(
                        max_workers=cfg.concurrency, thread_name_prefix=f"q-{ch}"
                    )

            while True:
                if deadline and self._now() > deadline:
                    raise TimeoutError("queue.drain timed out")
                self.recover_expired()
                # try to fill each channel up to its concurrency
                progressed = False
                for ch, pool in pools.items():
                    cfg = self.channels[ch]
                    # prune finished futures
                    active[ch] = [f for f in active[ch] if not f.done()]
                    while len(active[ch]) < cfg.concurrency:
                        task = self._claim(ch)
                        if task is None:
                            break
                        active[ch].append(pool.submit(self._run_one, task))
                        progressed = True

                if self._all_settled() and not any(active[ch] for ch in pools):
                    break
                if not progressed:
                    # block briefly for completions / new pendings
                    with self._cond:
                        self._cond.wait(timeout=0.25)
        finally:
            for pool in pools.values():
                pool.shutdown(wait=True)
        return self.snapshot()

    def _run_one(self, task: Task) -> None:
        handler = self._handlers.get(task.channel)
        if handler is None:
            self._fail(task, f"no handler for channel {task.channel}")
            return
        self._limiters[task.channel].acquire()
        with self._lock:
            task.state = RUNNING
        try:
            outcome = handler(task) or {}
        except Exception as exc:  # handler-level error -> retry/dlq
            self._fail(task, f"{type(exc).__name__}: {exc}")
            return
        self._succeed(task, cost=float(outcome.get("cost", 0.0)),
                      meta=dict(outcome.get("meta", {})))

    def _all_settled(self) -> bool:
        with self._lock:
            return all(t.state in (SUCCEEDED, DLQ) for t in self.tasks.values())

    # ---- inspection --------------------------------------------------------
    def task(self, task_id: str) -> Optional[Task]:
        return self.tasks.get(task_id)

    def by_state(self, state: str) -> List[Task]:
        return [t for t in self.tasks.values() if t.state == state]

    def stats(self) -> Dict[str, Dict[str, int]]:
        out: Dict[str, Dict[str, int]] = {ch: {} for ch in self.channels}
        for task in self.tasks.values():
            ch = out.setdefault(task.channel, {})
            ch[task.state] = ch.get(task.state, 0) + 1
        out["dlq_total"] = {"count": len(self.dlq)}
        return out

    def total_cost(self) -> float:
        return round(sum(t.cost for t in self.tasks.values()
                         if t.state == SUCCEEDED), 4)

    def retry_dlq(self) -> int:
        """Move every DLQ task back to PENDING with attempts reset."""
        moved = 0
        with self._cond:
            for tid in list(self.dlq):
                task = self.tasks.get(tid)
                if not task:
                    continue
                task.state = PENDING
                task.attempts = 0
                task.last_error = ""
                self._record("task.retry_dlq", task)
                moved += 1
            self.dlq = [tid for tid in self.dlq
                        if self.tasks.get(tid, Task("", "", "")).state == DLQ]
            if moved:
                self._cond.notify_all()
        if moved:
            self._persist()
        return moved

    # ---- persistence -------------------------------------------------------
    def snapshot(self) -> Dict[str, Any]:
        return {
            "channels": {ch: {"concurrency": cfg.concurrency, "rpm": cfg.rpm,
                              "lease_seconds": cfg.lease_seconds}
                         for ch, cfg in self.channels.items()},
            "tasks": [t.to_dict() for t in self.tasks.values()],
            "dlq": list(self.dlq),
            "history_tail": self.history[-50:],  # bounded
        }

    def restore(self, snapshot: Dict[str, Any]) -> None:
        if not snapshot:
            return
        for ch, conf in (snapshot.get("channels") or {}).items():
            if ch in self.channels:
                self.channels[ch].concurrency = int(conf.get("concurrency", self.channels[ch].concurrency))
                self.channels[ch].rpm = int(conf.get("rpm", self.channels[ch].rpm))
                self.channels[ch].lease_seconds = float(conf.get(
                    "lease_seconds", self.channels[ch].lease_seconds))
                # rebuild limiter with new rpm
                self._limiters[ch] = _RateLimiter(self.channels[ch].rpm,
                                                  sleep=self._sleep, now=self._now)
        for raw in snapshot.get("tasks") or []:
            task = Task.from_dict(raw)
            self.tasks[task.id] = task
        self.dlq = list(snapshot.get("dlq") or [])
        self.history = list(snapshot.get("history_tail") or [])

    def _persist(self) -> None:
        if self._on_change:
            try:
                self._on_change(self)
            except Exception:  # pragma: no cover - persistence is best-effort
                pass

    def _record(self, event: str, task: Task) -> None:
        self.history.append({
            "ts": self._now(), "event": event, "task_id": task.id,
            "state": task.state, "attempts": task.attempts,
            "channel": task.channel, "kind": task.kind,
        })
        if len(self.history) > 500:
            self.history = self.history[-250:]


# --------------------------------------------------------------------------- #
# helper: bind queue to a Project so its snapshot lives in project.json
# --------------------------------------------------------------------------- #
def queue_for_project(project, channels: Optional[Dict[str, ChannelConfig]] = None,
                      sleep: Callable[[float], None] = time.sleep,
                      now: Callable[[], float] = time.time) -> GenerationQueue:
    """Create a queue whose state mirrors into ``project.data['queue']``.

    Each state change rewrites the snapshot inside project.json (single source
    of truth, per docs/ARCHITECTURE.md s7), so a crashed run picks up where it
    left off after ``recover_expired`` clears stale leases.
    """

    def persist(q: GenerationQueue) -> None:
        project.data["queue"] = q.snapshot()
        project.save()

    queue = GenerationQueue(channels=channels, on_change=persist,
                            sleep=sleep, now=now)
    queue.restore(project.data.get("queue") or {})
    queue.recover_expired()
    return queue
