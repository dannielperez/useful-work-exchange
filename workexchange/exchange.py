import base64
import csv
import json
import re
import secrets
import time
from uuid import uuid4

from .workload import VERSION, canonical, compute, digest, merge, parse_csv, validate_output

SUPPLY = 10_000
WORKER_RATE = 8
VERIFIER_RATE = 2
QUOTE_SECONDS = 900
LEASE_SECONDS = 120
JOB_SECONDS = 3600
NAME = re.compile(r"[a-z][a-z0-9_-]{0,62}")


class ExchangeError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Exchange:
    def __init__(self, database, objects, signer, image, clock=time.time):
        self.database = database
        self.objects = objects
        self.signer = signer
        self.image = image
        self.clock = clock

    def initialize(self):
        self.database.initialize()
        with self.database.transaction() as query:
            if query("SELECT id FROM accounts WHERE id = 'treasury'").fetchone():
                return
            for name in ("treasury", "issuance"):
                query("INSERT INTO accounts(id,operator,role) VALUES (?,?,?)", name, name, name)
            self.transfer(query, "genesis", "issuance", "treasury", SUPPLY, int(self.clock()))

    def transfer(self, query, identifier, debit, credit, amount, now):
        source = query("SELECT balance FROM accounts WHERE id = ?", debit).fetchone()
        target = query("SELECT id FROM accounts WHERE id = ?", credit).fetchone()
        if source is None or target is None:
            raise ExchangeError(404, "Account does not exist")
        if debit != "issuance" and source["balance"] < amount:
            raise ExchangeError(409, "Insufficient credits")
        query("INSERT INTO transfers VALUES (?,?,?,?,?)", identifier, debit, credit, amount, now)
        query("UPDATE accounts SET balance = balance - ? WHERE id = ?", amount, debit)
        query("UPDATE accounts SET balance = balance + ? WHERE id = ?", amount, credit)
        return {"id": identifier, "debit": debit, "credit": credit, "amount": amount}

    def enroll(self, name, operator, role):
        if not NAME.fullmatch(name) or not NAME.fullmatch(operator) or role not in ("member", "worker", "verifier"):
            raise ExchangeError(400, "Invalid account, operator or role")
        token = secrets.token_urlsafe(32)
        with self.database.transaction() as query:
            if query("SELECT id FROM accounts WHERE id = ?", name).fetchone():
                raise ExchangeError(409, "Account already exists")
            query("INSERT INTO accounts(id,operator,role,token_hash) VALUES (?,?,?,?)", name, operator, role, digest(token.encode()))
        return token

    def grant(self, account, amount):
        if not 0 < amount <= SUPPLY:
            raise ExchangeError(400, "Invalid allocation")
        with self.database.transaction() as query:
            if not query("SELECT id FROM accounts WHERE id = ? AND token_hash IS NOT NULL", account).fetchone():
                raise ExchangeError(404, "Participant does not exist")
            return self.transfer(query, uuid4().hex, "treasury", account, amount, int(self.clock()))

    def authenticate(self, token):
        with self.database.transaction() as query:
            account = query("SELECT id,operator,role FROM accounts WHERE token_hash = ?", digest(token.encode())).fetchone()
            if account is None:
                raise ExchangeError(401, "Invalid participant token")
            return dict(account)

    def quote(self, account, data):
        try:
            blocks = parse_csv(data)
        except (ValueError, csv.Error) as error:
            raise ExchangeError(400, str(error)) from error
        original = self.objects.put(data)
        manifest = [[count, self.objects.put(content)] for count, content in blocks]
        identifier = uuid4().hex
        expires = int(self.clock()) + QUOTE_SECONDS
        with self.database.transaction() as query:
            query("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?)", identifier, account, VERSION, self.image, original,
                  canonical(manifest).decode(), WORKER_RATE, VERIFIER_RATE, expires)
        return {"id": identifier, "workload": VERSION, "image": self.image, "units": len(blocks), "input_hash": original,
                "worker_rate": WORKER_RATE, "verifier_rate": VERIFIER_RATE,
                "price": len(blocks) * (WORKER_RATE + VERIFIER_RATE), "expires": expires}

    def submit(self, account, quote_id):
        with self.database.transaction() as query:
            now = int(self.clock())
            quote = query("SELECT * FROM quotes WHERE id = ? AND owner = ?", quote_id, account).fetchone()
            if quote is None:
                raise ExchangeError(404, "Quote does not exist")
            previous = query("SELECT id,deadline,status FROM jobs WHERE quote_id = ?", quote_id).fetchone()
            if previous:
                return dict(previous)
            if quote["expires"] <= now:
                raise ExchangeError(409, "Quote expired")
            manifest = json.loads(quote["blocks"])
            job = "job_" + uuid4().hex
            deadline = now + JOB_SECONDS
            query("INSERT INTO accounts(id,operator,role) VALUES (?,?,'escrow')", job, job)
            self.transfer(query, job + ":reserve", account, job,
                          len(manifest) * (quote["worker_rate"] + quote["verifier_rate"]), now)
            query("INSERT INTO jobs(id,quote_id,created,deadline,status) VALUES (?,?,?,?,'running')", job, quote_id, now, deadline)
            for number, (count, key) in enumerate(manifest, 1):
                query("INSERT INTO blocks(job,number,input_hash,row_count,state) VALUES (?,?,?,?,'pending')", job, number, key, count)
            return {"id": job, "deadline": deadline, "status": "running"}

    def tick(self):
        with self.database.transaction() as query:
            now = int(self.clock())
            query("UPDATE leases SET state = 'expired' WHERE state = 'leased' AND expires <= ?", now)
            for job in query("SELECT id FROM jobs WHERE status = 'running' AND deadline <= ?", now).fetchall():
                self.close(query, job["id"], now)

    def claim(self, account):
        role = account["role"]
        if role not in ("worker", "verifier"):
            raise ExchangeError(403, "Worker or verifier enrollment required")
        with self.database.transaction() as query:
            now = int(self.clock())
            query("UPDATE leases SET state = 'expired' WHERE state = 'leased' AND expires <= ?", now)
            lease = query("""
                SELECT l.id,l.job,l.number,l.expires,b.input_hash,q.workload,q.image
                FROM leases l JOIN blocks b ON b.job = l.job AND b.number = l.number
                JOIN jobs j ON j.id = b.job JOIN quotes q ON q.id = j.quote_id
                WHERE l.account = ? AND l.state = 'leased'
                """, account["id"]).fetchone()
            if lease:
                return dict(lease)
            block = query("""
                SELECT b.job,b.number,b.input_hash,j.deadline,q.workload,q.image,
                    (SELECT COUNT(*) FROM leases l WHERE l.job=b.job AND l.number=b.number AND l.role=?) AS attempts
                FROM blocks b JOIN jobs j ON j.id=b.job JOIN quotes q ON q.id=j.quote_id
                WHERE b.state='pending' AND j.status='running' AND j.deadline > ?
                    AND NOT EXISTS (
                        SELECT 1 FROM leases l JOIN accounts a ON a.id=l.account
                        WHERE l.job=b.job AND l.number=b.number AND (
                            (l.role=? AND l.state IN ('leased','submitted')) OR a.operator=?
                        )
                    )
                    AND (SELECT COUNT(*) FROM leases l WHERE l.job=b.job AND l.number=b.number AND l.role=?) < 2
                ORDER BY j.deadline,b.job,b.number LIMIT 1
                """, role, now, role, account["operator"], role).fetchone()
            if block is None:
                return None
            lease_id = uuid4().hex
            expires = min(now + LEASE_SECONDS, block["deadline"])
            query("INSERT INTO leases(id,job,number,role,attempt,account,assigned,expires,state) VALUES (?,?,?,?,?,?,?,?,'leased')",
                  lease_id, block["job"], block["number"], role, block["attempts"] + 1, account["id"], now, expires)
            return {"id": lease_id, "job": block["job"], "number": block["number"], "expires": expires,
                    "input_hash": block["input_hash"], "workload": block["workload"], "image": block["image"]}

    def input(self, account, lease_id):
        with self.database.transaction() as query:
            row = query("""SELECT l.expires,l.state,b.input_hash FROM leases l
                           JOIN blocks b ON b.job=l.job AND b.number=l.number WHERE l.id=? AND l.account=?""",
                        lease_id, account).fetchone()
            if row is None:
                raise ExchangeError(404, "Assignment does not exist")
            if row["state"] != "leased" or row["expires"] <= int(self.clock()):
                raise ExchangeError(409, "Assignment is no longer active")
            key = row["input_hash"]
        return self.objects.get(key)

    def result(self, account, lease_id, data):
        with self.database.transaction() as query:
            now = int(self.clock())
            lease = query("""SELECT l.*,b.row_count,b.input_hash FROM leases l
                             JOIN blocks b ON b.job=l.job AND b.number=l.number WHERE l.id=? AND l.account=?""",
                          lease_id, account).fetchone()
            if lease is None:
                raise ExchangeError(404, "Assignment does not exist")
            if lease["state"] == "submitted" and lease["output_hash"] == digest(data):
                return {"received": True}
            if lease["state"] != "leased" or lease["expires"] <= now:
                raise ExchangeError(409, "Assignment is no longer active")
            try:
                validate_output(data, lease["row_count"])
            except ValueError as error:
                raise ExchangeError(400, str(error)) from error
            key = self.objects.put(data)
            query("UPDATE leases SET state='submitted',output_hash=?,submitted=? WHERE id=?", key, now, lease_id)
            job, number = lease["job"], lease["number"]
            submissions = query("SELECT * FROM leases WHERE job=? AND number=? AND state='submitted'", job, number).fetchall()
            if len(submissions) == 2:
                if submissions[0]["output_hash"] != submissions[1]["output_hash"]:
                    key = self.objects.put(compute(self.objects.get(lease["input_hash"])))
                self.settle(query, job, number, key, submissions, now)
                self.close(query, job, now)
            return {"received": True}

    def settle(self, query, job, number, key, submissions, now):
        row = query("""SELECT q.*,b.input_hash AS block_hash FROM blocks b JOIN jobs j ON j.id=b.job
                       JOIN quotes q ON q.id=j.quote_id WHERE b.job=? AND b.number=?""", job, number).fetchone()
        movements = []
        refund = row["worker_rate"] + row["verifier_rate"]
        for submission in sorted(submissions, key=lambda item: item["role"]):
            if submission["output_hash"] == key:
                role = submission["role"]
                amount = row[role + "_rate"]
                movements.append(self.transfer(query, f"{job}:{number}:{role}", job, submission["account"], amount, now))
                refund -= amount
        if refund:
            movements.append(self.transfer(query, f"{job}:{number}:refund", job, row["owner"], refund, now))
        state = "accepted" if key else "failed"
        query("UPDATE blocks SET state=?,output_hash=? WHERE job=? AND number=?", state, key, job, number)
        document = canonical({"job": job, "block": number, "workload": row["workload"], "image": row["image"], "input_hash": row["block_hash"],
                              "output_hash": key, "state": state, "movements": movements, "timestamp": now})
        signature = base64.b64encode(self.signer.sign(document)).decode()
        query("INSERT INTO receipts VALUES (?,?,?,?)", job, number, document.decode(), signature)

    def close(self, query, job, now):
        record = query("SELECT deadline FROM jobs WHERE id=?", job).fetchone()
        blocks = query("SELECT * FROM blocks WHERE job=? ORDER BY number", job).fetchall()
        pending = [block for block in blocks if block["state"] == "pending"]
        if pending and now < record["deadline"]:
            return
        for block in pending:
            self.settle(query, job, block["number"], None, [], now)
        accepted = [block["output_hash"] for block in blocks if block["state"] == "accepted"]
        output = self.objects.put(merge(self.objects.get(key) for key in accepted))
        status = "failed" if not accepted else "complete" if len(accepted) == len(blocks) else "partial"
        query("UPDATE jobs SET status=?,output_hash=?,closed=? WHERE id=?", status, output, now, job)

    def job(self, account, identifier, download=False):
        with self.database.transaction() as query:
            job = query("""SELECT j.id,j.status,j.deadline,j.output_hash,q.workload,q.input_hash FROM jobs j
                           JOIN quotes q ON q.id=j.quote_id WHERE j.id=? AND q.owner=?""", identifier, account).fetchone()
            if job is None:
                raise ExchangeError(404, "Job does not exist")
            blocks = query("SELECT number,state,row_count FROM blocks WHERE job=? ORDER BY number", identifier).fetchall()
        if download:
            if job["output_hash"] is None:
                raise ExchangeError(409, "Job is still running")
            return self.objects.get(job["output_hash"])
        return {**dict(job), "blocks": [dict(block) for block in blocks],
                "missing_blocks": [block["number"] for block in blocks if block["state"] == "failed"]}

    def wallet(self, account):
        with self.database.transaction() as query:
            row = query("SELECT balance FROM accounts WHERE id=?", account).fetchone()
            rows = query("SELECT * FROM transfers WHERE debit=? OR credit=? ORDER BY created,id", account, account).fetchall()
            return {"account": account, "balance": row["balance"], "transfers": [dict(item) for item in rows]}

    def receipts(self, account):
        with self.database.transaction() as query:
            rows = query("""SELECT r.document,r.signature FROM receipts r JOIN jobs j ON j.id=r.job
                JOIN quotes q ON q.id=j.quote_id WHERE q.owner=? OR EXISTS (
                    SELECT 1 FROM leases l WHERE l.job=r.job AND l.number=r.number AND l.account=? AND l.state='submitted'
                ) ORDER BY r.job,r.number""", account, account).fetchall()
            return [{"document": json.loads(row["document"]), "signature": row["signature"]} for row in rows]

    def audit(self):
        with self.database.transaction() as query:
            rows = query("""SELECT a.id,a.balance,a.role,COALESCE(SUM(entries.delta),0) AS expected
                FROM accounts a LEFT JOIN (
                    SELECT debit AS account,-amount AS delta FROM transfers
                    UNION ALL SELECT credit AS account,amount AS delta FROM transfers
                ) entries ON entries.account=a.id GROUP BY a.id,a.balance,a.role""").fetchall()
            supply = sum(row["balance"] for row in rows if row["role"] != "issuance")
            mismatches = [row["id"] for row in rows if row["balance"] != row["expected"]]
            return {"supply": supply, "mismatches": mismatches, "balanced": supply == SUPPLY and not mismatches}

    def metrics(self):
        with self.database.transaction() as query:
            jobs = query("""SELECT j.id,j.status,j.closed-j.created AS elapsed_seconds,q.owner
                            FROM jobs j JOIN quotes q ON q.id=j.quote_id ORDER BY j.created,j.id""").fetchall()
            blocks = query("SELECT state,COUNT(*) AS total FROM blocks GROUP BY state").fetchall()
            leases = query("""SELECT role,state,COUNT(*) AS total,SUM(submitted-assigned) AS dispatch_seconds
                              FROM leases GROUP BY role,state""").fetchall()
            rewards = query("""SELECT l.role,SUM(t.amount) AS credits FROM leases l JOIN transfers t
                               ON t.id = l.job || ':' || CAST(l.number AS TEXT) || ':' || l.role
                               AND t.credit = l.account WHERE l.state='submitted' GROUP BY l.role""").fetchall()
        return {"jobs": [dict(row) for row in jobs], "blocks": {row["state"]: row["total"] for row in blocks},
                "leases": [{**dict(row), "dispatch_seconds": int(row["dispatch_seconds"] or 0)} for row in leases],
                "payouts": {row["role"]: int(row["credits"]) for row in rewards}}
