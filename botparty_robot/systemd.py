"""Minimal sd_notify support without an additional runtime dependency."""

from __future__ import annotations

import os
import socket


def notify_systemd(message: str) -> bool:
    address = os.getenv("NOTIFY_SOCKET", "")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.connect(address)
            client.sendall(message.encode("utf-8"))
        return True
    except OSError:
        return False
