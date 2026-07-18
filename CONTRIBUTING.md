# Contributing

Keep changes focused and preserve the safety contract. New moving adapters must implement emergency stop, close, explicit motion capabilities, guarded actuator writes and tests. Never add automatic executable downloads or in-place production updates.

Read the [developer guide](DEVELOPER_GUIDE.md) for the module map, runtime flows, configuration
boundaries and debugging entry points.

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements/build-toolchain.txt
.venv/bin/pip install --require-hashes -r requirements/dev.txt
.venv/bin/pip install --no-deps -e .
.venv/bin/ruff format .
.venv/bin/ruff check .
.venv/bin/mypy
COVERAGE_FILE=/tmp/botparty.coverage .venv/bin/pytest --cov=botparty_robot --cov-branch \
  --cov-report=json:/tmp/botparty-coverage.json
.venv/bin/python scripts/check-coverage.py /tmp/botparty-coverage.json
.venv/bin/python scripts/adapter-inventory.py --check
.venv/bin/python scripts/check-doc-links.py
.venv/bin/python -m build --no-isolation
```

Add a changelog entry for operator-visible behavior. New config keys require validation, an executable example and the options matrix. Security fixes should avoid publishing exploit detail before coordinated release.
