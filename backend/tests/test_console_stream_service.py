import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch

from services.console_stream_service import (
    _ServerState,
    _get_state,
    _STATES,
    _STATES_LOCK,
    RING_BUFFER_SIZE,
    ingest_line,
    connect,
    reset_state_for_tests,
)
from starlette.websockets import WebSocketDisconnect, WebSocketState


@pytest.fixture(autouse=True)
def setup_teardown():
    reset_state_for_tests()
    yield
    reset_state_for_tests()


@pytest.mark.asyncio
async def test_stream_service_respects_buffer_limit():
    server_id = 42
    
    async with _STATES_LOCK:
        _get_state(server_id)

    # Ingest 1500 lines
    for i in range(1500):
        await ingest_line(server_id, f"Line {i}", "msm", "2026-07-20T10:00:00Z")
        
    async with _STATES_LOCK:
        state = _get_state(server_id)
        assert len(state.lines) == RING_BUFFER_SIZE
        # Check that it kept the newest ones (lines 500 to 1499 if size is 1000)
        assert state.lines[0].text == f"Line {1500 - RING_BUFFER_SIZE}"
        assert state.lines[-1].text == "Line 1499"


@pytest.mark.asyncio
async def test_stream_service_preserves_buffer_on_last_disconnect():
    server_id = 42
    
    # Mock WebSocket
    class MockWebSocket:
        def __init__(self):
            self.client_state = WebSocketState.CONNECTED
            self.sent_messages = []
            self.disconnect_event = asyncio.Event()

        async def accept(self):
            pass

        async def send_text(self, text):
            self.sent_messages.append(text)

        async def receive_text(self):
            await self.disconnect_event.wait()
            self.client_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect()

    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    
    # Connect ws1
    task1 = asyncio.create_task(
        connect(ws1, server_id, "test_container", "/tmp/dummy.log")
    )
    await asyncio.sleep(0.05)
    
    # Ingest some lines
    await ingest_line(server_id, "Test 1", "msm")
    
    # Connect ws2
    task2 = asyncio.create_task(
        connect(ws2, server_id, "test_container", "/tmp/dummy.log")
    )
    await asyncio.sleep(0.05)
    
    await ingest_line(server_id, "Test 2", "msm")
    
    async with _STATES_LOCK:
        state = _get_state(server_id)
        assert len(state.websockets) == 2
        assert len(state.lines) == 2
        
    # Disconnect ws2 (not the last)
    ws2.disconnect_event.set()
    await task2
    
    async with _STATES_LOCK:
        state = _get_state(server_id)
        assert len(state.websockets) == 1
        assert len(state.lines) == 2  # Buffer is NOT cleared
        
    # Disconnect ws1 (last)
    ws1.disconnect_event.set()
    await task1
    
    async with _STATES_LOCK:
        state = _get_state(server_id)
        assert len(state.websockets) == 0
        assert len(state.lines) == 2  # Buffer IS PRESERVED, not cleared
        assert state.next_id == 3    # next_id is preserved


@pytest.mark.asyncio
async def test_reconnect_preserves_lines_and_monotonic_ids():
    from services.console_stream_service import cleanup_inactive_states
    server_id = 77

    class MockWebSocket:
        def __init__(self):
            self.client_state = WebSocketState.CONNECTED
            self.sent_messages = []
            self.disconnect_event = asyncio.Event()

        async def accept(self):
            pass

        async def send_text(self, text):
            self.sent_messages.append(text)

        async def receive_text(self):
            await self.disconnect_event.wait()
            self.client_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect()

    ws1 = MockWebSocket()
    task1 = asyncio.create_task(connect(ws1, server_id, "c", "/tmp/dummy.log"))
    await asyncio.sleep(0.05)

    await ingest_line(server_id, "Line 1", "msm")
    await ingest_line(server_id, "Line 2", "msm")

    # Disconnect ws1
    ws1.disconnect_event.set()
    await task1

    # Reconnect with last_id=1
    ws2 = MockWebSocket()
    task2 = asyncio.create_task(connect(ws2, server_id, "c", "/tmp/dummy.log", last_id=1))
    await asyncio.sleep(0.05)

    await ingest_line(server_id, "Line 3", "msm")

    # Parse messages sent to ws2
    received = [json.loads(m) for m in ws2.sent_messages if "id" in json.loads(m)]
    ids = [r["id"] for r in received]
    texts = [r["text"] for r in received]

    assert texts == ["Line 2", "Line 3"]
    assert ids == [2, 3]

    # Clean up ws2
    ws2.disconnect_event.set()
    await task2

    # Verify inactive state cleanup
    async with _STATES_LOCK:
        assert server_id in _STATES
    removed = cleanup_inactive_states(max_age_seconds=0)
    assert removed == 1
    async with _STATES_LOCK:
        assert server_id not in _STATES

    # Reconnect after state cleanup -> line ID must still be monotonic (starting at 4)
    ws3 = MockWebSocket()
    task3 = asyncio.create_task(connect(ws3, server_id, "c", "/tmp/dummy.log"))
    await asyncio.sleep(0.05)

    await ingest_line(server_id, "Line 4", "msm")

    received3 = [json.loads(m) for m in ws3.sent_messages if "id" in json.loads(m)]
    assert received3[0]["id"] == 4
    assert received3[0]["text"] == "Line 4"

    ws3.disconnect_event.set()
    await task3


@pytest.mark.asyncio
async def test_stream_service_broadcasts_to_all_clients():
    server_id = 99
    
    # Mock WebSocket that just waits
    class MockWebSocket:
        def __init__(self):
            self.client_state = WebSocketState.CONNECTED
            self.sent_messages = []
            self.disconnect_event = asyncio.Event()

        async def accept(self):
            pass

        async def send_text(self, text):
            self.sent_messages.append(text)

        async def receive_text(self):
            await self.disconnect_event.wait()
            raise WebSocketDisconnect()

    ws1 = MockWebSocket()
    ws2 = MockWebSocket()
    
    task1 = asyncio.create_task(connect(ws1, server_id, "c", "/tmp/fake"))
    task2 = asyncio.create_task(connect(ws2, server_id, "c", "/tmp/fake"))
    
    await asyncio.sleep(0.1)  # Let them connect
    
    # Ingest a line
    await ingest_line(server_id, "Broadcast line", "docker")
    
    # Check that both received it
    assert any("Broadcast line" in msg for msg in ws1.sent_messages)
    assert any("Broadcast line" in msg for msg in ws2.sent_messages)
    
    # Clean up
    task1.cancel()
    task2.cancel()

