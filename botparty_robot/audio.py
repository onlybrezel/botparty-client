"""Audio device helpers for BotParty robot client."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Literal

DeviceKind = Literal["playback", "capture"]


def list_alsa_devices(kind: DeviceKind = "playback") -> list[dict[str, str]]:
    command = ["aplay", "-l"] if kind == "playback" else ["arecord", "-l"]
    if shutil.which(command[0]) is None:
        return []

    try:
        output = subprocess.run(command, check=False, capture_output=True, text=True).stdout
    except Exception:
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
        result = subprocess.run(
            ["amixer", "-c", str(card), "sset", control, target],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True
    return False

