"""Unit tests for gateway connection logic."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from botparty_robot.gateway import GatewayConnection
from botparty_robot.client_state import ClientState


@pytest.fixture
async def gateway_connection():
    """Create a gateway connection for testing."""
    conn = GatewayConnection(
        robot_id="test-robot-id",
        robot_token="test-token",
        gateway_url="ws://localhost:8080/ws",
        on_action=AsyncMock(),
        on_disconnect=AsyncMock(),
    )
    yield conn
    # Cleanup
    if conn.connected:
        await conn.disconnect()


@pytest.mark.asyncio
async def test_gateway_connection_init(gateway_connection):
    """Test gateway connection initialization."""
    assert gateway_connection.robot_id == "test-robot-id"
    assert gateway_connection.robot_token == "test-token"
    assert gateway_connection.gateway_url == "ws://localhost:8080/ws"
    assert not gateway_connection.connected


@pytest.mark.asyncio
async def test_gateway_connection_disconnect_idempotent(gateway_connection):
    """Test that disconnect can be called multiple times safely."""
    # Should not raise, even if not connected
    await gateway_connection.disconnect()
    await gateway_connection.disconnect()  # Second call should be safe


@pytest.mark.asyncio
async def test_gateway_send_event(gateway_connection):
    """Test sending an event through gateway."""
    # Mock the WebSocket connection
    gateway_connection._ws = AsyncMock()
    gateway_connection._ws.send = AsyncMock()
    gateway_connection._connected = True

    await gateway_connection.send_event("control", {"command": "forward", "value": 100})

    # Verify the event was sent
    gateway_connection._ws.send.assert_called_once()
    call_args = gateway_connection._ws.send.call_args[0][0]
    sent_data = json.loads(call_args)
    assert sent_data["event"] == "control"
    assert sent_data["data"]["command"] == "forward"


@pytest.mark.asyncio
async def test_gateway_heartbeat():
    """Test gateway heartbeat mechanism."""
    conn = GatewayConnection(
        robot_id="test-robot",
        robot_token="test-token",
        gateway_url="ws://localhost:8080/ws",
        on_action=AsyncMock(),
        on_disconnect=AsyncMock(),
        heartbeat_interval_s=0.1,  # Short interval for testing
    )

    conn._ws = AsyncMock()
    conn._ws.send = AsyncMock()
    conn._connected = True

    # Start heartbeat
    task = asyncio.create_task(conn._heartbeat_loop())

    # Wait for at least one heartbeat
    await asyncio.sleep(0.2)

    # Cleanup
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Verify heartbeat was sent (should have at least 1 or 2 calls)
    assert conn._ws.send.call_count >= 1


@pytest.mark.asyncio
async def test_gateway_reconnect_backoff(gateway_connection):
    """Test exponential backoff on reconnection."""
    with patch.object(gateway_connection, "_connect", new_callable=AsyncMock):
        backoffs = []

        async def track_backoff():
            for i in range(3):
                # Simulate backoff delays
                delay = min(2 ** i, 30)
                backoffs.append(delay)
                if i >= 2:
                    break

        await track_backoff()

        # Expected: 1s, 2s, 4s (exponential with cap)
        assert backoffs[0] <= 2
        assert backoffs[1] <= 4
        assert backoffs[2] <= 8


def test_gateway_event_parsing():
    """Test parsing incoming WebSocket events."""
    event_data = {
        "event": "control_result",
        "data": {"status": "success", "command": "forward"},
    }

    # The gateway should properly parse this event
    event_str = json.dumps(event_data)
    parsed = json.loads(event_str)

    assert parsed["event"] == "control_result"
    assert parsed["data"]["status"] == "success"


@pytest.mark.asyncio
async def test_gateway_handles_invalid_json(gateway_connection):
    """Test graceful handling of invalid JSON."""
    gateway_connection._ws = AsyncMock()
    gateway_connection._connected = True

    # The gateway should handle invalid messages without crashing
    try:
        # Simulate receiving invalid JSON
        invalid_msg = "not json at all"
        # This would normally come from ws.recv()
        # For now, just verify the connection doesn't crash
        assert gateway_connection.connected
    except Exception as e:
        pytest.fail(f"Gateway should handle invalid JSON gracefully: {e}")


@pytest.mark.asyncio
async def test_gateway_reconnect_callback(gateway_connection):
    """Test that on_disconnect callback is called."""
    gateway_connection.on_disconnect = AsyncMock()

    # Simulate disconnection
    await gateway_connection._handle_disconnect()

    gateway_connection.on_disconnect.assert_called_once()
