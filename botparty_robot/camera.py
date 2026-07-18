"""Camera pipeline for BotParty robot client."""

import asyncio
import concurrent.futures
import contextlib
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from livekit import rtc

from .config import RobotConfig
from .process_group import terminate_async_process
from .video.base import BaseVideoProfile, MediaProcess

logger = logging.getLogger("botparty.camera")

CAMERA_BACKEND_MAP = {
    "auto": None,
    "any": None,
    "v4l2": "CAP_V4L2",
    "ffmpeg": "CAP_FFMPEG",
}


class _OpenCVCaptureWorker:
    """Own one OpenCV capture and bridge its blocking reads into asyncio."""

    def __init__(self, cap: Any, *, warmup_frames: int, camera_id: str) -> None:
        self._cap = cap
        self._warmup_frames = warmup_frames
        self._loop = asyncio.get_running_loop()
        self._frames: asyncio.Queue[tuple[bool, Any]] = asyncio.Queue(maxsize=1)
        self._stop = threading.Event()
        self._finished = asyncio.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"botparty-camera-{camera_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _mark_finished(self) -> None:
        self._finished.set()

    def _publish(self, result: tuple[bool, Any]) -> bool:
        try:
            delivery = asyncio.run_coroutine_threadsafe(self._frames.put(result), self._loop)
        except RuntimeError:
            return False
        while not self._stop.is_set():
            try:
                delivery.result(timeout=0.2)
                return True
            except concurrent.futures.TimeoutError:
                continue
            except (concurrent.futures.CancelledError, RuntimeError):
                return False
        delivery.cancel()
        return False

    def _run(self) -> None:
        try:
            for _ in range(self._warmup_frames):
                if self._stop.is_set():
                    return
                self._cap.read()
            while not self._stop.is_set():
                result = self._cap.read()
                if not self._publish(result):
                    return
        finally:
            with contextlib.suppress(Exception):
                self._cap.release()
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._mark_finished)

    async def read(self) -> tuple[bool, Any]:
        return await self._frames.get()

    async def close(self) -> None:
        self._stop.set()
        try:
            await asyncio.wait_for(self._finished.wait(), timeout=1.2)
        except asyncio.TimeoutError:
            # Some V4L2/OpenCV backends wake a blocked read only when the descriptor closes.
            with contextlib.suppress(Exception):
                self._cap.release()
            try:
                await asyncio.wait_for(self._finished.wait(), timeout=0.5)
            except asyncio.TimeoutError as exc:
                raise RuntimeError("camera capture owner did not stop within deadline") from exc
        self._thread.join(timeout=0.1)
        if self._thread.is_alive():
            raise RuntimeError("camera capture owner remained alive after release")


class CameraManager:
    """Manages camera capture and publishing to a LiveKit room."""

    def __init__(
        self,
        config: RobotConfig,
        video_profile: BaseVideoProfile,
        *,
        track_name: str = "camera",
        audio_enabled: bool = True,
        camera_id: str = "front",
    ) -> None:
        self.config = config
        self.video_profile = video_profile
        self.track_name = track_name
        self.audio_enabled = audio_enabled
        self.camera_id = camera_id
        self._frame_count = 0
        self._video_track_published = False
        self._audio_task: asyncio.Task[None] | None = None

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def video_track_published(self) -> bool:
        return self._video_track_published

    @property
    def audio_task(self) -> asyncio.Task[None] | None:
        return self._audio_task

    async def run(
        self,
        room: rtc.Room | None,
        target_bitrate_kbps: int | None,
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        """Full camera pipeline. Designed to run as an asyncio.Task."""
        if room is None:
            raise RuntimeError("LiveKit room is required for the camera publisher")
        cap = None
        capture_handed_off = False
        self._video_track_published = False
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
            track = rtc.LocalVideoTrack.create_video_track(self.track_name, source)
            publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)

            if target_bitrate_kbps:
                try:
                    publish_options.video_encoding.CopyFrom(
                        rtc.VideoEncoding(
                            max_bitrate=target_bitrate_kbps * 1000,
                            max_framerate=round(camera_fps),
                        )
                    )
                    logger.info("Applying target bitrate: %d kbps", target_bitrate_kbps)
                except Exception:
                    logger.warning(
                        "Current LiveKit SDK does not expose publish bitrate controls; "
                        "using default encoder settings"
                    )

            await room.local_participant.publish_track(track, publish_options)
            self._video_track_published = True
            logger.info("Camera track published: id=%s track=%s", self.camera_id, self.track_name)

            if self.audio_enabled and self.video_profile.has_audio():
                self._audio_task = asyncio.create_task(
                    self.video_profile.start_audio(rtc, room, running_fn)
                )

            if mode == "ffmpeg":
                await self._loop_ffmpeg(
                    source, frame_width, frame_height, camera_fps, running_fn, connected_fn
                )
            elif mode == "sdk":
                await self.video_profile.capture_sdk_frames(
                    rtc, source, running_fn, lambda: self._inc_frame()
                )
            else:
                capture_handed_off = True
                await self._loop_cv2(
                    cap, source, frame_width, frame_height, camera_fps, running_fn, connected_fn
                )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Camera pipeline error: %s", e)
            raise
        finally:
            self._video_track_published = False
            if cap is not None and not capture_handed_off:
                cap.release()
                logger.info("Camera released")
            if self._audio_task is not None and not self._audio_task.done():
                self._audio_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._audio_task
                self._audio_task = None

    def restart_audio(self, room: rtc.Room, running_fn: Callable[[], bool]) -> asyncio.Task[None]:
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
        return str(self.video_profile.capture_mode()).strip().lower()

    def _open_camera(self) -> tuple[Any, int, int, float]:
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
        return cap, frame_width, frame_height, float(camera_fps)

    def _resolve_device(self) -> str | int:
        device = self.config.camera.device
        if isinstance(device, int):
            return device
        device = str(device).strip()
        if device.isdigit():
            return int(device)
        match = re.fullmatch(r"/dev/video(\d+)", device)
        if match:
            return int(match.group(1))
        return device

    def _configure_capture(self, cap: Any, cv2: Any) -> None:
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
        for attribute in ("CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC"):
            timeout_property = getattr(cv2, attribute, None)
            if timeout_property is not None:
                with contextlib.suppress(Exception):
                    cap.set(timeout_property, 1000)

    def _resolve_backend(self, cv2: Any) -> Any | None:
        attr = CAMERA_BACKEND_MAP.get(self.config.camera.backend.strip().lower())
        if attr is None:
            return None
        return getattr(cv2, attr, None)

    def _update_adaptive_publish_rate(
        self,
        *,
        now: float,
        next_publish_at: float,
        publish_interval: float,
        lag_overruns: int,
        effective_publish_fps: float,
        min_publish_fps: float,
        publish_fps: float,
        stable_since: float,
    ) -> tuple[float, float, int, float, float]:
        lag = now - next_publish_at
        if lag > publish_interval * 1.5:
            lag_overruns += 1
        elif lag_overruns > 0:
            lag_overruns -= 1

        if lag_overruns >= 8 and effective_publish_fps > min_publish_fps:
            previous_fps = effective_publish_fps
            effective_publish_fps = max(min_publish_fps, effective_publish_fps - 2.0)
            publish_interval = 1.0 / effective_publish_fps
            lag_overruns = 0
            stable_since = now
            logger.info(
                "Adaptive performance cap: publish_fps %.1f -> %.1f for %s",
                previous_fps,
                effective_publish_fps,
                self.camera_id,
            )
        elif (
            effective_publish_fps < publish_fps
            and now - stable_since >= 12.0
            and lag <= publish_interval * 0.5
        ):
            previous_fps = effective_publish_fps
            effective_publish_fps = min(publish_fps, effective_publish_fps + 1.0)
            publish_interval = 1.0 / effective_publish_fps
            stable_since = now
            logger.info(
                "Adaptive performance recovery: publish_fps %.1f -> %.1f for %s",
                previous_fps,
                effective_publish_fps,
                self.camera_id,
            )

        if lag > publish_interval * 2:
            next_publish_at = now

        return next_publish_at, publish_interval, lag_overruns, effective_publish_fps, stable_since

    def _maybe_log_ffmpeg_runtime(
        self,
        *,
        now: float,
        report_started_at: float,
        frames_since_report: int,
        effective_publish_fps: float,
        dropped_for_pacing: int,
        frame_width: int,
        frame_height: int,
    ) -> tuple[float, int, int]:
        elapsed = now - report_started_at
        if elapsed < 10:
            return report_started_at, frames_since_report, dropped_for_pacing

        logger.info(
            "Camera runtime: sent_fps=%.1f target_fps=%.1f dropped_pacing=%d resolution=%dx%d",
            frames_since_report / elapsed,
            effective_publish_fps,
            dropped_for_pacing,
            frame_width,
            frame_height,
        )
        return now, 0, 0

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
        reader_task = None
        reader_error: Exception | None = None
        dropped_for_pacing = 0
        latest_frame: bytes | None = None
        latest_frame_seq = 0
        consumed_frame_seq = 0
        frame_ready = asyncio.Event()
        stream_ended = False
        frames_since_yield = 0

        requested_publish_fps = float(self.video_profile.output_fps())
        publish_fps = (
            min(camera_fps, max(5.0, requested_publish_fps))
            if camera_fps > 0
            else max(5.0, requested_publish_fps)
        )
        effective_publish_fps = publish_fps
        publish_interval = (
            1.0 / effective_publish_fps
            if effective_publish_fps > 0
            else 1.0 / max(self.config.camera.fps, 1)
        )
        next_publish_at = time.monotonic()
        lag_overruns = 0
        stable_since = next_publish_at
        min_publish_fps = max(8.0, publish_fps * 0.6)

        try:
            proc = await self.video_profile.spawn_ffmpeg_process()
            logger.info(
                "Camera opened via %s: device=%s resolution=%dx%d "
                "fps=%.1f publish_fps=%.1f format=%s",
                self.config.video.type,
                self.config.camera.device,
                frame_width,
                frame_height,
                camera_fps,
                publish_fps,
                (self.config.camera.fourcc or "auto").upper(),
            )

            if publish_fps < camera_fps:
                logger.info(
                    "Performance cap active: camera_fps=%.1f publish_fps=%.1f for %s",
                    camera_fps,
                    publish_fps,
                    self.camera_id,
                )

            if proc.stderr is not None:
                stderr_task = asyncio.create_task(self._drain_stderr(proc.stderr))
            stdout = proc.stdout
            if stdout is None:
                raise RuntimeError("ffmpeg process has no stdout pipe")

            async def read_frames() -> None:
                nonlocal latest_frame, latest_frame_seq, reader_error, stream_ended

                try:
                    while True:
                        frame = await stdout.readexactly(frame_bytes)
                        latest_frame = frame
                        latest_frame_seq += 1
                        frame_ready.set()
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
                    stream_ended = True
                    latest_frame = None
                    frame_ready.set()

            reader_task = asyncio.create_task(read_frames())

            while running_fn() and connected_fn():
                if consumed_frame_seq == latest_frame_seq:
                    frame_ready.clear()
                    if consumed_frame_seq == latest_frame_seq and not stream_ended:
                        await frame_ready.wait()

                if consumed_frame_seq == latest_frame_seq and stream_ended:
                    break

                frame = latest_frame
                consumed_frame_seq = latest_frame_seq
                if frame is None:
                    if stream_ended:
                        break
                    continue

                now = time.monotonic()
                if now < next_publish_at:
                    dropped_for_pacing += 1
                    continue

                (
                    next_publish_at,
                    publish_interval,
                    lag_overruns,
                    effective_publish_fps,
                    stable_since,
                ) = self._update_adaptive_publish_rate(
                    now=now,
                    next_publish_at=next_publish_at,
                    publish_interval=publish_interval,
                    lag_overruns=lag_overruns,
                    effective_publish_fps=effective_publish_fps,
                    min_publish_fps=min_publish_fps,
                    publish_fps=publish_fps,
                    stable_since=stable_since,
                )

                lk_frame = rtc.VideoFrame(
                    frame_width, frame_height, rtc.VideoBufferType.RGBA, frame
                )
                source.capture_frame(lk_frame)
                self._inc_frame()
                frames_since_report += 1
                frames_since_yield += 1
                next_publish_at += publish_interval

                if frames_since_yield >= 4:
                    frames_since_yield = 0
                    await asyncio.sleep(0)

                report_started_at, frames_since_report, dropped_for_pacing = (
                    self._maybe_log_ffmpeg_runtime(
                        now=now,
                        report_started_at=report_started_at,
                        frames_since_report=frames_since_report,
                        effective_publish_fps=effective_publish_fps,
                        dropped_for_pacing=dropped_for_pacing,
                        frame_width=frame_width,
                        frame_height=frame_height,
                    )
                )

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

    async def _shutdown_process(self, proc: MediaProcess, name: str) -> None:
        if not await terminate_async_process(proc):
            logger.error("%s process group could not be reaped after SIGKILL", name)

    async def _drain_stderr(self, stderr: asyncio.StreamReader) -> None:
        while True:
            line = await stderr.readline()
            if not line:
                return
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                logger.warning("ffmpeg: %s", msg)

    async def _loop_cv2(
        self,
        cap: Any,
        source: rtc.VideoSource,
        frame_width: int,
        frame_height: int,
        camera_fps: float,
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV not installed: pip install opencv-python-headless") from exc

        interval = 1.0 / camera_fps if camera_fps > 0 else 1.0 / max(self.config.camera.fps, 1)
        next_frame_at = time.monotonic()
        consecutive_failures = 0
        frames_since_report = 0
        report_started_at = time.monotonic()
        worker = _OpenCVCaptureWorker(
            cap,
            warmup_frames=self.config.camera.warmup_frames,
            camera_id=self.camera_id,
        )
        worker.start()
        try:
            while running_fn() and connected_fn():
                ret, frame = await worker.read()
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
                frame_rgba = self.video_profile.transform_rgba(
                    frame_rgba, frame_width, frame_height
                )
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
        finally:
            await asyncio.shield(worker.close())
