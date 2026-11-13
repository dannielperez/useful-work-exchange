# Development contract

Read docs/development/ARCHITECTURE.md, docs/development/ROADMAP.md and docs/ai/HANDOFF.md before implementation. The active project is Useful Work Exchange.

Implement the first unchecked roadmap item whose dependencies are complete. Work in a feature branch. Keep changes scoped to that item and verify its acceptance criteria. Record actual commands and outcomes in the handoff. Mark an item complete only after its checks pass. Record blockers explicitly and stop if no eligible item remains.

Rust owns the future coordinator and worker agent. Python remains the executable reference and workload runtime during migration. Preserve the documented HTTP behavior and byte-level receipt compatibility until an explicit versioned change. Money uses checked integer operations. Scheduling and ledger changes commit together. Retry and crash behavior require regression tests.

Run the Python suite on every compatibility change. When Rust crates exist, run cargo fmt --check, cargo clippy --workspace --all-targets -- -D warnings, and cargo test --workspace --locked. Add PostgreSQL integration tests for persistence changes. Never report unexecuted checks as passing.

Keep credentials, real inputs, receipts and runtime state outside committed files. Automation may edit, test and commit its task branch. Publishing branches, merging and deployments require the scope of the active user instruction. The unattended development shift is scoped to local implementation and handoff. Preserve user edits and existing worktrees.
