"""Alerting subsystem.

Public surface (filled in across phases A-G):

- ``hysteresis``: pure state-machine evaluators for per-space edges and
  threshold rules.  No DB, no I/O — easy to unit-test.
- ``engine``: tails ``Occupancy`` and ``CameraScan`` rows, evaluates
  rules, fires alerts.  Started from ``main.py`` lifespan (phase B+).
- ``dispatcher``: worker pool that delivers ``alert_events`` via the
  channel implementations, with exponential-backoff retries (phase F).
- ``routes``: REST endpoints for channel / group / rule CRUD, alert
  history, manual retry.  Gated by the ``manage_alerts`` permission.
- ``channels.webhook`` / ``channels.email`` / ``channels.mqtt``: the
  three delivery implementations.
"""

from .hysteresis import (
    evaluate_space_rule,
    evaluate_threshold_rule,
)

__all__ = [
    "evaluate_space_rule",
    "evaluate_threshold_rule",
]
