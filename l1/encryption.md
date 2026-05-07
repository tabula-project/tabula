# L1 Encryption — Age + Audience Tiers

> Source: omniscia/twin architecture/TWIN-V1.md §3.4. Adapted for Tabula's substrate-agnostic framing.

## Algorithm

All non-public memory bodies are encrypted using **age** (modern X25519 + ChaCha20-Poly1305 multi-recipient encryption). Frontmatter (id, classification, audience, etc.) remains plaintext for queryability.

## Audience tier taxonomy (reference)

These are the tiers from the original TWIN convergence. Each Tabula consumer defines its own tier set; this is the canonical reference that proved the pattern.

| Tier | Audience | Encryption recipient |
|---|---|---|
| `public` | Everyone | None |
| `org-maj` | MAJ Foundation trustees + operational principals | MAJ-org age public key |
| `org-maj:trustees` | MAJ Foundation trustees only (governance content) | MAJ-trustees sub-key |
| `org-goodstudios` | Good Studios principals | GoodStudios-org age public key |
| `org-shamrock` | Shamrock co-founders | Shamrock-org age public key |
| `inner-circle` | Selected advisors and close collaborators | Per-person keys |
| `family` | Spouse + children when of age + parents | Family group age public key |
| `spouse-only` | Spouse | Spouse age public key |
| `self-only` | Creator only during life | Master age public key |

## Per-segment encryption

Each memory body gets its own data-encryption key (DEK), and the DEK is encrypted to all the audience public keys authorized to read it. A single memory can be readable by multiple tiers.

## Key custody pattern

- Audience age private keys held in a password manager + Shamir-distributed (3-of-5) for posthumous recovery.
- Master age private key (root of `self-only` and ultimate fallback for all tiers) Shamir-distributed (3-of-5) across custodians.

## Key rotation

When an audience member changes (someone leaves, advisor relationship ends), rotate that audience's age key and re-encrypt affected segments. Append-only with `supersedes` relations makes this surgical (only that tier's segments are affected).

## Tabula consumer adaptation

Each consumer (TWIN, Luce, Bower, etc.) substitutes its own tier set, but the pattern is invariant:
1. `public` = no encryption
2. Every non-public tier = age-encrypted body, plaintext frontmatter
3. DEK encrypted to all authorized recipient keys
4. Shamir custody for root keys
5. Surgical rotation via `supersedes` chains
