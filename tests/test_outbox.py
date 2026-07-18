import json

import pytest

from botparty_robot.outbox import MAX_OUTBOX_ENTRIES, OutcomeOutbox, OutcomeOutboxError


def _enqueue(outbox: OutcomeOutbox, subject: str, state: str = "accepted"):
    return outbox.enqueue(
        kind="command",
        subject_id=subject,
        state=state,
        event="robot:command-result",
        data={"commandId": subject, "state": state},
    )


def test_outbox_survives_restart_and_deduplicates_terminal_state(tmp_path) -> None:
    tmp_path.chmod(0o700)
    outbox = OutcomeOutbox(tmp_path)
    accepted = _enqueue(outbox, "command-1")
    terminal = _enqueue(outbox, "command-1", "completed")

    reloaded = OutcomeOutbox(tmp_path)
    assert [item.outcome_id for item in reloaded.pending()] == [
        accepted.outcome_id,
        terminal.outcome_id,
    ]
    assert _enqueue(reloaded, "command-1", "failed") is None
    assert reloaded.ready_for_delivery() == reloaded.pending()
    reloaded.mark_attempted(accepted.outcome_id)
    assert [item.outcome_id for item in reloaded.ready_for_delivery()] == [terminal.outcome_id]
    reloaded.reset_delivery_attempts()
    assert reloaded.ready_for_delivery() == reloaded.pending()
    reloaded.mark_confirmed(accepted.outcome_id)
    reloaded.mark_confirmed(terminal.outcome_id)
    assert OutcomeOutbox(tmp_path).pending() == ()
    assert OutcomeOutbox(tmp_path).state_for("command", "command-1") == "completed"


def test_outbox_rejects_corruption_and_unsafe_permissions(tmp_path) -> None:
    tmp_path.chmod(0o700)
    outbox = OutcomeOutbox(tmp_path)
    record = _enqueue(outbox, "command-1")
    path = tmp_path / "outcomes.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["pending"][0]["data"]["outcomeId"] = "forged"
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(OutcomeOutboxError, match="corrupt"):
        OutcomeOutbox(tmp_path)

    path.unlink()
    _enqueue(OutcomeOutbox(tmp_path), "command-2")
    path.chmod(0o644)
    with pytest.raises(OutcomeOutboxError, match="private"):
        OutcomeOutbox(tmp_path)
    assert record is not None


def test_outbox_capacity_failure_rolls_back_in_memory_state(tmp_path, monkeypatch) -> None:
    tmp_path.chmod(0o700)
    outbox = OutcomeOutbox(tmp_path)
    monkeypatch.setattr("botparty_robot.outbox.MAX_OUTBOX_ENTRIES", 2)
    _enqueue(outbox, "one")
    _enqueue(outbox, "two")

    with pytest.raises(OutcomeOutboxError, match="capacity"):
        _enqueue(outbox, "three")

    assert outbox.pending_count() == 2
    assert outbox.state_for("command", "three") is None


def test_outbox_rejects_oversized_payload_and_rolls_back_write_failure(
    tmp_path, monkeypatch
) -> None:
    tmp_path.chmod(0o700)
    outbox = OutcomeOutbox(tmp_path)
    with pytest.raises(OutcomeOutboxError, match="64 KiB"):
        outbox.enqueue(
            kind="action",
            subject_id="large",
            state="accepted",
            event="robot:action-result",
            data={"value": "x" * 70_000},
        )
    monkeypatch.setattr(outbox, "_persist", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        _enqueue(outbox, "disk-full")
    assert outbox.pending_count() == 0
    assert outbox.state_for("command", "disk-full") is None


def test_outbox_constant_is_large_enough_for_reconnect_bursts() -> None:
    assert MAX_OUTBOX_ENTRIES >= 2_048


def test_outbox_handles_duplicate_invalid_and_empty_operations(tmp_path, monkeypatch) -> None:
    tmp_path.chmod(0o700)
    outbox = OutcomeOutbox(tmp_path)
    assert outbox.oldest_pending_age_seconds() == 0.0
    accepted = _enqueue(outbox, "one")
    assert accepted is not None
    assert _enqueue(outbox, "one") is None
    with pytest.raises(OutcomeOutboxError, match="unsupported outcome state"):
        _enqueue(outbox, "two", "running")
    outbox.mark_attempted("unknown")
    outbox.mark_confirmed("unknown")

    monkeypatch.setattr(outbox, "_persist", lambda: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        outbox.mark_confirmed(accepted.outcome_id)
    assert outbox.pending_count() == 1


def test_outbox_rejects_non_private_state_directory(tmp_path) -> None:
    tmp_path.chmod(0o755)
    with pytest.raises(OutcomeOutboxError, match="private and service-owned"):
        OutcomeOutbox(tmp_path)
