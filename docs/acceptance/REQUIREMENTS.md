# Normative acceptance catalog — planning assertions, NOT passing tests

| ID | Primary owner | Verification owner | Executable outcome required |
|---|---|---|---|
| DEPLOY-01 | T02 | DEPLOY-01 | Only backend exposed at host loopback; DB/vector/Ollama container ports private |
| DEPLOY-02 | DEPLOY-01 | T41 | Connected installer provisions pinned Compose services + native CLI; volumes survive upgrade/uninstall |
| DEPLOY-03 | T37 | DEPLOY-01 | Offline consistent manual restore replays deletion journal before serving |
| DB-ROLE-01 | T03 | T40 | Real Postgres 18 tenant RLS, role separation and grant checks |
| DB-MIG-01 | T07 | T40 | Fresh and incremental V001–V005 Flyway tests, drift and negative check |
| API-SESSION-01 | T08 | T24 | Implicit 30d idle/90d absolute opaque session renewal, no refresh route |
| API-CREATE-01 | T11 | T26 | Atomic idempotent conversation create/project reassignment |
| RUN-01 | T16 | T27 | Atomic run completion+assistant revision+job enqueue, stale executor fenced |
| SSE-01 | T18 | CLI-14 | Snapshot replace, contiguous text sequence and reconnect with no duplication |
| MEM-01 | T31 | T29 | Pinned Mem0 and embedding/Qdrant compatibility; user filter and owner validation |
| MEM-02 | T33 | T34 | Exact committed turn async ingest, no uncertain external write replay |
| MEM-03 | T34 | CLI-16A | Missing memory vs unavailable memory distinguished; tenant doubt fails closed |
| CLI-CMD-01 | CLI-08 | CLI-16A | stdin+argument semantics and max 1 MiB; canonical only stdout; exit codes |
| CLI-CTX-01 | CLI-06 | CLI-16A | sticky generation CAS, explicit precedence and stale selection handling |
| CLI-REC-01 | CLI-07 | CLI-16A | metadata-only journal before send, 404 uncertain/no auto replay |
| CLI-TUI-01 | CLI-10 | CLI-16B | PTY keybindings, composer draft focus and cancellation precedence |
| CLI-TUI-02 | CLI-12A | CLI-16B | scroll anchors survive pages and resize, bottom-follow rule |
| CLI-TUI-03 | CLI-12B | CLI-16B | escape-sequence sanitization and bounded Markdown cache |
| CLI-CFG-01 | CLI-04 | CLI-16A | Keyring and explicit 0700/0600 fallback; no silent insecure storage |
| LIFECYCLE-01 | T35 | T40 | Account deletion fence survives older backup restore |
| LIFECYCLE-02 | T36 | T40 | Conversation subtree asynchronous fenced delete, existing memories retained |
| INFER-01 | T13 | T28 | Fake and Ollama providers share typed port; OpenVINO not in v1 |
| GOV-01 | S00 | T41 | Required branch PR review and human merge approval, protected paths |
| GOV-02 | S01 | T28 | Versioned OpenAPI/SSE/error fixtures compatible and checksum validated |

Existing IDs in root `TRACEABILITY.md` and `backend/ACCEPTANCE_TESTS.md` remain normative requirements; S00/S01 and owners must reconcile them into this machine-checked registry before marking implementation tasks ready. An absent executable test remains **not verified**. Every MUST/SHALL needs assigned coverage, including negative failure paths.
