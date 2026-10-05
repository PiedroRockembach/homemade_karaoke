import asyncio
import threading

from karaoke.events import EventBus, sse_stream


def test_publish_from_thread_reaches_subscriber():
    async def run():
        bus = EventBus()
        bus.bind_loop()
        sub = bus.subscribe("c_1", "stage")
        assert bus.count("stage") == 1
        t = threading.Thread(target=bus.publish, args=("job.updated", {"id": "j_1"}))
        t.start()
        t.join()
        ev = await asyncio.wait_for(sub.queue.get(), 1)
        assert ev.type == "job.updated" and ev.data == {"id": "j_1"}
        assert ev.encode().startswith(f"id: {ev.id}\nevent: job.updated\ndata: ")
        bus.unsubscribe(sub)
        assert bus.count() == 0

    asyncio.run(run())


def test_sse_stream_ping_and_close():
    async def run():
        bus = EventBus()
        bus.bind_loop()
        sub = bus.subscribe("c_1", "control")
        gen = sse_stream(bus, sub, keepalive_s=0.01)
        assert (await anext(gen)).startswith("retry:")
        assert await anext(gen) == ": ping\n\n"
        bus.publish("toast", {"level": "info", "message": "olá"})
        assert "event: toast" in await anext(gen)
        bus.close_all()
        chunks = [c async for c in gen]
        assert chunks == []

    asyncio.run(run())
