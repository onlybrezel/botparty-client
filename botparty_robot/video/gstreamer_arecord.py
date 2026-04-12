"""Direct GStreamer publisher profile with ALSA audio."""

from __future__ import annotations

import shlex

from ..audio import resolve_alsa_device
from .gstreamer import VideoProfile as GStreamerVideoProfile


class VideoProfile(GStreamerVideoProfile):
    profile_name = "gstreamer_arecord"

    def _build_audio_branch(self) -> str | None:
        direct_audio_enabled = bool(self.options.get("direct_audio_enabled", True))
        if not direct_audio_enabled:
            return None

        if not self.gst_element_exists("alsasrc"):
            raise RuntimeError(
                "gstreamer_arecord requires the GStreamer ALSA plugin. Install gstreamer1.0-alsa."
            )
        if not self.gst_element_exists("opusenc"):
            raise RuntimeError(
                "gstreamer_arecord requires the GStreamer Opus encoder. Install gstreamer1.0-plugins-good."
            )

        sample_rate = int(self.options.get("audio_sample_rate", 48000))
        channels = int(self.options.get("audio_channels", 1))
        audio_device = resolve_alsa_device(
            str(self.options.get("audio_device", "default")),
            "capture",
        )
        audio_device_quoted = shlex.quote(str(audio_device))
        audio_bitrate = int(str(self.options.get("audio_bitrate_kbps", 64)).rstrip("k"))

        return (
            f"alsasrc device={audio_device_quoted} do-timestamp=true "
            f"! audio/x-raw,rate={sample_rate},channels={channels} "
            "! audioconvert ! audioresample ! queue "
            f"! opusenc bitrate={audio_bitrate * 1000} audio-type=voice frame-size=20"
        )
