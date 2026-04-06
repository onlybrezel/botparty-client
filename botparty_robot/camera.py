"""Camera pipeline for BotParty robot client."""

import asyncio
import contextlib
import logging
import re
import time
from typing import Callable, Optional

from livekit import rtc

from .config import RobotConfig

logger = logging.getLogger("botparty.camera")

CAMERA_BACKEND_MAP = {
    "auto": None,
    "any": None,
    "v4l2": "CAP_V4L2",
    "gstreamer": "CAP_GSTREAMER",
    "ffmpeg": "CAP_FFMPEG",
}


class CameraManager:
    """Manages camera capture and publishing to a LiveKit room."""

    def __init__(self, config: RobotConfig, video_profile) -> None:
        self.config = config
        self.video_profile = video_profile
        self._frame_count = 0
        self._audio_task: Optional[asyncio.Task] = None

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def audio_task(self) -> Optional[asyncio.Task]:
        return self._audio_task

    async def run(
        self,
        room: rtc.Room,
        target_bitrate_kbps: Optional[int],
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        """Full camera pipeline. Designed to run as an asyncio.Task."""
        cap = None
        try:
            mode = self._pipeline_mode()
            if mode == "none":
                logger.info("Camera disabled by profile: %s", self.config.video.type)
                await self.video_profile.run_disabled(running_fn)
                return

            if mode in {"ffmpeg", "sdk"}:
                frame_width = self.config.camera.width
                frame_height = self.config.camera.height
                camera_fps = float(self.config.camera.fps)
            else:
                cap, frame_width, frame_height, camera_fps = self._open_camera()

            source = rtc.VideoSource(frame_width, frame_height)
            track = rtc.LocalVideoTrack.create_video_track("camera", source)
            publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)

            if target_bitrate_kbps:
                try:
                    publish_options.video_encoding = rtc.VideoEncoding(
                        max_bitrate=target_bitrate_kbps * 1000,
                        max_framerate=int(round(camera_fps)),
                    )
                    logger.info("Applying target bitrate: %d kbps", target_bitrate_kbps)
                except Exception:
                    logger.warning(
                        "Current LiveKit SDK does not expose publish bitrate controls; "
                        "using default encoder settings"
                    )

            await room.local_participant.publish_track(track, publish_options)
            logger.info("Camera track published")

            if self.video_profile.has_audio():
                self._audio_task = asyncio.create_task(
                    self.video_profile.start_audio(rtc, room, running_fn)
                )

            if mode == "ffmpeg":
                await self._loop_ffmpeg(source, frame_width, frame_height, camera_fps, running_fn, connected_fn)
            elif mode == "sdk":
                await self.video_profile.capture_sdk_frames(
                    rtc, source, running_fn, lambda: self._inc_frame()
                )
            else:
                await self._loop_cv2(cap, source, frame_width, frame_height, camera_fps, running_fn, connected_fn)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Camera pipeline error: %s", e)
            raise
        finally:
            if cap is not None:
                cap.release()
                logger.info("Camera released")
            if self._audio_task is not None and not self._audio_task.done():
                self._audio_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._audio_task
                self._audio_task = None

    def restart_audio(self, room: rtc.Room, running_fn: Callable[[], bool]) -> asyncio.Task:
        """(Re)start the audio task and return it. Caller is responsible for storing the ref."""
        task = asyncio.create_task(self.video_profile.start_audio(rtc, room, running_fn))
        self._audio_task = task
        return task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _inc_frame(self) -> None:
        self._frame_count += 1

    def _pipeline_mode(self) -> str:
        return self.video_profile.capture_mode().strip().lower()

    def _open_camera(self):
        import cv2

        device = self._resolve_device()
        backend_flag = self._resolve_backend(cv2)
        cap = (
            cv2.VideoCapture(device, backend_flag)
            if backend_flag is not None
            else cv2.VideoCapture(device)
        )
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera: {self.config.camera.device}")

        self._configure_capture(cap, cv2)

        for _ in range(self.config.camera.warmup_frames):
            with contextlib.suppress(Exception):
                cap.read()

        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.config.camera.width
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.config.camera.height
        camera_fps = cap.get(cv2.CAP_PROP_FPS)
        if not camera_fps or camera_fps < 1:
            camera_fps = float(self.config.camera.fps)

        backend_name = "default"
        with contextlib.suppress(Exception):
            backend_name = cap.getBackendName() or backend_name

        logger.info(
            "Camera opened: device=%s backend=%s resolution=%dx%d fps=%.1f requested=%dx%d@%dfps",
            self.config.camera.device,
            backend_name,
            frame_width,
            frame_height,
            camera_fps,
            self.config.camera.width,
            self.config.camera.height,
            self.config.camera.fps,
        )
        return cap, frame_width, frame_height, camera_fps

    def _resolve_device(self):
        device = self.config.camera.device.strip()
        match = re.fullmatch(r"/dev/video(\d+)", device)
        if match:
            return int(match.group(1))
        return device

    def _configure_capture(self, cap, cv2) -> None:
        if self.config.camera.fourcc:
            fourcc = self.config.camera.fourcc.strip().upper()
            if len(fourcc) == 4:
                with contextlib.suppress(Exception):
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.config.camera.buffer_size)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.camera.width)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camera.height)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FPS, self.config.camera.fps)

    def _resolve_backend(self, cv2):
        attr = CAMERA_BACKEND_MAP.get(self.config.camera.backend.strip().lower())
        if attr is None:
            return None
        return getattr(cv2, attr, None)

    async def _loop_ffmpeg(
        self,
        source: rtc.VideoSource,
        frame_width: int,
        frame_height: int,
        camera_fps: float,
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        frame_bytes = frame_width * frame_height * 4
        proc = None
        frames_since_report = 0
        report_started_at = time.monotonic()
        stderr_task = None
        frame_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=1)
        reader_task = None
        reader_error: Exception | None = None
        dropped_for_pacing = 0

        requested_publish_fps = float(self.video_profile.options.get("publish_fps", camera_fps))
        publish_fps = min(camera_fps, max(5.0, requested_publish_fps)) if camera_fps > 0 else max(5.0, requested_publish_fps)
        publish_interval = 1.0 / publish_fps if publish_fps > 0 else 1.0 / max(self.config.camera.fps, 1)
        next_publish_at = time.monotonic()

        try:
            proc = await self.video_profile.spawn_ffmpeg_process()
            logger.info(
                "Camera opened via %s: device=%s resolution=%dx%d fps=%.1f publish_fps=%.1f format=%s",
                self.config.video.type,
                self.config.camera.device,
                frame_width,
                frame_height,
                camera_fps,
                publish_fps,
                (self.config.camera.fourcc or "auto").upper(),
            )

            if proc.stderr is not None:
                stderr_task = asyncio.create_task(self._drain_stderr(proc.stderr))
            if proc.stdout is None:
                raise RuntimeError("ffmpeg process has no stdout pipe")

            async def read_frames() -> None:
                nonlocal reader_error

                try:
                    while True:
                        frame = await proc.stdout.readexactly(frame_bytes)
                        if frame_queue.full():
                            with contextlib.suppress(asyncio.QueueEmpty):
                                frame_queue.get_nowait()
                        frame_queue.put_nowait(frame)
                except asyncio.IncompleteReadError as exc:
                    if exc.partial:
                        reader_error = RuntimeError(
                            f"ffmpeg stream ended early ({len(exc.partial)} of {frame_bytes} bytes)"
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    reader_error = exc
                finally:
                    if frame_queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            frame_queue.get_nowait()
                    with contextlib.suppress(asyncio.QueueFull):
                        frame_queue.put_nowait(None)

            reader_task = asyncio.create_task(read_frames())

            while running_fn() and connected_fn():
                frame = await frame_queue.get()
                if frame is None:
                    break

                now = time.monotonic()
                if now < next_publish_at:
                    dropped_for_pacing += 1
                    continue

                if now - next_publish_at > publish_interval * 2:
                    next_publish_at = now

                lk_frame = rtc.VideoFrame(frame_width, frame_height, rtc.VideoBufferType.RGBA, frame)
                source.capture_frame(lk_frame)
                self._inc_frame()
                frames_since_report += 1
                next_publish_at += publish_interval

                # Keep the reader task moving so old frames are discarded instead of queued.
                await asyncio.sleep(0)

                elapsed = now - report_started_at
                if elapsed >= 10:
                    logger.info(
                        "Camera runtime: sent_fps=%.1f target_fps=%.1f dropped_pacing=%d resolution=%dx%d",
                        frames_since_report / elapsed,
                        publish_fps,
                        dropped_for_pacing,
                        frame_width,
                        frame_height,
                    )
                    frames_since_report = 0
                    dropped_for_pacing = 0
                    report_started_at = now

            if reader_error is not None:
                raise reader_error

            if proc.returncode not in (None, 0):
                raise RuntimeError(f"ffmpeg process exited with code {proc.returncode}")

        except asyncio.IncompleteReadError as exc:
            raise RuntimeError(
                f"ffmpeg stream ended early ({len(exc.partial)} of {frame_bytes} bytes)"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg not found; install it or switch video.type to 'opencv'"
            ) from exc
        finally:
            if reader_task is not None:
                reader_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader_task
            if proc is not None:
                await asyncio.shield(self._shutdown_process(proc, "ffmpeg"))
            if stderr_task is not None:
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    async def _shutdown_process(self, proc, name: str) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()

        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            logger.warning("%s did not exit after SIGTERM; killing", name)
        except asyncio.CancelledError:
            # Cleanup must continue even if the owning task was cancelled.
            pass

        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(proc.wait(), timeout=2)

    async def _drain_stderr(self, stderr) -> None:
        while True:
            line = await stderr.readline()
            if not line:
                return
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                logger.warning("ffmpeg: %s", msg)

    async def _loop_cv2(
        self,
        cap,
        source: rtc.VideoSource,
        frame_width: int,
        frame_height: int,
        camera_fps: float,
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        try:
            import cv2
        except ImportError:
            logger.error("OpenCV not installed: pip install opencv-python-headless")
            return

        interval = 1.0 / camera_fps if camera_fps > 0 else 1.0 / max(self.config.camera.fps, 1)
        next_frame_at = time.monotonic()
        consecutive_failures = 0
        frames_since_report = 0
        report_started_at = time.monotonic()

        while running_fn() and connected_fn():
            ret, frame = await asyncio.to_thread(cap.read)
            if not ret:
                consecutive_failures += 1
                logger.warning("Camera read failure #%d", consecutive_failures)
                if consecutive_failures >= 30:
                    logger.error("Camera failed 30 times - aborting loop")
                    break
                await asyncio.sleep(0.1)
                continue

            consecutive_failures = 0
            frame_rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            frame_rgba = self.video_profile.transform_rgba(frame_rgba, frame_width, frame_height)
            lk_frame = rtc.VideoFrame(
                frame_width, frame_height, rtc.VideoBufferType.RGBA, frame_rgba.tobytes()
            )
            source.capture_frame(lk_frame)
            self._inc_frame()
            frames_since_report += 1

            now = time.monotonic()
            elapsed = now - report_started_at
            if elapsed >= 10:
                logger.info(
                    "Camera runtime: sent_fps=%.1f target_fps=%.1f resolution=%dx%d",
                    frames_since_report / elapsed,
                    camera_fps,
                    frame_width,
                    frame_height,
                )
                frames_since_report = 0
                report_started_at = now

            next_frame_at += interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                next_frame_at = time.monotonic()
