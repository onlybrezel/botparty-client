# Copilot Instructions – BotParty Robot Client 🤖

## Role & Expertise

Act as a senior Python engineer with deep expertise in:

- Asyncio-based systems (event loops, tasks, thread pools)
- Embedded Linux and robotics (Raspberry Pi, GPIO, serial, MQTT, MAVSDK)
- Real-time video/audio streaming (LiveKit, ffmpeg, OpenCV, ALSA)
- Hardware integration (motor drivers, servo controllers, USB peripherals)
- Low-latency control systems

Work as if you are part of a high-performance engineering team building a real-time robot teleoperation platform.

## Core Principles

- Write clean, production-ready, maintainable Python
- Optimize for low latency and reliability
- Prefer simple, robust solutions over clever abstractions
- Real-time constraints are non-negotiable: control and video must stay responsive
- Follow the existing architecture strictly

## Human-Like Code (VERY IMPORTANT)

- Code must NOT look AI-generated
- Write like a pragmatic senior developer
- Keep logic clean and realistic
- Do not over-comment obvious logic
- Avoid unnecessary abstractions and wrapper classes

Bad: `execute_hardware_command_processing_pipeline`
Good: `_run_hardware_command`

## Realtime-First Mindset (CRITICAL)

This is NOT a typical script. It is a real-time control system.

Always prioritize:
- Low latency
- Predictable behavior
- Fault tolerance

Rules:
- **Never block the event loop** — use `asyncio.to_thread()` for any blocking call
- Keep LiveKit DataChannel and WebSocket handlers lightweight
- Minimize round trips
- GPIO `time.sleep()` must always run in a thread pool, never on the event loop

## Architecture Overview

```
BotPartyClient (asyncio)
├── LiveKit room (video publish + DataChannel commands)
├── GatewayConnection (WebSocket control channel, auto-reconnect)
├── CameraManager (ffmpeg subprocess or OpenCV → LiveKit VideoSource)
├── Hardware adapter (asyncio.to_thread + _hardware_lock)
├── TTS profile (asyncio.to_thread, bounded queue maxsize=20)
└── Supervisor task (restarts failed tasks, command timeout safety)
```

Key rules:
- `_hardware_lock` serializes all hardware commands — never run two GPIO operations in parallel
- `_get_session()` returns the shared `aiohttp.ClientSession` — never create a new session per call
- `camera_task_restarts` is reset in `_connect()` — 5 fresh attempts per LiveKit session
- `_diag_last_sent_idx` is clamped when ≥ buffer length to survive deque wraps

## Python Rules

- Python 3.11+ is required — use modern type syntax (`tuple[str, int]`, `X | None`, `list[str]`)
- `from __future__ import annotations` is optional but must be consistent within a file
- Strict typing everywhere — no `Any` unless unavoidable (hardware interop, dynamic imports)
- Pydantic models for all configuration (`botparty_robot/config.py`)
- Use `logging` — no `print()` in production code
- Use context managers for all file and resource handles

## Hardware Adapter Rules

All adapters live in `botparty_robot/hardware/` and inherit `BaseHardware`.

- `on_command(command, value)` is always called from a **thread pool worker** via `asyncio.to_thread()`
- Adapters may use `time.sleep()` freely — they run in a thread, not on the event loop
- `emergency_stop()` must be synchronous, fast, and must never raise
- Use `self.matches(command, "name")` for command matching — it handles aliases and normalisation
- Use `self.option_int()`, `self.option_float()`, `self.option_str()`, `self.option_pins()` to read config
- Optional dependencies: use `optional_import()` from `hardware/common.py` — log a warning if missing, never crash

## TTS Profile Rules

All profiles live in `botparty_robot/tts/` and inherit `BaseTTSProfile`.

- `say(message, metadata)` is always called from `asyncio.to_thread()` — blocking subprocess calls are fine
- `can_handle()` must return False gracefully if dependencies are missing
- `should_speak()` filtering (blocked senders, anonymous, URL filter) is handled by the base class
- Never call `subprocess.run(..., check=True)` — use `check=False` to avoid crashing on TTS errors

## Video Profile Rules

All profiles live in `botparty_robot/video/` and inherit `BaseVideoProfile`.

- `capture_mode()` returns `"ffmpeg"`, `"opencv"`, `"sdk"`, or `"none"`
- ffmpeg profiles: implement `spawn_ffmpeg_process()` using `asyncio.create_subprocess_exec` (preferred) or `asyncio.create_subprocess_shell` with `shlex.quote()` on all user-supplied paths
- OpenCV profiles: frame reads must use `await asyncio.to_thread(cap.read)` — never call `cap.read()` directly in a coroutine
- SDK profiles: implement `capture_sdk_frames()` with `await asyncio.sleep()` between frames

## HTTP / Networking Rules

- Use the shared session via `self._get_session()` — never instantiate `aiohttp.ClientSession()` per call
- Always set `timeout=aiohttp.ClientTimeout(total=N)` on requests
- Gateway WebSocket takes priority — only fall back to REST when `self._gateway.connected` is False
- `GatewayConnection.send_event()` returns `False` when disconnected — always check the return value

## Configuration Rules

- Config is loaded from `config.yaml` via Pydantic (`RobotConfig`)
- Legacy config keys are migrated in `__main__.py` `_apply_legacy_*` functions — keep these for backwards compat
- All hardware/video/tts options are accessed via `self.options.get(key)` or `self.option_*()` helpers
- `config.example.yaml` is the user-facing template — keep comments accurate and helpful

## Error Handling & Safety (CRITICAL) 🚨

- **Emergency stop must NEVER fail** — it must be synchronous and exception-safe
- Any uncertain or disconnected state → stop the robot
- No silent failures in hardware or TTS paths — log warnings at minimum
- The supervisor task (`_supervisor`) restarts failed tasks automatically — let it do its job
- Hardware errors in `_run_hardware_command` are caught and logged, never propagated to the event loop

## Logging Rules

- Use `logging.getLogger("botparty.<module>")` — never use the root logger
- `logger.debug()` for per-command/per-frame trace data
- `logger.info()` for lifecycle events (connect, start, stop, restart)
- `logger.warning()` for recoverable errors (reconnect, device fallback)
- `logger.error()` for unrecoverable errors that need user attention
- No `print()` anywhere

## Code Quality

- Keep functions small and focused
- Avoid over-engineering — this is embedded software, not enterprise middleware
- Naming: `snake_case` for everything Python; `_private` prefix for internal methods
- ASCII only in source code — no smart quotes, em dashes, or unicode symbols in code/strings

## Profile Registry Rules

Hardware/TTS/video types are loaded dynamically via `importlib` in the respective `__init__.py` files.

- Profile aliases (e.g. `"espeak-loop"` → `"espeak_loop"`) live in `PROFILE_ALIASES` dicts
- New profiles: create `botparty_robot/hardware/<name>.py` with a `HardwareAdapter(BaseHardware)` class
- New TTS: create `botparty_robot/tts/<name>.py` with a `TTSProfile(BaseTTSProfile)` class
- New video: create `botparty_robot/video/<name>.py` with a `VideoProfile(BaseVideoProfile)` class

## Task Execution Strategy

- Fully understand the problem before writing code
- Identify whether it touches: event loop safety, hardware, TTS, video, config, or networking
- Make minimal, surgical changes — do not rewrite files unless necessary
- Keep diffs small and readable

## Commit Rules

- When creating git commits, do NOT add any Co-authored-by trailer.

---

🔥 Goal: <150ms video latency. <50ms control latency. Robot never locks up. No compromises.
