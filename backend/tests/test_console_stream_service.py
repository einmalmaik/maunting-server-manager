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
async def test_stream_service_clears_buffer_on_last_disconnect():
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
    # Yield control to let the ws accept and the loop start
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
        assert len(state.lines) == 0  # Buffer IS cleared


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
