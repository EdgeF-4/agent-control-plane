"""Pluggable eval adapters.

An *adapter* is the one part of an eval that knows how to obtain a system's
answer to a prompt; everything downstream (assertions, scoring, regression
diffing, the store) is the eval engine's and is adapter-agnostic. Two adapters
ship, both composed onto the engine's normalized adapter contract:

* ``golden`` — a fully local, air-gapped adapter that answers from a map of
  golden answers you provide inline (input or case-id -> expected output). It
  needs no network, so reliability checks run in a locked-down install; it is
  also a replay tool — capture a real system's outputs once and re-score them
  whenever you change the assertions.
* ``http`` — POSTs each prompt to a model/agent HTTP endpoint you configure
  (``base_url``, optional bearer key, headers) and scores the response. This is
  how you point a suite at a real model you host.

Any other ``type`` falls through to the engine's own adapter registry, so a
config-defined suite can use whatever the engine supports.
"""

from __future__ import annotations

from typing import Any

from agent_eval.adapters import HttpAdapter, MockAdapter, build_adapter
from agent_eval.config import AdapterConfig


class EvalAdapterError(ValueError):
    """Raised when an adapter spec is malformed (missing url, etc.)."""


def _golden_fixtures(spec: dict) -> dict:
    """Turn a golden-answers map into the engine's fixtures shape.

    Each answer may be a bare string (the expected output) or a full response
    spec (``{"output": ..., "tool_calls": [...], "prompt_tokens": ...}``).
    """
    responses: dict[str, Any] = {}
    for key, value in (spec.get("golden") or {}).items():
        responses[key] = value if isinstance(value, dict) else {"output": value}
    fixtures: dict[str, Any] = {"responses": responses}
    if "default" in spec:
        default = spec["default"]
        fixtures["default"] = default if isinstance(default, dict) else {"output": default}
    return fixtures


def build_eval_adapter(spec: dict, *, base_dir: str = "."):
    """Construct an eval adapter from a control-plane adapter spec."""
    atype = (spec or {}).get("type", "golden")

    if atype == "golden":
        cfg = AdapterConfig(type="mock", model=spec.get("model", "golden"))
        return MockAdapter(cfg, fixtures=_golden_fixtures(spec))

    if atype == "http":
        base_url = spec.get("base_url")
        if not base_url:
            raise EvalAdapterError("http eval adapter requires 'base_url'")
        cfg = AdapterConfig(
            type="http",
            model=spec.get("model", "model"),
            base_url=base_url,
            headers=dict(spec.get("headers") or {}),
            api_key=spec.get("api_key"),
            api_key_env=spec.get("api_key_env"),
            temperature=float(spec.get("temperature", 0.0)),
            max_tokens=spec.get("max_tokens"),
            timeout_s=float(spec.get("timeout_s", 30.0)),
            extra=dict(spec.get("extra") or {}),
        )
        return HttpAdapter(cfg)

    # Anything else: hand it to the engine's registry as-is.
    return build_adapter(AdapterConfig.from_dict(spec), base_dir=base_dir)


def labeling_config(spec: dict) -> AdapterConfig:
    """A config used only to label the run (adapter type + model in the record).

    The real adapter object is built by :func:`build_eval_adapter`; this carries
    the control-plane-facing type (e.g. ``golden``) into the stored result.
    """
    return AdapterConfig(type=(spec or {}).get("type", "golden"), model=(spec or {}).get("model", ""))
