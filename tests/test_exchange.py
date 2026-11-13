import base64
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workexchange.__main__ import Client
from workexchange.api import Handler, ThreadingHTTPServer
from workexchange.database import Database
from workexchange.exchange import JOB_SECONDS, LEASE_SECONDS, QUOTE_SECONDS, Exchange, ExchangeError
from workexchange.storage import ObjectStore
from workexchange.worker import work
from workexchange.workload import canonical, compute, merge, parse_csv

DATA = b"group,value\nb,4\na,1\n" + b"a,1\n" * 998 + b"a,-2\n"


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory(dir=Path.cwd()))
        self.now = 10_000
        pg_url = os.getenv("WORK_TEST_POSTGRES_URL")
        if pg_url:
            import psycopg
            from psycopg.conninfo import make_conninfo

            schema = "work_test_" + uuid4().hex
            admin = self.enterContext(psycopg.connect(pg_url, autocommit=True))
            admin.execute(f"CREATE SCHEMA {schema}")
            self.addCleanup(admin.execute, f"DROP SCHEMA {schema} CASCADE")
            database = Database(make_conninfo(pg_url, options=f"-c search_path={schema}"))
        else:
            database = Database(f"sqlite:///{Path(temp) / 'test.db'}")
        self.exchange = Exchange(database, ObjectStore(Path(temp) / "objects"), Ed25519PrivateKey.generate(),
                                 "local", lambda: self.now)
        self.exchange.initialize()
        self.tokens = {}
        self.people = {}
        for name, operator, role in (("buyer", "buyer", "member"), ("worker1", "one", "worker"),
                                     ("worker2", "two", "worker"), ("verifier", "three", "verifier"),
                                     ("same_operator", "one", "verifier")):
            self.tokens[name] = self.exchange.enroll(name, operator, role)
            self.people[name] = self.exchange.authenticate(self.tokens[name])
        self.exchange.grant("buyer", 20)

    def submit(self, data=DATA):
        quote = self.exchange.quote("buyer", data)
        return self.exchange.submit("buyer", quote["id"])["id"]

    def claim(self, name):
        return self.exchange.claim(self.people[name])

    def finish(self, name, lease, result=None):
        if result is None:
            result = compute(self.exchange.input(name, lease["id"]))
        return self.exchange.result(name, lease["id"], result)

    def test_pool_and_spend_earned_credits_over_http(self):
        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            server.exchange = self.exchange
            thread = Thread(target=server.serve_forever)
            thread.start()
            try:
                clients = {name: Client(f"http://127.0.0.1:{server.server_port}", token)
                           for name, token in self.tokens.items()}
                buyer = clients["buyer"]
                quote = buyer.json("POST", "/v1/quotes", DATA)
                self.assertEqual(quote["price"], 20)
                job = buyer.json("POST", "/v1/jobs/" + quote["id"])["id"]
                self.assertEqual(buyer.json("POST", "/v1/jobs/" + quote["id"])["id"], job)
                with ThreadPoolExecutor(2) as pool:
                    tasks = [pool.submit(work, clients[name], "local", True, True) for name in ("worker1", "worker2")]
                    for task in tasks:
                        task.result()
                for _ in range(2):
                    work(clients["verifier"], "local", True, True)
                result = buyer.request("GET", f"/v1/jobs/{job}/result")
                local_rows = [row for _, block in parse_csv(DATA) for row in json.loads(block)]
                self.assertEqual(result, merge([compute(canonical(local_rows))]))
                self.assertEqual(result, b"group,count,sum\r\na,1000,997\r\nb,1,4\r\n")
                key = self.exchange.signer.public_key()
                receipts = buyer.json("GET", "/v1/receipts")
                self.assertEqual(len(receipts), 2)
                for receipt in receipts:
                    signature = base64.b64decode(receipt["signature"])
                    key.verify(signature, canonical(receipt["document"]))
                    with self.assertRaises(InvalidSignature):
                        key.verify(signature, canonical({**receipt["document"], "timestamp": 0}))
                self.assertEqual([clients[name].json("GET", "/v1/wallet")["balance"]
                                  for name in ("buyer", "worker1", "worker2", "verifier")], [0, 8, 8, 4])
                # One more block supplies the second worker payout needed to buy a block.
                self.exchange.grant("buyer", 10)
                next_quote = buyer.json("POST", "/v1/quotes", b"group,value\na,5\n")
                buyer.json("POST", "/v1/jobs/" + next_quote["id"])
                work(clients["worker1"], "local", True, True)
                work(clients["verifier"], "local", True, True)
                provider = clients["worker1"]
                earned_quote = provider.json("POST", "/v1/quotes", b"group,value\na,8\n")
                earned_job = provider.json("POST", "/v1/jobs/" + earned_quote["id"])["id"]
                self.assertEqual(provider.json("GET", "/v1/wallet")["balance"], 6)
                work(clients["worker2"], "local", True, True)
                work(clients["verifier"], "local", True, True)
                self.assertEqual(provider.json("GET", f"/v1/jobs/{earned_job}")["status"], "complete")
                with self.assertRaises(ExchangeError):
                    provider.json("GET", f"/v1/jobs/{job}")
                self.assertTrue(self.exchange.audit()["balanced"])
                self.assertEqual(self.exchange.metrics()["payouts"], {"worker": 32, "verifier": 8})
            finally:
                server.shutdown()
                thread.join()

    def test_disagreement_wrong_worker_is_unpaid(self):
        job = self.submit(b"group,value\na,5\n")
        worker = self.claim("worker1")
        self.assertIsNone(self.claim("same_operator"))
        verifier = self.claim("verifier")
        wrong = canonical([["a", 1, 9]])
        self.finish("worker1", worker, wrong)
        data = compute(self.exchange.input("verifier", verifier["id"]))
        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(self.finish, "verifier", verifier, data) for _ in range(2)]
            for task in tasks:
                self.assertEqual(task.result(), {"received": True})
        self.assertEqual(self.exchange.wallet("worker1")["balance"], 0)
        self.assertEqual(self.exchange.wallet("verifier")["balance"], 2)
        self.assertEqual(self.exchange.wallet("buyer")["balance"], 18)
        self.assertEqual(self.exchange.job("buyer", job, True), b"group,count,sum\r\na,1,5\r\n")

    def test_expiry_retry_partial_result_and_restart(self):
        job = self.submit()
        expired = self.claim("worker1")
        self.now += LEASE_SECONDS
        retry = self.claim("worker2")
        self.assertNotEqual(expired["id"], retry["id"])
        with self.assertRaisesRegex(ExchangeError, "no longer active"):
            self.finish("worker1", expired, b"[]")
        self.finish("worker2", retry)
        self.finish("verifier", self.claim("verifier"))
        self.now += JOB_SECONDS
        restarted = Exchange(self.exchange.database, self.exchange.objects, self.exchange.signer, "local", lambda: self.now)
        restarted.initialize()
        restarted.tick()
        restarted.tick()
        self.assertEqual(restarted.job("buyer", job)["missing_blocks"], [2])
        self.assertEqual(restarted.job("buyer", job)["status"], "partial")
        self.assertEqual(restarted.wallet("buyer")["balance"], 10)
        self.assertEqual(restarted.wallet(job)["balance"], 0)
        self.assertEqual(len(restarted.receipts("buyer")), 2)
        self.assertTrue(restarted.audit()["balanced"])

    def test_bad_input_and_expired_quote(self):
        for bad in (b"group,value\n", b"group,value\na,1.2\n", b"group,value\na,9223372036854775808\n",
                    b'group,value\n"a,2\n', b"group,value\n\xff,1\n"):
            with self.assertRaises(ExchangeError):
                self.exchange.quote("buyer", bad)
        quote = self.exchange.quote("buyer", DATA)
        self.now += QUOTE_SECONDS
        with self.assertRaisesRegex(ExchangeError, "Quote expired"):
            self.exchange.submit("buyer", quote["id"])
        self.assertEqual(self.exchange.wallet("buyer")["balance"], 20)

    def test_concurrent_reservations_cannot_overspend(self):
        quotes = [self.exchange.quote("buyer", DATA) for _ in range(2)]
        with ThreadPoolExecutor(2) as pool:
            tasks = [pool.submit(self.exchange.submit, "buyer", quote["id"]) for quote in quotes]
        self.assertEqual(sum(task.exception() is None for task in tasks), 1)
        self.assertEqual([str(task.exception()) for task in tasks if task.exception()], ["Insufficient credits"])
        self.assertEqual(self.exchange.wallet("buyer")["balance"], 0)
        self.assertTrue(self.exchange.audit()["balanced"])

    def test_malformed_result_and_assignment_ownership(self):
        self.submit(b"group,value\na,5\n")
        lease = self.claim("worker1")
        with self.assertRaisesRegex(ExchangeError, "does not exist"):
            self.exchange.input("worker2", lease["id"])
        for invalid in (b"[]", b'[["a",true,5]]', b'[["a", 1, 5]]'):
            with self.assertRaisesRegex(ExchangeError, "not a canonical aggregate"):
                self.finish("worker1", lease, invalid)
        self.finish("worker1", lease)
        self.assertEqual(self.exchange.wallet("worker1")["balance"], 0)

    def test_failure_after_payout_rolls_back_and_retry_settles_once(self):
        job = self.submit(b"group,value\na,5\n")
        self.finish("worker1", self.claim("worker1"))
        lease = self.claim("verifier")
        output = compute(self.exchange.input("verifier", lease["id"]))
        with patch.object(self.exchange, "close", side_effect=OSError("simulated storage failure")):
            with self.assertRaises(OSError):
                self.finish("verifier", lease, output)
        self.assertEqual(self.exchange.wallet("worker1")["balance"], 0)
        self.assertEqual(self.exchange.wallet(job)["balance"], 10)
        self.assertEqual(self.exchange.receipts("buyer"), [])
        self.finish("verifier", lease, output)
        self.finish("verifier", lease, output)
        self.assertEqual(self.exchange.wallet("worker1")["balance"], 8)
        self.assertEqual(self.exchange.wallet("verifier")["balance"], 2)
        self.assertEqual(self.exchange.wallet(job)["balance"], 0)
        self.assertTrue(self.exchange.audit()["balanced"])


if __name__ == "__main__":
    unittest.main()
