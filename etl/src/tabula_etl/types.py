"""Core types for tabula-etl. The wire format between L2 → distiller → L1."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RawEvent(BaseModel):
    """A single L2 operational event.

    The shape is deliberately permissive — adapters know their source's quirks.
    The distiller is responsible for normalizing into the typed L1 form.
    """

    # Stable identifier for idempotency. Adapters MUST produce the same id
    # for the same logical event across retries.
    source_id: str

    # Free-form source URL (telegram://, kitsu://, slack://, etc.)
    source: str

    # When the event happened in the source system, not when it was ingested.
    occurred_at: datetime

    # The raw payload, parsed from the source. Schema is per-adapter.
    payload: dict[str, Any] = Field(default_factory=dict)

    # Optional pre-classified audience hint. The distiller may override.
    audience_hint: list[str] = Field(default_factory=list)


class DistilledMemory(BaseModel):
    """The output of distillation — an L1 memory ready to be committed.

    This is intentionally a thinner wrapper than tabula_graph.Memory: the
    distiller produces frontmatter + body, and the pipeline writes it to
    the L1 corpus (markdown + git). The graph adapter picks it up via the
    rebuild path, or via a direct upsert call from the pipeline.
    """

    # Lineage back to the source event(s) — for idempotency + audit.
    source_event_ids: list[str]

    # The L1 type (one of the schema vocabulary, e.g. "decision", "observation").
    type: str

    # Frontmatter fields. Validated against schema/v1/types/<type>.yaml before commit.
    frontmatter: dict[str, Any]

    # The markdown body. Plaintext at this stage; encryption happens at commit time.
    body: str

    # Which L1 archive (consumer-defined) this should land in.
    archive: str  # e.g. "omniscia/family", "v-good/memory"

    # Optional override for commit message. Default: "<type>: first-line-of-body"
    commit_message: str | None = None


class AuditEntry(BaseModel):
    """One row in the distillation audit log.

    Captures: which raw events became which L1 memory, what the distiller
    decided, and (if LLM-driven) the prompt + response that drove the call.
    """

    distillation_id: str  # ULID assigned by the pipeline per batch run
    timestamp: datetime
    distiller_name: str
    distiller_version: str

    input_event_ids: list[str]
    output_memory_ids: list[str]   # ULIDs of L1 entries that were committed

    # Optional LLM trace (if distillation used a model)
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None
    llm_prompt_hash: str | None = None  # SHA-256 of prompt; full prompt logged separately

    # Outcome
    succeeded: bool
    error: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
