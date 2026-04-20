"""Shared websocket event names used by the BotParty robot client."""

WS_PROTOCOL_VERSION = 1

WS_EVENTS: dict[str, str] = {
    "ERROR": "error",
    "ROBOT_CLAIM": "robot:claim",
    "ROBOT_HEARTBEAT": "robot:heartbeat",
    "ROBOT_TELEMETRY": "robot:telemetry",
    "ROBOT_ACTIONS_PULL": "robot:actions:pull",
    "ROBOT_ACTIONS": "robot:actions",
    "CONTROL_COMMAND": "control:command",
    "CONTROL_EMERGENCY_STOP": "control:emergency-stop",
    "SERVER_SHUTDOWN": "server:shutdown",
}