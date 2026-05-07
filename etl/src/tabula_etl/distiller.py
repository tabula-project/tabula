"""Distiller — the consumer-defined logic that turns RawEvents into L1 memories.

Distillers are typically domain-specific: "when the Botho coordination chat
contains 'we agreed X,' produce a `decision` memory tagged inner-circle."

The framework provides:
  - Batching (the distiller sees a list, not a stream)
  - Idempotency (the framework deduplicates source_event_ids before the call)
  - Audit (every distill() call is logged with input + output ULIDs + LLM trace)

The distiller provides:
  - The actual logic (rules, LLM calls, schema mapping)
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tabula_etl.types import DistilledMemory, RawEvent


class Distiller(ABC):
    """Turns a batch of RawEvents into zero-or-more DistilledMemorys.

    A single batch may produce:
      - 0 memories (nothing meaningful surfaced; events go to /dev/null but are still tracked in idempotency)
      - 1:1 mapping (each event → one memory, e.g. capturing every voice memo)
      - N:1 aggregation (a thread of messages → one `conversation` memory)
      - 1:N expansion (one inbound message → a `decision` plus an `observation`)
    """

    name: str  # stable identifier for audit, e.g. "botho-decision-rules"
    version: str  # bumps when distillation logic changes; backfills can target older versions

    @abstractmethod
    async def distill(self, batch: list[RawEvent]) -> list[DistilledMemory]:
        """Process a batch of events. Return memories to commit."""


class PassthroughDistiller(Distiller):
    """Reference distiller: each RawEvent becomes a 1:1 `observation` memory.

    Useful for backfills where you want every event captured, even if domain
    logic isn't ready. Also useful as a smoke test for the full pipeline.
    """

    name = "passthrough"
    version = "0.1.0"

    def __init__(self, archive: str, audience: list[str], type: str = "observation") -> None:
        self.archive = archive
        self.audience = audience
        self.type = type

    async def distill(self, batch: list[RawEvent]) -> list[DistilledMemory]:
        from datetime import timezone

        out: list[DistilledMemory] = []
        for ev in batch:
            body = _payload_to_body(ev.payload)
            frontmatter = {
                "author": "system:tabula-etl",
                "audience": list(self.audience),
                "created_at": ev.occurred_at.astimezone(timezone.utc).isoformat(),
                "modified_at": None,
                "source": ev.source,
                "tags": ["etl", "passthrough"],
            }
            out.append(
                DistilledMemory(
                    source_event_ids=[ev.source_id],
                    type=self.type,
                    frontmatter=frontmatter,
                    body=body,
                    archive=self.archive,
                )
            )
        return out


def _payload_to_body(payload: dict) -> str:
    """Best-effort markdown rendering of an arbitrary RawEvent payload."""
    if "content" in payload and isinstance(payload["content"], str):
        return payload["content"]
    if "text" in payload and isinstance(payload["text"], str):
        return payload["text"]
    # Fallback: render as a YAML block.
    import yaml
    return "```yaml\n" + yaml.safe_dump(payload, sort_keys=True) + "```\n"
