"""Small durable outcome ledger and delivery outbox."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_OUTBOX_ENTRIES = 2048
MAX_OUTCOME_BYTES = 64 * 1024
TERMINAL_STATES = frozenset({"completed", "rejected", "failed", "superseded", "cancelled_by_stop"})


class OutcomeOutboxError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    outcome_id: str
    kind: str
    subject_id: str
    state: str
    event: str
    data: dict[str, Any]
    created_at_ms: int


class OutcomeOutbox:
    """Persist monotone outcomes before attempting websocket delivery."""

    def __init__(self, state_directory: Path) -> None:
        self._path = state_directory / "outcomes.json"
        self._pending: dict[str, OutcomeRecord] = {}
        self._states: dict[str, str] = {}
        self._attempted: set[str] = set()
        state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = state_directory.stat()
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
            raise OutcomeOutboxError("outcome state directory must be private and service-owned")
        self._load()

    @staticmethod
    def _subject_key(kind: str, subject_id: str) -> str:
        return f"{kind}:{subject_id}"

    @staticmethod
    def _outcome_id(kind: str, subject_id: str, state: str) -> str:
        value = f"{kind}\0{subject_id}\0{state}".encode()
        return hashlib.sha256(value).hexdigest()

    def state_for(self, kind: str, subject_id: str) -> str | None:
        return self._states.get(self._subject_key(kind, subject_id))

    def enqueue(
        self,
        *,
        kind: str,
        subject_id: str,
        state: str,
        event: str,
        data: dict[str, Any],
    ) -> OutcomeRecord | None:
        key = self._subject_key(kind, subject_id)
        previous = self._states.get(key)
        if previous in TERMINAL_STATES:
            return None
        if state == "accepted" and previous == "accepted":
            return None
        if previous is not None and previous != "accepted":
            raise OutcomeOutboxError(f"invalid outcome transition {previous} -> {state}")
        if state not in TERMINAL_STATES and state != "accepted":
            raise OutcomeOutboxError(f"unsupported outcome state: {state}")
        outcome_id = self._outcome_id(kind, subject_id, state)
        payload = dict(data)
        payload["outcomeId"] = outcome_id
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > MAX_OUTCOME_BYTES:
            raise OutcomeOutboxError("outcome payload exceeds 64 KiB")
        record = OutcomeRecord(
            outcome_id, kind, subject_id, state, event, payload, int(time.time() * 1000)
        )
        previous_states = self._states.copy()
        previous_pending = self._pending.copy()
        try:
            self._states[key] = state
            self._pending[outcome_id] = record
            self._trim()
            self._persist()
        except Exception:
            self._states = previous_states
            self._pending = previous_pending
            raise
        return record

    def pending(self) -> tuple[OutcomeRecord, ...]:
        return tuple(self._pending.values())

    def ready_for_delivery(self) -> tuple[OutcomeRecord, ...]:
        return tuple(
            record
            for outcome_id, record in self._pending.items()
            if outcome_id not in self._attempted
        )

    def mark_attempted(self, outcome_id: str) -> None:
        if outcome_id in self._pending:
            self._attempted.add(outcome_id)

    def reset_delivery_attempts(self) -> None:
        self._attempted.clear()

    def pending_count(self) -> int:
        return len(self._pending)

    def oldest_pending_age_seconds(self) -> float:
        if not self._pending:
            return 0.0
        oldest = min(record.created_at_ms for record in self._pending.values())
        return max(0.0, (time.time() * 1000 - oldest) / 1000)

    def mark_confirmed(self, outcome_id: str) -> None:
        record = self._pending.pop(outcome_id, None)
        if record is None:
            return
        self._attempted.discard(outcome_id)
        try:
            self._persist()
        except Exception:
            self._pending[outcome_id] = record
            raise

    def _trim(self) -> None:
        if len(self._states) > MAX_OUTBOX_ENTRIES:
            terminal_keys = [key for key, state in self._states.items() if state in TERMINAL_STATES]
            for key in terminal_keys[: len(self._states) - MAX_OUTBOX_ENTRIES]:
                self._states.pop(key, None)
        if len(self._pending) > MAX_OUTBOX_ENTRIES:
            raise OutcomeOutboxError("outcome outbox capacity is exhausted")

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            metadata = self._path.lstat()
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise OutcomeOutboxError("outcome outbox must be private and service-owned")
            raw = self._path.read_bytes()
            if len(raw) > 4 * 1024 * 1024:
                raise OutcomeOutboxError("outcome outbox exceeds 4 MiB")
            payload = json.loads(raw)
            states = payload["states"]
            pending = payload["pending"]
            if not isinstance(states, dict) or not isinstance(pending, list):
                raise ValueError
            self._states = {str(key): str(value) for key, value in states.items()}
            self._pending = {str(item["outcome_id"]): OutcomeRecord(**item) for item in pending}
            if len(self._states) > MAX_OUTBOX_ENTRIES or len(self._pending) > MAX_OUTBOX_ENTRIES:
                raise OutcomeOutboxError("outcome outbox entry limit is exceeded")
            for key, state in self._states.items():
                if ":" not in key or (state != "accepted" and state not in TERMINAL_STATES):
                    raise ValueError
            for outcome_id, record in self._pending.items():
                key = self._subject_key(record.kind, record.subject_id)
                current_state = self._states.get(key)
                if (
                    not record.kind
                    or not record.subject_id
                    or (
                        record.state != current_state
                        and not (record.state == "accepted" and current_state in TERMINAL_STATES)
                    )
                    or (record.state != "accepted" and record.state not in TERMINAL_STATES)
                    or outcome_id != self._outcome_id(record.kind, record.subject_id, record.state)
                    or record.data.get("outcomeId") != outcome_id
                    or record.outcome_id != outcome_id
                    or record.created_at_ms < 0
                ):
                    raise ValueError
                encoded = json.dumps(record.data, sort_keys=True, separators=(",", ":")).encode()
                if len(encoded) > MAX_OUTCOME_BYTES:
                    raise OutcomeOutboxError("outcome payload exceeds 64 KiB")
        except OutcomeOutboxError:
            raise
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OutcomeOutboxError("outcome outbox is corrupt") from exc

    def _persist(self) -> None:
        payload = {
            "schemaVersion": 1,
            "states": self._states,
            "pending": [asdict(record) for record in self._pending.values()],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self._path.parent, prefix=f".{self._path.name}."
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
            directory_fd = os.open(self._path.parent, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise OutcomeOutboxError("could not persist outcome outbox") from exc
        finally:
            temporary.unlink(missing_ok=True)
