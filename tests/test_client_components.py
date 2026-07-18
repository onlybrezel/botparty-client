from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from botparty_robot.client import BotPartyClient
from botparty_robot.client_commands import ClientCommandsComponent
from botparty_robot.client_media import ClientMediaComponent
from botparty_robot.client_ops import ClientOpsComponent
from botparty_robot.client_runtime import ClientLifecycleComponent
from botparty_robot.config import RobotConfig, ServerConfig, VideoConfig


def test_production_client_composes_explicit_runtime_components() -> None:
    client = BotPartyClient(
        RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            video=VideoConfig(type="none"),
        )
    )
    try:
        assert isinstance(client.lifecycle, ClientLifecycleComponent)
        assert isinstance(client.media, ClientMediaComponent)
        assert isinstance(client.operations, ClientOpsComponent)
        assert isinstance(client.control, ClientCommandsComponent)
        assert not isinstance(client, ClientLifecycleComponent)
        assert not isinstance(client, ClientMediaComponent)
        assert not isinstance(client, ClientOpsComponent)
        assert not isinstance(client, ClientCommandsComponent)

        assert client.control.host is client
        assert client.lifecycle.host is client
        client.control.host._latest_motion_command_id = 7
        client.lifecycle.host._running = True
        assert client._latest_motion_command_id == 7
        assert client._running is True
        assert set(client.control.__dict__) == {"_component_host"}
    finally:
        logging.getLogger("botparty").removeHandler(client._diag_handler)
        client.handler.close()


def test_components_construct_with_focused_host_doubles() -> None:
    host = SimpleNamespace()
    control = ClientCommandsComponent(host)
    media = ClientMediaComponent(host)
    lifecycle = ClientLifecycleComponent(host)

    assert control._coerce_tts_volume({"level": 125}) == 100
    assert media._parse_target_bitrate_kbps(900.0) == 900
    assert lifecycle._camera_last_frame_age(SimpleNamespace(last_frame_at_monotonic=0)) is None


def test_shutdown_propagates_unconfirmed_stop_and_adapter_close(monkeypatch) -> None:
    async def scenario() -> None:
        client = BotPartyClient(
            RobotConfig(
                server=ServerConfig(claim_token="claim-token"),
                video=VideoConfig(type="none"),
            )
        )

        async def unconfirmed_stop(_reason: str) -> bool:
            return False

        client._trigger_hardware_stop = unconfirmed_stop  # type: ignore[method-assign]
        monkeypatch.setattr(
            client.handler,
            "close",
            lambda: (_ for _ in ()).throw(RuntimeError("close failed")),
        )
        try:
            with pytest.raises(RuntimeError, match="safe shutdown was not confirmed"):
                await client.shutdown()
            codes = {str(fault["code"]) for fault in client._runtime_faults.snapshot()}
            assert "hardware_close_failed" in codes
        finally:
            logging.getLogger("botparty").removeHandler(client._diag_handler)

    asyncio.run(scenario())
