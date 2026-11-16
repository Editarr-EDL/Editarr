# Requirement coverage

Use this map to check the task pack against the full design. It is not a launch
scope selection. Task IDs resolve through the [master checklist](README.md).

Updated October 6 for [Docker on a Mac mini](../community-service-docker.md).
Completed Worker/D1 tasks remain historical shared-contract evidence, not proof
that the Node adapter, live ingress or target machine has passed release acceptance.

| Requirement / risk | Owning tasks |
| --- | --- |
| Replace obsolete wipe guidance without wiping data | CSS-00 |
| Verified baseline only; no blind stamping | CSS-01, 04, 05 |
| Surviving workers, exclusive maintenance, migration safety | CSS-02, 03, 05, 06 |
| Packaged migrations / fresh-upgrade parity | CSS-04, 06, 67 |
| Release authority / scanner behavior / effective provenance | CSS-07, 08, 09 |
| JSON versus Protobuf space/CPU/queryability evaluation | CSS-10, 11; G-ENCODING |
| Signed exact request and artifact bytes, trusted key binding | CSS-12, 13, 25, 42, 44 |
| Portable secret storage, identity backup with optional encryption off by default | CSS-14, 15, 16, 19, 20 |
| Safe full/settings restore, path/auth conflicts, no implicit sharing | CSS-17, 18, 20, 62 |
| Initial setup restore option, setup-only authorization and administrator transition | CSS-71, 66, 67; G-SETUP-RESTORE |
| Local persistent identity/provenance/outbox | CSS-21 |
| Separate Docker Node/SQLite runtime and operator assets; Worker regression harness retained | CSS-22/23/38 historical foundations; CSS-66, 67 |
| Passive usage/byte observations, logical SQLite writes and unknown reads; no user admission allowances | CSS-39, 68; OPS-02; CSS-40 retired |
| No nonce cache-only replay defense; mutation idempotency | CSS-25, 31, 39, 45, 51, 52 |
| Discord community/application creation | OPS-01 |
| OAuth state/session/key binding and no anonymous fallback | CSS-26, 28, 29, 30 |
| Compare membership method, then implement approved freshness/outage/rejoin | CSS-70, 27; G-MEMBERSHIP/G-ADMISSION |
| Stable contributor/name and no duplicate identity on reinstall | CSS-23, 28, 33 |
| Manual UI rotation, immediate retirement, old artifact verification | CSS-13, 31, 32 |
| Multiple installations and lost-key/Discord recovery behavior | CSS-33; G-KEYS/G-RECOVERY |
| Typed movie/series/episode identity and safe source adapters | CSS-34 |
| Versioned fingerprints, legacy MD5 separation, changed-file detection | CSS-35 |
| Selected-track AI input, language/description metadata, cache isolation and durable actual execution provenance | CSS-35A, 35C |
| Independent compressed audio/video fingerprints, remux normalization, measured demux bounds and stream timing | CSS-35B, 36, 48; G-MATCHING |
| Scanned individual audio track only, non-mutating mixed-review exclusions and review-required unknown-track history | CSS-35A, 37, 46, 49, 66 |
| Compatibility bands, cuts/dubs/timing, explicit uncertain review | CSS-36, 48, 49, 50 |
| AI/manual/derived artifacts, coverage/counts, privacy preview | CSS-11, 37, 46 |
| Immutable revisions and bounded source lineage | CSS-13, 37, 38, 44 |
| SQLite indexed metadata and immutable artifact/manifest/signature BLOBs | CSS-38, 39, 41, 42, 66 |
| Atomic SQLite publication rollback, retry after lost response and payload integrity | CSS-39, 42, 45, 66; CSS-60/61 legacy evidence; CSS-40 retired |
| Bounded availability/search, cache isolation and errors | CSS-41, 42, 48 |
| Reuse EDL/MCF transactions and preserve saved-review behavior | CSS-43, 44, 50 |
| Manual donation and exact approved retry payload | CSS-45, 46 |
| Local Find Filters extraction and shared entry points | CSS-47, 48 |
| Rich responsive result UI, detail/filters/sorts | CSS-49, 50, 54 |
| Multiple imports, partial failure/cancel and already-imported reuse | CSS-44, 50 |
| Unique successful import count, no self/duplicate inflation | CSS-51 |
| One changeable vote/revision, privacy and eligibility | CSS-52, 54 |
| Compatibility-first ranking, limited uploader boost | CSS-53; G-REPUTATION |
| Separate reports, audited moderation and reversals | CSS-54, 56, 58 |
| Cloudflare Access for both operator UI/API | CSS-55; OPS-02 |
| Hosted dashboard, keyboard/mobile/session/error states | CSS-57, 58 |
| CLI maintenance, progress/resume, shared admin services | CSS-59 |
| Offline verified SQLite snapshot, separate configuration/key history and independent restore | CSS-67, 68; G-SERVICE-BACKUP; CSS-60/61 legacy archive inventory |
| Conditional D1/archive import: explicit approval, verified source backup, known-schema conversion, no blind stamping or source deletion | CSS-67; CSS-69 cutover gate |
| Explicit opt-in automatic sharing, restore-safe behavior | CSS-62 |
| License/withdrawal/derivative rights remain user decisions | CSS-63; G-LICENSE |
| Retention, dedup facts, historical keys and privacy | CSS-64 DONE locally/disposable October 8; G-RETENTION approved retain-first, indefinite durable evidence, bounded existing transient cleanup and read-only offline preview; no service-backup pruning, capacity certification or public activation |
| Optional unattended backup policy, no saved passphrase | CSS-65; G-SCHEDULING |
| Cross-language/process/browser/abuse/recovery evidence, every API family through real Node HTTP/SQLite and enabled image | CSS-66 |
| Graceful/hard restart, exclusive writer, durable replay/operations and actual scheduled cleanup wiring | CSS-66, 67 |
| Live Discord callback/cookies, enrollment, bot member/nonmember/rejoin/outage and token rotation | OPS-01; CSS-66 synthetic matrix; CSS-69 evidence gate |
| Tunnel Host/IP boundary, loopback isolation and real Access browser/CLI session/JWKS/CSRF lifecycle | OPS-02; CSS-69 evidence gate |
| Native/container packaging, ARM64/AMD64 support, private mounts/secrets and versioned image/schema rollback | CSS-67; G-LAUNCH |
| Mac cold boot, FileVault/login, Docker/tunnel startup, reconnect and manual-intervention limits | CSS-67; OPS-02; CSS-69 evidence gate |
| Target-Mac capacity/load audit and constrained-storage recovery drill | CSS-68 CANCELED / RETIRED by user October 7; no active implementation requirement. CSS-69 records unknown hardware headroom; unrestricted usage/disable/storage safety remain covered by existing runtime tests |
| Independent recovery, backup protection/key custody, recovery objectives and stale-security-state reconciliation | CSS-68 drill canceled; G-SERVICE-BACKUP unchanged; CSS-69 owns explicit launch disposition of unverified recovery, not an assumed replacement drill |
| Full plan first, explicit pilot/launch boundary later | CSS-69; G-LAUNCH |

Advanced perceptual alignment, automatic consensus merging, hardware attestation,
public social profiles/comments and whole-library background polling remain outside
the accepted scope. Do not add them to complete another task. Any requested expansion
needs its own clarified brief and dependencies.
