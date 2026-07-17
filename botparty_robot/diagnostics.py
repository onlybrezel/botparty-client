"""Bounded, acknowledged diagnostics upload service."""

from __future__ import annotations

import json
import time
from collections import deque
from collections.abc import Callable

import aiohttp

from .client_state import DiagnosticRecord
from .config import DiagnosticsConfig


class DiagnosticsUploader:
    def __init__(
        self,
        *,
        config: DiagnosticsConfig,
        api_url: str,
        records: deque[DiagnosticRecord],
        session: Callable[[], aiohttp.ClientSession],
        auth_token: Callable[[], str | None],
        client_id: Callable[[], str | None],
    ) -> None:
        self._config = config
        self._api_url = api_url.rstrip("/")
        self._records = records
        self._session = session
        self._auth_token = auth_token
        self._client_id = client_id
        self.acked_sequence = 0

    async def upload_once(self, enabled_until: float) -> bool:
        if not self._config.upload_enabled or time.time() >= enabled_until:
            return False
        token = (self._auth_token() or "").strip()
        if not token:
            return False
        cutoff = time.time() - self._config.retention_sec
        while self._records and self._records[0].created_at < cutoff:
            self._records.popleft()
        pending = [record for record in self._records if record.sequence > self.acked_sequence]
        if not pending:
            return False
        batch = pending[: self._config.batch_lines]
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "clientId": self._client_id(),
            "sequenceStart": batch[0].sequence,
            "sequenceEnd": batch[-1].sequence,
            "lines": [record.line for record in batch],
        }
        acknowledged = await self._post(self._session(), headers, payload)
        if acknowledged is None:
            return False
        self.acked_sequence = max(self.acked_sequence, acknowledged)
        while self._records and self._records[0].sequence <= self.acked_sequence:
            self._records.popleft()
        return True

    async def _post(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> int | None:
        endpoint = f"{self._api_url}/api/v1/robots/logs"
        async with session.post(
            endpoint,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            if response.status == 400:
                return await self._post_legacy(session, headers, payload)
            if response.status not in (200, 201, 204):
                return None
            return await self._acknowledged_sequence(response, payload)

    async def _post_legacy(
        self,
        session: aiohttp.ClientSession,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> int | None:
        lines = payload.get("lines")
        endpoint = f"{self._api_url}/api/v1/robots/logs"
        async with session.post(
            endpoint,
            json={"lines": lines},
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=5),
        ) as response:
            if response.status not in (200, 201, 204):
                return None
            sequence_end = payload.get("sequenceEnd")
            return sequence_end if isinstance(sequence_end, int) else None

    async def _acknowledged_sequence(
        self,
        response: aiohttp.ClientResponse,
        payload: dict[str, object],
    ) -> int:
        sequence_start_value = payload.get("sequenceStart")
        sequence_end_value = payload.get("sequenceEnd")
        if not isinstance(sequence_start_value, int) or not isinstance(sequence_end_value, int):
            raise ValueError("diagnostics batch has invalid sequence bounds")
        sequence_start = sequence_start_value
        sequence_end = sequence_end_value
        if response.content_type == "application/json":
            raw = await response.content.read(4_097)
            if len(raw) > 4_096:
                raise ValueError("diagnostics acknowledgement exceeds 4 KiB")
            body = json.loads(raw)
            if isinstance(body, dict):
                acknowledgement: object = body.get("ackSequence")
                if isinstance(acknowledgement, int):
                    if acknowledgement < sequence_start or acknowledgement > sequence_end:
                        raise ValueError("diagnostics acknowledgement is outside the batch")
                    return acknowledgement
        return sequence_end
