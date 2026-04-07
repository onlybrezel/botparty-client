"""Direct FFmpeg publisher for LiveKit WHIP ingress."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Callable, Optional

from .config import RobotConfig

logger = logging.getLogger("botparty.camera")


class WhipPublisherManager:
    def __init__(
        self,
        config: RobotConfig,
        video_profile,
        *,
        ingress_info_fn: Callable[[], dict[str, object] | None],
        camera_id: str = "front",
    ) -> None:
        self.config = config
        self.video_profile = video_profile
        self.camera_id = camera_id
        self._ingress_info_fn = ingress_info_fn
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
        ingress = self._ingress_info_fn() or {}
        publish_url = str(ingress.get("endpointUrl") or ingress.get("url") or "").strip()
        stream_key = str(ingress.get("streamKey") or "").strip()
        if not publish_url:
            raise RuntimeError("WHIP ingress publish URL is missing from the claim response")

        if stream_key:
            publish_url = self.video_profile.build_whip_publish_url(publish_url, stream_key)

        proc = None
        stderr_task = None

        try:
            proc = await self.video_profile.spawn_whip_process(publish_url, target_bitrate_kbps)
            logger.info(
                "Publishing %s directly to WHIP ingress: device=%s resolution=%dx%d fps=%d publish_fps=%.1f",
                self.camera_id,
                self.config.camera.device,
                self.config.camera.width,
                self.config.camera.height,
                self.config.camera.fps,
                self.video_profile.output_fps(),
            )

            if proc.stderr is not None:
                stderr_task = asyncio.create_task(self._drain_stderr(proc.stderr))

            while running_fn() and connected_fn():
                return_code = proc.returncode
                if return_code is not None:
                    if return_code == 0:
                        return
                    raise RuntimeError(f"WHIP ffmpeg process exited with code {return_code}")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            if stderr_task is not None:
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("WHIP ffmpeg did not exit after SIGTERM; killing")
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=2)

    async def _drain_stderr(self, stderr) -> None:
        while True:
            line = await stderr.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                logger.warning("whip-ffmpeg: %s", message)
