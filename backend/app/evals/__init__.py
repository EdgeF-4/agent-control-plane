"""Reliability-eval adapters and the suite registry.

The control plane doesn't reinvent scoring — it composes the eval engine's
runner, assertions, and store. This package adds the pluggable *adapter* layer
(what the suite runs against) and a small registry of named suites the
scheduler, gate, and routes resolve by name.
"""

from .adapters import build_eval_adapter
from .suites import builtin_suites, default_suite_name, load_suites

__all__ = [
    "build_eval_adapter",
    "builtin_suites",
    "default_suite_name",
    "load_suites",
]
