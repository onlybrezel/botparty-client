"""Audio device helpers for BotParty robot client."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .process_group import (
    credential_minimized_environment,
    run_sandboxed,
    terminate_async_process,
)

DeviceKind = Literal["playback", "capture"]
logger = logging.getLogger("botparty.audio")


@dataclass(frozen=True, slots=True)
class AudioCaptureConfig:
    sample_rate: int
    channels: int
    chunk_ms: int
    queue_frames: int
    executable: str
    requested_device: str
    sample_format: str

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> AudioCaptureConfig:
        return cls(
            sample_rate=int(options.get("audio_sample_rate", 48000)),
            channels=int(options.get("audio_channels", 1)),
            chunk_ms=int(options.get("audio_chunk_ms", 40)),
            queue_frames=int(options.get("audio_queue_frames", 8)),
            executable=str(options.get("arecord_path", "arecord")),
            requested_device=str(options.get("audio_device", "default")),
            sample_format=str(options.get("arecord_format", "S16_LE")),
        )


class AudioCapture:
    """Capture PCM with arecord and publish bounded frames to one LiveKit source."""

    def __init__(self, config: AudioCaptureConfig) -> None:
        self.config = config
        self.dropped_chunks = 0

    def candidate_devices(self) -> list[str]:
        resolved = resolve_alsa_device(self.config.requested_device, "capture")
        candidates = [resolved]
        if self.config.requested_device.strip().lower() in {"", "default", "pulse"}:
            candidates.extend(f"plughw:{device['hw']}" for device in list_alsa_devices("capture"))
        return list(dict.fromkeys(candidates))

    async def publish(self, rtc: Any, room: Any, running: Callable[[], bool]) -> None:
        if room is None:
            raise RuntimeError("audio capture requires a connected media room")
        sample_rate = self.config.sample_rate
        channels = self.config.channels
        samples_per_channel = sample_rate * self.config.chunk_ms // 1000
        frame_bytes = samples_per_channel * channels * 2
        source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(track, publish_options)

        last_error: str | None = None
        candidates = self.candidate_devices()
        for index, device in enumerate(candidates):
            if not running():
                return
            process = await self._spawn(device)
            try:
                await self._run_process(
                    process,
                    rtc,
                    source,
                    running,
                    frame_bytes,
                    samples_per_channel,
                )
            finally:
                await asyncio.shield(self._shutdown(process))
            if process.returncode in (None, 0):
                return
            last_error = f"arecord exited with code {process.returncode} on device {device}"
            if index + 1 < len(candidates):
                logger.warning("%s; trying fallback capture device", last_error)
        if last_error:
            raise RuntimeError(last_error)

    async def _spawn(self, device: str) -> Any:
        logger.info(
            "Starting audio capture: device=%s sample_rate=%d channels=%d",
            device,
            self.config.sample_rate,
            self.config.channels,
        )
        return await asyncio.create_subprocess_exec(
            self.config.executable,
            "-q",
            "-D",
            device,
            "-f",
            self.config.sample_format,
            "-c",
            str(self.config.channels),
            "-r",
            str(self.config.sample_rate),
            "-t",
            "raw",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=credential_minimized_environment(),
            start_new_session=True,
        )

    async def _run_process(
        self,
        process: Any,
        rtc: Any,
        source: Any,
        running: Callable[[], bool],
        frame_bytes: int,
        samples_per_channel: int,
    ) -> None:
        if process.stdout is None:
            raise RuntimeError("arecord did not provide a PCM stream")
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=self.config.queue_frames)

        async def read_stdout() -> None:
            try:
                while running():
                    chunk = await process.stdout.readexactly(frame_bytes)
                    if queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            queue.get_nowait()
                        self.dropped_chunks += 1
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(chunk)
            except asyncio.IncompleteReadError:
                return
            finally:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)

        async def publish_audio() -> None:
            while running():
                chunk = await queue.get()
                if chunk is None:
                    return
                await source.capture_frame(
                    rtc.AudioFrame(
                        data=chunk,
                        sample_rate=self.config.sample_rate,
                        num_channels=self.config.channels,
                        samples_per_channel=samples_per_channel,
                    )
                )

        async def drain_stderr() -> None:
            if process.stderr is None:
                return
            while line := await process.stderr.readline():
                detail = line.decode("utf-8", errors="replace").strip()
                if detail:
                    logger.warning("arecord: %.300s", detail)

        tasks = [
            asyncio.create_task(read_stdout()),
            asyncio.create_task(publish_audio()),
            asyncio.create_task(drain_stderr()),
        ]
        try:
            await asyncio.gather(tasks[0], tasks[1])
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def _shutdown(self, process: Any) -> None:
        await terminate_async_process(process)


def list_alsa_devices(kind: DeviceKind = "playback") -> list[dict[str, str]]:
    command = ["aplay", "-l"] if kind == "playback" else ["arecord", "-l"]
    if shutil.which(command[0]) is None:
        return []

    try:
        output = run_sandboxed(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    devices: list[dict[str, str]] = []
    pattern = re.compile(r"card (\d+): ([^\[]+)\[([^\]]+)\], device (\d+): ([^\[]+)\[([^\]]+)\]")
    for line in output.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        card, short_name, name, device, dev_short, dev_name = match.groups()
        devices.append(
            {
                "hw": f"{card},{device}",
                "card": card,
                "device": device,
                "name": name.strip(),
                "short_name": short_name.strip(),
                "device_name": dev_name.strip(),
                "device_short_name": dev_short.strip(),
            }
        )
    return devices


def resolve_alsa_device(spec: str | None, kind: DeviceKind = "playback") -> str:
    if not spec:
        return "default"
    normalized = spec.strip()
    if normalized in {"default", "pulse"}:
        return normalized
    if normalized.startswith(("hw:", "plughw:")):
        match = re.match(r"^(?:plug)?hw:(\d+)(?:,(\d+))?$", normalized)
        if match:
            card = match.group(1)
            device = match.group(2) or "0"
            requested = f"{card},{device}"
            available = {entry["hw"] for entry in list_alsa_devices(kind)}
            if requested in available:
                return normalized
            return "default"
        return normalized
    if re.fullmatch(r"\d+(,\d+)?", normalized):
        card_device = f"{normalized},0" if "," not in normalized else normalized
        available = {entry["hw"] for entry in list_alsa_devices(kind)}
        if card_device in available:
            return f"plughw:{normalized}"
        return "default"

    lowered = normalized.lower()
    for device in list_alsa_devices(kind):
        haystacks = [
            device["name"].lower(),
            device["short_name"].lower(),
            device["device_name"].lower(),
            device["device_short_name"].lower(),
        ]
        if any(lowered in item for item in haystacks):
            return f"plughw:{device['hw']}"
    return normalized


def resolve_alsa_card(spec: str | None, kind: DeviceKind = "playback") -> str | None:
    resolved = resolve_alsa_device(spec, kind)
    match = re.match(r"(?:plug)?hw:(\d+)(?:,\d+)?", resolved)
    if match:
        return match.group(1)

    normalized = (spec or "").strip().lower()
    for device in list_alsa_devices(kind):
        haystacks = [
            device["name"].lower(),
            device["short_name"].lower(),
            device["device_name"].lower(),
            device["device_short_name"].lower(),
        ]
        if normalized and any(normalized in item for item in haystacks):
            return device["card"]
    return None


def set_alsa_volume(spec: str | None, level: int) -> bool:
    if shutil.which("amixer") is None:
        return False

    card = resolve_alsa_card(spec, "playback")
    if card is None:
        return False

    target = f"{max(0, min(level, 100))}%"
    for control in ("PCM", "Speaker", "Master"):
        try:
            result = run_sandboxed(
                ["amixer", "-c", str(card), "sset", control, target],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False
