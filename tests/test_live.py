"""Long runs stay visible: thread-safe events, overflow → resync, live state on reload."""

from __future__ import annotations

import asyncio
import json

from opendot.bus import EventBus


def test_emit_from_worker_thread_reaches_subscribers():
    async def go():
        bus = EventBus()
        got = []

        async def listener(kind, data):
            got.append(kind)

        bus.listen(listener)
        q = bus.subscribe()
        await asyncio.to_thread(bus.emit, "thread_file", x=1)
        msg = await asyncio.wait_for(q.get(), 1)
        await asyncio.sleep(0)
        return json.loads(msg)["kind"], got

    kind, got = asyncio.run(go())
    assert kind == "thread_file" and got == ["thread_file"]


def test_overflow_tells_client_to_resync():
    async def go():
        bus = EventBus()
        q = bus.subscribe()
        for i in range(q.maxsize + 5):
            bus.emit("delta", text=str(i))
        items = [json.loads(q.get_nowait())["kind"] for _ in range(q.qsize())]
        return items

    items = asyncio.run(go())
    assert "resync" in items and len(items) < 20


def test_bootstrap_carries_runs_in_flight(client, auth_headers):
    from opendot import runtime
    runtime._live["run_x"] = {"agent_id": "ag_1", "thread_id": "th_1", "run_id": "run_x",
                              "text": "half an answer", "thought": "", "workers": {
                                  "ag_1-w0": {"worker_id": "ag_1-w0", "title": "dig"}}}
    try:
        live = client.get("/api/bootstrap", headers=auth_headers).json()["live"]
    finally:
        runtime._live.pop("run_x")
    assert live[0]["text"] == "half an answer" and live[0]["workers"][0]["title"] == "dig"
