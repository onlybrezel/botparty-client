"""Shared websocket event names used by the BotParty robot client."""

from .protocol import PROTOCOL_VERSION

WS_PROTOCOL_VERSION = PROTOCOL_VERSION

WS_EVENTS: dict[str, str] = {
    "ERROR": "error",
    "ROBOT_CLAIM": "robot:claim",
    "ROBOT_HEARTBEAT": "robot:heartbeat",
    "ROBOT_TELEMETRY": "robot:telemetry",
    "ROBOT_ACTIONS_PULL": "robot:actions:pull",
    "ROBOT_ACTIONS": "robot:actions",
    "ROBOT_ACTION_RESULT": "robot:action-result",
    "ROBOT_OUTCOME_ACK": "robot:outcome-ack",
    "CONTROL_COMMAND": "control:command",
    "CONTROL_ACK": "control:ack",
    "CONTROL_EMERGENCY_STOP": "control:emergency-stop",
    "SERVER_SHUTDOWN": "server:shutdown",
}
