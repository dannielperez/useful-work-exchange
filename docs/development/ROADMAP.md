# Ordered implementation backlog

Each item is a bounded shift task. Finish and record evidence before advancing.

- [ ] D01 — Add Cargo workspace and locked dependencies for core, API and agent. Acceptance requires format, clippy and tests on Windows and Linux. Add CI for Python and Rust. Document exact supported toolchain.
- [ ] D02 — Capture Python HTTP contracts as OpenAPI and synthetic golden fixtures. Cover all endpoints, authentication, input limits, duplicate requests and error status codes. Acceptance requires Python fixture generation and validation without real participant data.
- [ ] D03 — Implement core aggregate arithmetic, canonical bytes and receipt verification in Rust. Acceptance requires byte-for-byte Python parity including Unicode, extreme integers and tampered signatures.
- [ ] D04 — Introduce PostgreSQL migrations and Rust transactional repository. Acceptance requires reserve, retry, payout, refund, concurrent overspend and crash rollback integration tests against a real PostgreSQL instance.
- [ ] D05 — Implement Axum API, scheduler and sweeper with bounded requests and authentication. Acceptance requires the D02 contract suite to pass against both implementations and expired leases to remain unpayable.
- [ ] D06 — Implement Rust worker CLI and container lifecycle. Acceptance requires a real Linux Docker run, timeout cleanup, digest mismatch rejection, independent verification and safe submission retries.
- [ ] D07 — Run the complete two-worker pilot on PostgreSQL and Docker, including a second purchase funded by earnings. Record metrics, reconciliation, backup and restore evidence. Acceptance requires zero unexplained balance discrepancies.
- [ ] D08 — Rehearse Python-to-Rust cutover and rollback with copied state. Publish an operator runbook and versioned release once all compatibility gates pass.
- [ ] D09 — Add the Blender workload profile and reference corpus. Measure CPU time, output agreement and verification cost before setting its posted rate.
- [ ] D10 — Add pinned ONNX image classification with reference labels and measured rates. Validate supported host agreement before enabling settlement.

Pilot success requires two customers, three independent operators, 20 useful jobs, 95% timely block acceptance and a purchase entirely funded by earnings. Recruitment and four weeks of real usage are operational work beyond automated code completion.
