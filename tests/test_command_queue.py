import asyncio

import pytest

from botparty_robot.client_state import QueuedHardwareCommand
from botparty_robot.command_queue import HardwareCommandQueue


def _command(name: str, motion_id: int | None = None) -> QueuedHardwareCommand:
    return QueuedHardwareCommand(name, None, {"actionId": name}, motion_id)


def test_queue_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        HardwareCommandQueue(0)


def test_queue_is_bounded_and_preserves_fifo_for_non_motion_commands() -> None:
    queue = HardwareCommandQueue(2)

    assert queue.offer(_command("first")) == (True, ())
    assert queue.offer(_command("second")) == (True, ())
    assert queue.offer(_command("third")) == (False, ())
    assert queue.high_watermark == 2
    assert queue.dropped == 1
    assert queue.pop_nowait() == _command("first")
    assert queue.pop_nowait() == _command("second")
    assert queue.pop_nowait() is None


def test_latest_motion_supersedes_only_queued_motion() -> None:
    queue = HardwareCommandQueue(3)
    non_motion = _command("lights")
    old_motion = _command("left", 1)
    new_motion = _command("right", 2)

    assert queue.offer(non_motion)[0]
    assert queue.offer(old_motion)[0]
    accepted, superseded = queue.offer(new_motion)

    assert accepted is True
    assert superseded == (old_motion,)
    assert queue.pending() == (non_motion, new_motion)
    assert queue.dropped == 1


def test_cancel_motion_keeps_non_motion_commands_and_wakes_waiter() -> None:
    async def scenario() -> None:
        queue = HardwareCommandQueue(3)
        waiter = asyncio.create_task(queue.wait())
        await asyncio.sleep(0)
        queue.offer(_command("lights"))
        await asyncio.wait_for(waiter, timeout=1)
        queue.offer(_command("forward", 1))

        assert queue.cancel_motion() == (_command("forward", 1),)
        assert queue.pending() == (_command("lights"),)
        assert queue.dropped == 1

    asyncio.run(scenario())
