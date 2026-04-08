"""External publisher manager for direct low-latency video."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Callable, Optional

from .config import RobotConfig

logger = logging.getLogger("botparty.camera")


class LiveKitPublisherManager:
    def __init__(
        self,
        config: RobotConfig,
        video_profile,
        *,
        token_fn: Callable[[], str | None],
        livekit_url_fn: Callable[[], str | None],
        camera_id: str = "front",
    ) -> None:
        self.config = config
        self.video_profile = video_profile
        self.camera_id = camera_id
        self._token_fn = token_fn
        self._livekit_url_fn = livekit_url_fn
        self._audio_task: Optional[asyncio.Task] = None

    @property
    def frame_count(self) -> int:
        return 0

    @property
    def audio_task(self) -> Optional[asyncio.Task]:
        return self._audio_task

    def restart_audio(self, room, running_fn):
        return None

    async def run(
        self,
        room,
        target_bitrate_kbps: Optional[int],
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        livekit_url = str(self._livekit_url_fn() or "").strip()
        token = str(self._token_fn() or "").strip()
        if not livekit_url or not token:
            raise RuntimeError("LiveKit publish URL or token is missing")

        proc = None
        log_task = None

        try:
            proc = await self.video_profile.spawn_livekit_process(
                livekit_url=livekit_url,
                token=token,
                target_bitrate_kbps=target_bitrate_kbps,
            )
            logger.info(
                "Publishing %s directly: device=%s resolution=%dx%d fps=%d publish_fps=%.1f",
                self.camera_id,
                self.config.camera.device,
                self.config.camera.width,
                self.config.camera.height,
                self.config.camera.fps,
                self.video_profile.output_fps(),
            )

            log_stream = proc.stderr or proc.stdout
            if log_stream is not None:
                log_task = asyncio.create_task(self._drain_logs(log_stream))

            while running_fn() and connected_fn():
                return_code = proc.returncode
                if return_code is not None:
                    if return_code == 0:
                        return
                    raise RuntimeError(f"publisher exited with code {return_code}")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            if log_task is not None:
                log_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await log_task
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("publisher did not exit after SIGTERM; killing")
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=2)

    async def _drain_logs(self, stream) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                logger.warning("video: %s", message)
