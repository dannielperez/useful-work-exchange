# Architecture decision

Status accepted for implementation, 2026-11-13. The current executable is the Python CSV pilot. The Rust system below is the target architecture.

## Language and process boundaries

Use Rust 2021 for the coordinator and worker agent, with Tokio, Axum, SQLx, Serde, tracing and ed25519-dalek. Commit Cargo.lock for reproducible application builds. Use a modular monolith with one coordinator deployment and independently running workers. Python 3.12 implements approved dataset and AI workloads. Blender runs as its own pinned workload image. Future browser UI uses TypeScript and calls the same HTTP API.

Create Cargo workspace crates exchange-core, exchange-api and exchange-agent. Core owns workload profiles, checked credit arithmetic, state transitions and receipt encoding. API owns HTTP authentication, PostgreSQL transactions, object authorization, scheduler and deadline sweeper. Agent owns polling, image allowlisting, Docker execution and result retries. Use reqwest and clap for agent networking and commands. Keep cryptography and sandboxing in established libraries and runtimes.

## Persistence and execution

PostgreSQL is the pilot authority for accounts, quotes, jobs, blocks, leases, transfers and receipts. Adopt numbered SQLx migrations. Retain the existing schema semantics during porting. Start with the existing global transaction lock to preserve pilot ordering. Replace it only after concurrency benchmarks and lock-order tests establish equivalent behavior.

Private content-addressed objects live on a persistent coordinator volume through a storage interface. Write and fsync objects before committing references. Unreferenced objects may survive a failed transaction and are reclaimed only by a future explicit retention job. Add an S3-compatible implementation when multiple API replicas are needed. PostgreSQL-backed leases provide the work queue. A periodic database sweep handles expiry and refunds.

Workers use approved digest-pinned Linux containers, disabled network, read-only filesystem, bounded memory and CPU, and a wall-clock timeout. Distinct operator identities execute work and verification. Matching submissions settle, disagreements invoke the trusted reference computation. Enrollment establishes operator independence for this pilot. Colluding operators can agree on false results, so permissionless enrollment requires a separate verification design.

## Economics and contracts

Keep the 10,000-credit genesis supply, 1,000-row blocks, 8-credit worker payout and 2-credit verifier payout. Credits move between treasury, customer, escrow and providers using an append-only transfer journal. Enforce nonnegative spendable balances and reconcile journal totals with account balances. Replay of job submission and identical results is idempotent. A failed settlement transaction rolls back payouts, receipt insertion and job state together.

Retain /v1/quotes, /v1/jobs/{quote}, /v1/jobs/{job}, /v1/jobs/{job}/result, /v1/leases, /v1/leases/{lease}/input, /v1/leases/{lease}/result, /v1/wallet, /v1/receipts and /v1/key. Existing Python handlers define methods and payloads. Capture them as fixtures and OpenAPI before porting. Preserve integer JSON values, sorted compact ASCII JSON and Ed25519 signature bytes. Rust's default Unicode JSON encoding differs from Python ensure_ascii and requires explicit compatibility work. CSV sums may exceed i64, so aggregate in i128 with checked operations and validate the established bounds.

## Migration and operation

Port core behavior first, then database operations, then HTTP and the container agent. Use differential tests against Python for Unicode groups, integer boundaries, CSV quoting, receipts, failed blocks and deadlines. Drain jobs before cutover. Back up PostgreSQL and object volume, rehearse restore, and test Rust against a copy. Switch traffic after parity checks pass. Roll back the executable only while the schema remains compatible. Record every schema compatibility boundary.

Deploy coordinator behind HTTPS with request size limits and per-account concurrency limits. Log job IDs, timings and transitions without payloads or credentials. Export queue age, lease expirations, adjudications, payout totals, ledger reconciliation failures and API errors. Keep receipt key and database credentials in private deployment secrets. Rotate signing keys using a versioned key identifier before supporting multiple active keys.

## Expansion order

CSV pilot precedes Blender frame rendering, then pinned ONNX batch classification, then audited GiB-day storage. Each workload profile defines input bounds, unit price, sharding, comparison, timeout, resource limits and merge behavior. Benchmark verification cost and customer utility before enabling payouts. Token issuance, cash redemption and blockchain settlement require a later economic and protocol decision.
