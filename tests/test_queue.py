"""Unit tests for the lease-based GenerationQueue.

These cover the contract documented in docs/ARCHITECTURE.md s6: dual-channel
isolation, lease + retry + DLQ state machine, and persistence round-trip.
All tests are fully synchronous-equivalent (RPM=0, sleeps stubbed) so they
finish in milliseconds.
"""

from __future__ import annotations

import threading
import time

from agent_runtime.queue import (
    CHANNEL_IMAGE,
    CHANNEL_VIDEO,
    DLQ,
    LEASED,
    PENDING,
    SUCCEEDED,
    ChannelConfig,
    GenerationQueue,
    Task,
)


def _channels(image_concurrency: int = 4, video_concurrency: int = 2) -> dict:
    return {
        CHANNEL_IMAGE: ChannelConfig(CHANNEL_IMAGE, concurrency=image_concurrency, rpm=0),
        CHANNEL_VIDEO: ChannelConfig(CHANNEL_VIDEO, concurrency=video_concurrency, rpm=0),
    }


# --------------------------------------------------------------------------- #
# basic lifecycle
# --------------------------------------------------------------------------- #
def test_drain_runs_all_pending_tasks_and_records_cost():
    q = GenerationQueue(channels=_channels())
    q.register_handler(CHANNEL_IMAGE, lambda task: {"cost": 0.5})
    for _ in range(10):
        q.enqueue(CHANNEL_IMAGE, kind="img", payload={})
    q.drain()
    assert all(t.state == SUCCEEDED for t in q.tasks.values())
    assert q.total_cost() == round(10 * 0.5, 4)


def test_dual_channels_are_isolated():
    """Image and video tasks run independently with their own concurrency."""
    q = GenerationQueue(channels=_channels())
    seen = {"image": [], "video": []}
    lock = threading.Lock()

    def make_handler(label):
        def handle(task):
            with lock:
                seen[label].append(task.id)
            return {"cost": 1.0}
        return handle

    q.register_handler(CHANNEL_IMAGE, make_handler("image"))
    q.register_handler(CHANNEL_VIDEO, make_handler("video"))
    for i in range(6):
        q.enqueue(CHANNEL_IMAGE, kind="img", payload={"i": i})
    for i in range(3):
        q.enqueue(CHANNEL_VIDEO, kind="vid", payload={"i": i})
    q.drain()
    assert len(seen["image"]) == 6
    assert len(seen["video"]) == 3
    assert all(t.state == SUCCEEDED for t in q.tasks.values())


def test_channel_concurrency_is_respected():
    """At most ``concurrency`` workers from one channel run simultaneously."""
    q = GenerationQueue(channels=_channels(image_concurrency=3))
    in_flight = 0
    peak = 0
    cond = threading.Condition()

    def slow_handler(task):
        nonlocal in_flight, peak
        with cond:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with cond:
            in_flight -= 1
        return {"cost": 0.1}

    q.register_handler(CHANNEL_IMAGE, slow_handler)
    for _ in range(8):
        q.enqueue(CHANNEL_IMAGE, kind="img", payload={})
    q.drain()
    assert peak <= 3, f"peak in-flight ({peak}) exceeded concurrency cap"
    assert all(t.state == SUCCEEDED for t in q.tasks.values())


# --------------------------------------------------------------------------- #
# retry / DLQ
# --------------------------------------------------------------------------- #
def test_transient_error_retries_until_success():
    q = GenerationQueue(channels=_channels())
    attempts = {"n": 0}

    def flaky(task):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("temporary")
        return {"cost": 0.4}

    q.register_handler(CHANNEL_IMAGE, flaky)
    task = q.enqueue(CHANNEL_IMAGE, kind="img", payload={})
    q.drain()
    assert q.tasks[task.id].state == SUCCEEDED
    assert q.tasks[task.id].attempts == 3
    assert q.total_cost() == 0.4


def test_exhausted_retries_go_to_dlq():
    q = GenerationQueue(channels=_channels())
    q.register_handler(CHANNEL_IMAGE,
                       lambda task: (_ for _ in ()).throw(RuntimeError("boom")))
    task = q.enqueue(CHANNEL_IMAGE, kind="img", payload={}, max_attempts=2)
    q.drain()
    assert q.tasks[task.id].state == DLQ
    assert q.tasks[task.id].attempts == 2
    assert task.id in q.dlq
    assert "boom" in q.tasks[task.id].last_error


def test_retry_dlq_re_enqueues_with_clean_attempts():
    q = GenerationQueue(channels=_channels())
    fails = {"left": 3}  # 2 fails in first drain, 1 fail then success in second

    def maybe_fail(task):
        if fails["left"] > 0:
            fails["left"] -= 1
            raise RuntimeError("transient")
        return {"cost": 0.7}

    q.register_handler(CHANNEL_IMAGE, maybe_fail)
    q.enqueue(CHANNEL_IMAGE, kind="img", payload={}, max_attempts=2)
    q.drain()
    assert all(t.state == DLQ for t in q.tasks.values())

    moved = q.retry_dlq()
    assert moved == 1
    q.drain()
    assert all(t.state == SUCCEEDED for t in q.tasks.values())


# --------------------------------------------------------------------------- #
# leases
# --------------------------------------------------------------------------- #
def test_recover_expired_returns_lease_to_pending():
    """A worker that crashes leaves a stale lease; recover_expired fixes it."""
    fake_now = {"t": 1000.0}
    q = GenerationQueue(
        channels=_channels(),
        sleep=lambda _s: None,
        now=lambda: fake_now["t"],
    )
    # Manually inject a leased task as if a worker crashed mid-run.
    task = Task(id="abc", channel=CHANNEL_IMAGE, kind="img",
                state=LEASED, lease_deadline=fake_now["t"] + 10, attempts=1)
    q.tasks[task.id] = task

    # Time advances past the lease deadline.
    fake_now["t"] += 20
    recovered = q.recover_expired()
    assert recovered == 1
    assert q.tasks["abc"].state == PENDING
    assert "lease" in q.tasks["abc"].last_error


# --------------------------------------------------------------------------- #
# persistence (resumable across processes)
# --------------------------------------------------------------------------- #
def test_snapshot_restore_round_trip():
    a = GenerationQueue(channels=_channels())
    a.register_handler(CHANNEL_IMAGE, lambda t: {"cost": 0.3})
    a.enqueue(CHANNEL_IMAGE, kind="img", payload={"i": 1})
    a.enqueue(CHANNEL_IMAGE, kind="img", payload={"i": 2})
    a.drain()
    snap = a.snapshot()

    b = GenerationQueue(channels=_channels())
    b.restore(snap)
    assert len(b.tasks) == 2
    assert all(t.state == SUCCEEDED for t in b.tasks.values())
    # Configs round-tripped too.
    assert b.channels[CHANNEL_IMAGE].concurrency == 4


def test_persist_hook_fires_on_state_change(tmp_path):
    persist_calls = {"n": 0}

    def on_change(_q):
        persist_calls["n"] += 1

    q = GenerationQueue(channels=_channels(), on_change=on_change)
    q.register_handler(CHANNEL_IMAGE, lambda t: {"cost": 0.1})
    q.enqueue(CHANNEL_IMAGE, kind="img", payload={})
    # at least one persist for enqueue, plus on success
    pre_drain = persist_calls["n"]
    q.drain()
    assert persist_calls["n"] >= pre_drain + 1
