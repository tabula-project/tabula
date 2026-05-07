# Tabula ETL — adapter framework

> **Status:** Draft. The framework formalizes a pattern emerging from TWIN's gt-messaging plugin (`omniscia/twin/messaging/`) and intended for re-use across all Tabula consumers.

## Purpose

Universal **L2 ingestion** for Tabula consumers. Standardized adapters consume external sources (chat platforms, email, calendar, files, MCP tool calls, LLM session logs) and emit normalized events into the consumer's L2 operational log. A separate distillation stage promotes L2 events to L1 substrate (the [dual-tier memory pattern](../docs/patterns/dual-tier-memory.md)).

```
┌─────────────────────────────────────────────────────────┐
│  External sources                                       │
│  Signal · Telegram · Slack · Discord · email · cal     │
│  files · photos · voice · browser · code · MCP · LLM    │
└──────────────────────┬──────────────────────────────────┘
                       │ IngestionAdapter (per source)
                       ▼
┌─────────────────────────────────────────────────────────┐
│  Normalized Event stream                                │
│  { id, source, ts, actor, content, attachments,         │
│    raw, privacy_class, external_ids }                   │
└──────────────────────┬──────────────────────────────────┘
                       │ EventStore (L2)
                       ▼
┌─────────────────────────────────────────────────────────┐
│  L2 operational log                                     │
│  Postgres / SQLite / Parquet (per consumer)             │
└──────────────────────┬──────────────────────────────────┘
                       │ EventDistiller (L2 → L1)
                       ▼
┌─────────────────────────────────────────────────────────┐
│  L1 substrate (markdown + git)                          │
│  Typed records: observation, decision, conversation,    │
│  person, place, event, project, tool, vision            │
└─────────────────────────────────────────────────────────┘
```

## Adapters in v1 (planned)

| Adapter | Source | Volume | Sovereign? | Status |
|---|---|---|---|---|
| `TelegramAdapter` | Telethon userbot | High | Local possible | EXISTS in gt-messaging |
| `SignalAdapter` | signal-cli JSON-RPC | High | Local possible | EXISTS in gt-messaging |
| `WhatsAppAdapter` | whatsmeow | High | Local possible | PLANNED in gt-messaging |
| `SlackAdapter` | Slack API + canvases | High | Cloud SaaS | gap (Luce has live MCP, not ingest) |
| `DiscordAdapter` | Discord API | High | Cloud SaaS | gap |
| `EmailAdapter` | IMAP / Gmail API / Mail.app .mbox | Very high | Mixed (local mbox sovereign; Gmail leaks metadata) | gap |
| `CalendarAdapter` | CalDAV / Google / iCloud | Medium | Mixed | gap |
| `FileWatchAdapter` | filesystem watcher / Drive / iCloud | Medium | Mixed | gap |
| `PhotosAdapter` | Immich (local) / iCloud / Google | High | Local Immich sovereign | gap |
| `VoiceAdapter` | audio file → Whisper | Medium | Local Whisper sovereign | gap |
| `BrowserAdapter` | local sqlite history | Very high | Local | gap |
| `CodeAdapter` | git hooks; commit logs | Low | Local | gap |
| `LLMSessionAdapter` | claude-mem reader; pi session logs; ChatGPT export | Medium | Local | gap (the AI-memory feedback loop) |
| `MCPToolAdapter` | FastMCP middleware (Luce's existing pattern) | Very high | Application-specific | EXISTS in Luce |

## Core abstractions

### `IngestionAdapter` base class

Generalizes gt-messaging's `MessagingAdapter` beyond chat platforms.

```python
class IngestionAdapter(ABC):
    """Base class for all Tabula L2 ingestion sources."""
    
    name: str                          # adapter id, e.g. "signal", "email-imap"
    privacy_class: PrivacyClass        # default class for this source
    
    @abstractmethod
    async def start(self) -> None:
        """Begin ingestion. Long-running; daemonized."""
    
    @abstractmethod
    async def stop(self) -> None:
        """Graceful shutdown; flush pending."""
    
    @abstractmethod
    async def health(self) -> HealthStatus:
        """For supervision / heart_beat() lifecycle."""
    
    async def emit(self, event: Event) -> None:
        """Normalize raw source data → Event; write to L2 store. Default impl writes to configured EventStore."""
```

### `Event` (normalized)

```python
@dataclass
class Event:
    id: str                            # ULID
    source: str                        # URI: "signal://thread/abc/msg/def"
    ts: datetime                       # source-side timestamp
    actor: Optional[str]               # canonical entity ID, e.g. "person:rjwalters"
    content: str                       # normalized text content
    attachments: list[Attachment]      # optional binary payloads
    raw: dict                          # original platform-native payload
    privacy_class: PrivacyClass        # family_or_self | project | public
    external_ids: dict[str, str]       # platform-native IDs (slack_ts, telegram_msg_id, etc.)
    received_at: datetime              # local arrival timestamp
```

### `EventStore` (L2 storage abstraction)

```python
class EventStore(ABC):
    """Append-only, queryable, distillable L2 store."""
    
    @abstractmethod
    async def write(self, event: Event) -> None:
        """Idempotent write (dedup on event.id)."""
    
    @abstractmethod
    async def query(self, filter: EventFilter) -> AsyncIterator[Event]:
        """For distillation pipelines."""
    
    @abstractmethod
    async def rotate(self, before: datetime) -> int:
        """reset() lifecycle: rotate old events to compressed archive."""
```

Default implementations:
- `SQLiteEventStore` — for low-medium volume (TWIN-personal, Bower)
- `PostgresEventStore` — for high volume (Luce)
- `ParquetEventStore` — for archival rotation tier

### `EventDistiller` (L2 → L1 promotion)

```python
class EventDistiller(ABC):
    """Reads a stream of L2 events; emits L1 typed records."""
    
    @abstractmethod
    async def distill(self, events: AsyncIterator[Event]) -> AsyncIterator[L1Record]:
        """Consume events; yield typed records (type: observation/decision/conversation/etc.).
        
        The output is written to the consumer's L1 archive via TabulaWriter."""
    
    triggers: list[DistillationTrigger]  # message-arrival, periodic, explicit-marker, etc.
    
    audience_classifier: AudienceClassifier  # maps event → audience tier(s)
```

Distillers are LLM-assisted by default (use Tabula's L0 router for classification, summarization, structured extraction). Privacy-class metadata on events constrains which L0 backend the distiller can use.

## Consumer-specific configuration

Each consumer configures which adapters run, with what frequency, and how distillation is mapped:

```yaml
# tabula consumer config (per-application)
adapters:
  signal:
    enabled: true
    privacy_class: family_or_self    # default; per-conversation override below
    audience_map:
      "sg:contact:+16264834952": [project]   # Robb conversation → project tier
      "sg:contact:+1...": [self]
  email-imap:
    enabled: false                    # not yet implemented
  ...

distillers:
  conversation:
    trigger: topic-shift              # gap > 4h or channel close
    target_type: conversation
  decision:
    trigger: explicit-marker          # overseer says "lock this in"
    target_type: decision
  daily-observation:
    trigger: cron:0 22 * * *          # daily at 22:00
    target_type: observation
```

## Privacy class enforcement

Every event carries a `privacy_class`. The distiller passes class to the L0 router on every classification call. The router refuses to route a `family_or_self` event to closed-frontier models, even if a distiller asks. See [`l0/docs/router.md`](../l0/docs/router.md) for enforcement details.

Misclassification at ingest time is the security bug the framework is designed to surface. Adapters MUST set a default privacy class; distillers MUST pass class through to L0; the router MUST refuse to lower class. Three layers of enforcement.

## Reference implementation

TWIN's gt-messaging plugin at `omniscia/twin/messaging/` is the first-pass implementation of this pattern. The migration from "messaging-only" to "general IngestionAdapter framework" is tracked in `omniscia/twin` bead `tw-lmp` and tabula bead [TBD].

## What this framework does NOT do

- **It does not run the adapters.** Each consumer runs the adapters it needs; Tabula provides the abstractions and reference implementations, not a hosted service.
- **It does not handle L4 application logic.** Adapters write to L2 and trigger distillation. What the application does with L1 records (Bower's chat surface, Luce's L2 coordinator, TWIN's recall CLI) is outside this scope.
- **It does not enforce schema validity.** Distillers should validate their output against `schema/v1/types/*.yaml`, but the framework doesn't impose validation; that's a write-time concern at L1.

## Open questions

1. **Should `EventDistiller` be LLM-only, or rule-based + LLM hybrid?** Luce's shadow-learning has rule-based promotion; Bower's concierge is LLM-driven. Probably both.
2. **Where do schema-validation failures go?** Soft-reject (write to L2 quarantine) or hard-fail (refuse to write)? Probably soft-reject + audit alert.
3. **Cross-adapter dedup.** If a Slack message gets ingested by both `SlackAdapter` and `LLMSessionAdapter` (because it appeared in a Claude session), should they dedup? Probably yes, via `event.external_ids`.

## Cross-references

- Pattern: [`docs/patterns/dual-tier-memory.md`](../docs/patterns/dual-tier-memory.md)
- L1 substrate: [`l1/README.md`](../l1/README.md)
- L0 router (where privacy enforcement lives): [`l0/docs/router.md`](../l0/docs/router.md)
- TWIN gt-messaging reference impl: `~/gt/twin/crew/vivake/messaging/`
- Beads: `tw-lmp` (TWIN-side: generalize gt-messaging into IngestionAdapter), `tw-m3n` (Path C convergence)
