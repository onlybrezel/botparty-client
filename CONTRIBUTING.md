# Contributing

Keep changes focused and preserve the safety contract. New moving adapters must implement emergency stop, close, explicit motion capabilities, guarded actuator writes and tests. Never add automatic executable downloads or in-place production updates.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/mypy
.venv/bin/pytest
.venv/bin/python -m build
```

Add a changelog entry for operator-visible behavior. New config keys require validation, an executable example and the options matrix. Security fixes should avoid publishing exploit detail before coordinated release.
