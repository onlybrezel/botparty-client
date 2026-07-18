#!/usr/bin/env python3
"""Generate the versioned HTTP and websocket contract from runtime models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from botparty_robot.protocol import (
    PROTOCOL_VERSION,
    ActionResult,
    ClaimRequest,
    ClaimResponse,
    ControlAck,
    ControlCommand,
    OutcomeDeliveryAck,
    RemoteAction,
    RemoteActionsPayload,
    StreamPolicy,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "contracts" / "platform-contract-v1.json"
MODELS = {
    model.__name__: model
    for model in (
        ClaimRequest,
        ClaimResponse,
        StreamPolicy,
        ControlCommand,
        ControlAck,
        OutcomeDeliveryAck,
        RemoteAction,
        RemoteActionsPayload,
        ActionResult,
    )
}


def contract() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "protocolVersion": PROTOCOL_VERSION,
        "compatibility": {"minimumClient": 1, "maximumClient": 1},
        "http": {
            "POST /api/v1/robots/claim": {
                "request": "ClaimRequest",
                "success": "ClaimResponse",
                "successStatuses": [200, 201],
                "redirects": "rejected",
                "maxResponseBytes": 131072,
            },
            "POST /api/v1/robots/actions/poll": {
                "success": "RemoteActionsPayload",
                "successStatuses": [200, 201],
                "redirects": "rejected",
                "maxResponseBytes": 65536,
            },
            "POST /api/v1/robots/heartbeat": {
                "authentication": "robot bearer token",
                "request": {"type": "object", "additionalProperties": False},
                "successStatuses": [200, 201],
                "redirects": "rejected",
                "maxResponseBytes": 4096,
            },
            "POST /api/v1/robots/telemetry": {
                "authentication": "robot bearer token",
                "request": {
                    "type": "object",
                    "maxProperties": 32,
                    "additionalProperties": {
                        "oneOf": [
                            {"type": ["boolean", "integer", "number", "string", "null"]},
                            {
                                "type": "array",
                                "maxItems": 16,
                                "items": {"type": "string", "maxLength": 64},
                            },
                        ]
                    },
                },
                "successStatuses": [200, 201, 204],
                "redirects": "rejected",
                "maxResponseBytes": 4096,
            },
            "POST /api/v1/robots/logs": {
                "authentication": "robot bearer token",
                "request": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["clientId", "sequenceStart", "sequenceEnd", "lines"],
                    "properties": {
                        "clientId": {"type": "string", "maxLength": 128},
                        "sequenceStart": {"type": "integer", "minimum": 1},
                        "sequenceEnd": {"type": "integer", "minimum": 1},
                        "lines": {
                            "type": "array",
                            "maxItems": 200,
                            "items": {"type": "string", "maxLength": 4096},
                        },
                    },
                },
                "successStatuses": [200, 201, 204],
                "redirects": "rejected",
                "maxResponseBytes": 4096,
            },
        },
        "websocket": {
            "control:command": "ControlCommand",
            "control:ack": "ControlAck",
            "robot:actions": "RemoteActionsPayload",
            "robot:action-result": "ActionResult",
            "robot:outcome-ack": "OutcomeDeliveryAck",
        },
        "models": {
            name: model.model_json_schema(by_alias=True, mode="serialization")
            for name, model in MODELS.items()
        },
        "commandValueRules": {
            "motion": {
                "oneOf": [
                    {"type": "null"},
                    {"type": "number", "minimum": -100, "maximum": 100},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["x", "y"],
                        "properties": {
                            "x": {"type": "number", "minimum": -1, "maximum": 1},
                            "y": {"type": "number", "minimum": -1, "maximum": 1},
                        },
                    },
                ]
            },
            "nonMotionHardware": {"type": "null"},
            "ttsVolume": {
                "oneOf": [
                    {"type": "integer", "minimum": 0, "maximum": 100},
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "level": {"type": "integer", "minimum": 0, "maximum": 100},
                            "volume": {"type": "integer", "minimum": 0, "maximum": 100},
                        },
                    },
                ]
            },
        },
        "operatorOutputs": {
            "doctorJson": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["name", "status", "detail", "fix"],
                    "properties": {
                        "name": {"type": "string"},
                        "status": {"enum": ["OK", "WARN", "ERROR"]},
                        "detail": {"type": "string"},
                        "fix": {"type": ["string", "null"]},
                    },
                },
            },
            "commissionJson": {
                "type": "object",
                "required": [
                    "schemaVersion",
                    "clientVersion",
                    "buildId",
                    "mode",
                    "passed",
                    "phases",
                ],
                "properties": {
                    "schemaVersion": {"const": 1},
                    "clientVersion": {"type": "string"},
                    "buildId": {"type": "string"},
                    "mode": {"enum": ["online", "offline"]},
                    "passed": {"type": "boolean"},
                    "phases": {"type": "array"},
                },
            },
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(contract(), indent=2, sort_keys=True) + "\n"
    if args.write:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(rendered, encoding="utf-8")
        return 0
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != rendered:
            sys.stderr.write(
                "platform contract is stale; run generate-platform-contract.py --write\n"
            )
            return 1
        return 0
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
