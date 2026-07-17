# HIL reports

Store signed-off JSON measurements as `<release>/<device>.json`. Create a complete template with
`python scripts/check-hil-reports.py --template low` and validate it with
`python scripts/check-hil-reports.py reports/hil/<release>/<device>.json`.

Reports must contain measurements from the named physical adapter and device. CI data must not be
used as a substitute for oscilloscope or logic-analyzer stop-latency evidence.
