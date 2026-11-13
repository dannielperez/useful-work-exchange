CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    operator TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('member', 'worker', 'verifier', 'treasury', 'escrow', 'issuance')),
    token_hash TEXT UNIQUE,
    balance BIGINT NOT NULL DEFAULT 0 CHECK (balance >= 0 OR role = 'issuance')
);
CREATE TABLE IF NOT EXISTS exchange_lock (id INTEGER PRIMARY KEY);
INSERT INTO exchange_lock (id) VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS quotes (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL REFERENCES accounts(id),
    workload TEXT NOT NULL,
    image TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    blocks TEXT NOT NULL,
    worker_rate INTEGER NOT NULL,
    verifier_rate INTEGER NOT NULL,
    expires BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY REFERENCES accounts(id),
    quote_id TEXT NOT NULL UNIQUE REFERENCES quotes(id),
    created BIGINT NOT NULL,
    deadline BIGINT NOT NULL,
    closed BIGINT,
    status TEXT NOT NULL CHECK (status IN ('running', 'complete', 'partial', 'failed')),
    output_hash TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
    job TEXT NOT NULL REFERENCES jobs(id),
    number INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'accepted', 'failed')),
    output_hash TEXT,
    PRIMARY KEY (job, number)
);
CREATE TABLE IF NOT EXISTS leases (
    id TEXT PRIMARY KEY,
    job TEXT NOT NULL,
    number INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('worker', 'verifier')),
    attempt INTEGER NOT NULL CHECK (attempt IN (1, 2)),
    account TEXT NOT NULL REFERENCES accounts(id),
    assigned BIGINT NOT NULL,
    expires BIGINT NOT NULL,
    submitted BIGINT,
    state TEXT NOT NULL CHECK (state IN ('leased', 'submitted', 'expired')),
    output_hash TEXT,
    FOREIGN KEY (job, number) REFERENCES blocks(job, number),
    UNIQUE (job, number, role, attempt)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_lease ON leases(account) WHERE state = 'leased';
CREATE TABLE IF NOT EXISTS transfers (
    id TEXT PRIMARY KEY,
    debit TEXT NOT NULL REFERENCES accounts(id),
    credit TEXT NOT NULL REFERENCES accounts(id),
    amount BIGINT NOT NULL CHECK (amount > 0),
    created BIGINT NOT NULL,
    CHECK (debit <> credit)
);
CREATE TABLE IF NOT EXISTS receipts (
    job TEXT NOT NULL,
    number INTEGER NOT NULL,
    document TEXT NOT NULL,
    signature TEXT NOT NULL,
    PRIMARY KEY (job, number),
    FOREIGN KEY (job, number) REFERENCES blocks(job, number)
);
