"""Core types for tabula-graph. These match the L1 frontmatter spec."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# Audience tags are consumer-defined strings. Tabula doesn't enforce a closed set;
# each consumer's deployment configures its own. See l1/encryption.md for the canonical
# reference taxonomy.
AudienceTag = str

# ULIDs are 26-char Crockford base32. We type-alias for clarity at boundaries.
ULID = str

# Entity references use the namespaced-slug convention: `<type>:<slug>` or
# `<type>:<parent>:<slug>`. e.g. `person:rjwalters`, `shot:eve:s001-c0001`.
EntityRef = str

# Release triggers from the frontmatter spec.
ReleaseTrigger = Literal["immediate", "conditional", "delayed", "posthumous"]


class Relation(BaseModel):
    """A relation between two memories. Mirrors L1 frontmatter `relations:`."""

    kind: Literal["references", "supersedes", "caused-by", "is-about"]
    target: ULID  # the related memory's ULID


class Memory(BaseModel):
    """A canonical L1 memory entry, as it lands in L3.

    This is the wire-format between L1 (markdown + frontmatter) and L3 (graph).
    Adapters serialize this to graph nodes + edges + embeddings.
    """

    # Required identity
    id: ULID
    author: EntityRef  # canonical author entity, e.g. "person:omniscia"
    audience: list[AudienceTag] = Field(default_factory=list)

    # Timestamps
    created_at: datetime
    modified_at: datetime | None = None

    # Provenance
    source: str  # e.g. "telegram://chat-name/msg-12345" or "cli://capture"
    classification: str = ""  # human-readable label

    # Content
    body: str = ""  # plaintext body (decrypted if applicable; empty for encrypted-only-mode)
    body_encrypted: bool = False
    encryption_keyset: str | None = None  # e.g. "age:v1:keyset-2026"

    # Graph linkage
    entities: list[EntityRef] = Field(default_factory=list)
    relations: list[Relation] = Field(default_factory=list)

    # Categorical metadata
    tags: list[str] = Field(default_factory=list)
    release_trigger: ReleaseTrigger = "immediate"
    release_condition: str | None = None
    redactions: list[str] = Field(default_factory=list)
    external_ids: dict[str, str] = Field(default_factory=dict)

    # Schema vocabulary type (e.g. "decision", "observation", "person").
    # Required for schema validation; optional here to allow partial reads.
    type: str | None = None


class SearchResult(BaseModel):
    """A single hit from a hybrid search."""

    memory_id: ULID
    score: float  # combined hybrid score, normalized 0.0–1.0
    snippet: str  # body excerpt with the match highlighted
    semantic_score: float | None = None  # pgvector cosine similarity
    keyword_score: float | None = None  # ts_rank from FTS
    graph_score: float | None = None  # graph-traversal proximity bonus
    audience: list[AudienceTag] = Field(default_factory=list)
    source: str = ""
