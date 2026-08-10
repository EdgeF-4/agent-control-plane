"""SIEM forwarding routes — sink health, delivery stats, and the dead-letter queue.

Sinks are configured as code in ``config.json`` (so egress destinations and their
secrets are reviewable and never mutated at runtime by the dashboard). These
routes surface the *running* audit pipeline — which sinks are wired, whether they
answer a connectivity test, how much has been forwarded, and what is waiting in
the dead-letter queue — and let an operator replay dead-lettered batches once a
destination is healthy again. The presets are ready-to-paste config snippets.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..deps import CurrentUser, get_current_user, get_hub, require_admin
from ..engines import EngineHub

router = APIRouter(prefix="/siem", tags=["siem"])


# Copy-paste templates for the sinks the bridge supports. Secrets are shown as
# ``*_env`` references so they stay out of the config file when preferred.
_PRESETS = [
    {
        "type": "file",
        "label": "File (local NDJSON)",
        "description": "Append every audit event to a local newline-delimited JSON file. The default, fully air-gapped sink.",
        "config": {"type": "file", "name": "local-audit", "enabled": True,
                   "path": "/data/siem/audit.ndjson", "rotate_max_bytes": 104857600},
    },
    {
        "type": "splunk_hec",
        "label": "Splunk (HTTP Event Collector)",
        "description": "Forward to a Splunk HEC endpoint. Set the token inline or via SPLUNK_HEC_TOKEN.",
        "config": {"type": "splunk_hec", "name": "splunk", "enabled": True,
                   "url": "https://splunk.example:8088", "token_env": "SPLUNK_HEC_TOKEN",
                   "index": "mcp_audit", "sourcetype": "mcp:audit"},
    },
    {
        "type": "elasticsearch",
        "label": "Elasticsearch (_bulk)",
        "description": "Bulk-index into Elasticsearch. Authenticate with an API key via ELASTIC_API_KEY.",
        "config": {"type": "elasticsearch", "name": "elastic", "enabled": True,
                   "url": "https://elastic.example:9200", "index": "mcp-audit",
                   "api_key_env": "ELASTIC_API_KEY"},
    },
    {
        "type": "datadog",
        "label": "Datadog (Logs Intake)",
        "description": "Ship to Datadog Logs. Set the API key via DD_API_KEY and choose your site.",
        "config": {"type": "datadog", "name": "datadog", "enabled": True,
                   "site": "datadoghq.com", "api_key_env": "DD_API_KEY",
                   "service": "agent-control-plane", "source": "mcp"},
    },
    {
        "type": "webhook",
        "label": "Generic webhook",
        "description": "POST batches as JSON to any HTTPS endpoint (a chat webhook, a custom collector).",
        "config": {"type": "webhook", "name": "webhook", "enabled": True,
                   "url": "https://collector.example/audit", "body_format": "array"},
    },
]


@router.get("/status")
async def siem_status(
    current: CurrentUser = Depends(get_current_user),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    status = await asyncio.to_thread(hub.siem_status)
    status["presets"] = _PRESETS
    return status


@router.get("/dlq")
async def siem_dlq(
    limit: int = 100,
    current: CurrentUser = Depends(get_current_user),
    hub: EngineHub = Depends(get_hub),
) -> list[dict]:
    return await asyncio.to_thread(hub.siem_dlq_entries, min(limit, 500))


@router.post("/sinks/{name}/test")
async def siem_test_sink(
    name: str,
    current: CurrentUser = Depends(require_admin),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    return await asyncio.to_thread(hub.siem_test_sink, name)


@router.post("/dlq/replay")
async def siem_replay(
    sink: str | None = None,
    limit: int | None = None,
    current: CurrentUser = Depends(require_admin),
    hub: EngineHub = Depends(get_hub),
) -> dict:
    return await asyncio.to_thread(hub.siem_dlq_replay, sink, limit)
