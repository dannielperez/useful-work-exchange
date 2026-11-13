# Useful Work Exchange

An invite-only compute pool for public or explicitly shareable datasets. Customers buy CSV aggregations with work credits. Workers and independent verifiers earn credits for accepted blocks and can spend them on jobs of their own.

## Try it locally

Use Python 3.12 or newer. Run these commands from the project directory in PowerShell. The local mode uses SQLite and runs the built-in workload directly. It binds to loopback.

```powershell
python -m pip install -r requirements.txt
python -m workexchange init
python -m workexchange audit
$buyer = python -m workexchange enroll buyer buyer-team member | ConvertFrom-Json
$worker1 = python -m workexchange enroll worker1 operator-one worker | ConvertFrom-Json
$worker2 = python -m workexchange enroll worker2 operator-two worker | ConvertFrom-Json
$verifier = python -m workexchange enroll verifier operator-three verifier | ConvertFrom-Json
python -m workexchange grant buyer 100
```

In a second terminal, start `python -m workexchange serve`. In the first terminal, submit 1,001 rows, run two workers and a verifier, and verify the signed receipts. Use `-Encoding ascii` for the sample file so PowerShell emits no byte-order mark.

```powershell
@('group,value') + (@('a,1') * 1000) + @('b,5') | Set-Content -Encoding ascii sample.csv
$env:WORK_TOKEN = $buyer.token
$quote = python -m workexchange quote sample.csv | ConvertFrom-Json
$job = python -m workexchange submit $quote.id | ConvertFrom-Json
$env:WORK_TOKEN = $worker1.token
python -m workexchange work --local --once
$env:WORK_TOKEN = $worker2.token
python -m workexchange work --local --once
$env:WORK_TOKEN = $verifier.token
python -m workexchange work --local --once
python -m workexchange work --local --once
$env:WORK_TOKEN = $buyer.token
python -m workexchange download $job.id result.csv
$key = (python -m workexchange key | ConvertFrom-Json).ed25519
python -m workexchange receipts | Set-Content -Encoding ascii receipts.json
python -m workexchange verify-receipts $key receipts.json
python -m workexchange metrics
python -m workexchange audit
```

The customer pays 20 credits. Each worker earns 8 and the verifier earns 4. `result.csv` contains counts and sums. `result.csv.json` records the job status and any missing blocks. Treat `partial` output as an aggregation over the accepted blocks only.

## Deploy the invite-only pilot

1. Provision a dedicated PostgreSQL database and a persistent, private coordinator volume. Set `WORK_DATABASE_URL` to the PostgreSQL DSN and `WORK_STATE` to the volume directory. The coordinator stores private objects and the receipt signing key there. Back up the database and volume together. Keep the signing key private and distribute its public key to participants during enrollment.
2. Build `Dockerfile.worker` for Linux x86-64, publish it to your container registry, and set `WORK_IMAGE` on the coordinator and operators to the same `registry/repository@sha256:…` reference. Run `docker pull` with that reference on each operator host before starting the worker. Each lease carries the image reference from the original quote. Drain jobs before changing the approved image.
3. Run `python -m workexchange init` and start `python -m workexchange serve` behind an HTTPS reverse proxy on the same host. Configure the proxy to accept uploads up to 25 MiB. Distribute the HTTPS URL as `WORK_URL` and give each participant their own `WORK_TOKEN` from `enroll`. Grant starter credits to customers or sponsors using `grant`. Use consistent operator identifiers for accounts controlled by the same person or organization.
4. Start `python -m workexchange work` on each Linux worker and verifier host. Use distinct operator identities for the two roles on a block. The worker runs an approved container with no network, a read-only root filesystem, resource limits, and a 60-second timeout. Participants may use the same account token to buy jobs and to perform their enrolled role.

CSV input requires the header `group,value`, at least one row, nonempty group keys of up to 256 UTF-8 bytes, and signed 64-bit values. Aggregated sums use exact integers. There are up to 1,000 rows per block, two minutes per lease, one retry per role with a different operator, 15 minutes to accept a quote, and one hour to finish a job. The coordinator settles matching results immediately, adjudicates disagreements against its reference calculation, and refunds unresolved work at the deadline. The 10,000-credit treasury is the only source of starter balances. Credits have no cash redemption.

## Measure the pilot

Recruit at least two customers and three independent operators for a four-week pilot. Aim for 20 customer-requested jobs, 95% of blocks accepted by deadline, zero ledger discrepancies, and at least one completed purchase paid entirely with earned credits.

Run `metrics` and `audit` weekly. Record repeat customers, block completion, job elapsed time, and worker and verifier payouts. Compare execution time from the operator logs and end-to-end latency against running the same inputs through `parse_csv`, `compute`, and `merge` sequentially on one reference host. Track verifier execution separately and record when providers spend earned credits using their wallet history.

The first release implements CSV transforms. Expand to approved Blender frame profiles and pinned ONNX batch image classification after the pilot verifies pooling and settlement. Add GiB-day storage with audit and retrieval checks after compute. The exchange currently accepts the single built-in workload profile.

## Verification

```powershell
python -m unittest discover -s tests -v
```

Set `WORK_TEST_POSTGRES_URL` to a disposable PostgreSQL database to run the same suite against PostgreSQL. The test user needs permission to create and drop temporary test schemas. The HTTP tests exercise the built-in local runner. Test Docker on a Linux operator by running a quoted job with the published image and checking that both worker and verifier settle successfully.

## Repository and pilot data

The public repository contains the application source, schema, tests with synthetic inputs, worker image recipe, and generic setup instructions. Keep participant identities, enrollment tokens, database credentials, signing keys, real datasets, job outputs, receipts, and pilot metrics in private operational storage. The default `.state/` directory is Git-ignored. Set `WORK_STATE` to a directory outside the checkout for a deployed coordinator.
