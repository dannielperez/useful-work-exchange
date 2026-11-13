import argparse
import base64
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .api import serve
from .database import Database
from .exchange import Exchange, ExchangeError
from .storage import ObjectStore
from .worker import work
from .workload import MAX_INPUT, canonical

STATE = Path(os.getenv("WORK_STATE", ".state"))
DATABASE_URL = os.getenv("WORK_DATABASE_URL", f"sqlite:///{STATE / 'exchange.db'}")
IMAGE = os.getenv("WORK_IMAGE", "local")
IMAGE_DIGEST = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Client:
    def __init__(self, url, token):
        parsed = urlsplit(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ValueError("Use HTTPS for a remote coordinator")
        self.url = url.rstrip("/")
        self.token = token
        self.opener = build_opener(NoRedirect())

    def request(self, method, path, data=None):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        request = Request(self.url + path, data, headers, method=method)
        try:
            with self.opener.open(request, timeout=30) as response:
                return response.read()
        except HTTPError as error:
            with error:
                try:
                    message = json.loads(error.read(8192))["error"]
                except (ValueError, KeyError, TypeError):
                    message = str(error)
            raise ExchangeError(error.code, message) from error

    def json(self, method, path, data=None):
        return json.loads(self.request(method, path, data))


def signer(initialize):
    key_file = STATE / "receipt.key"
    if initialize and not key_file.exists():
        STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(dir=STATE, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                handle.write(Ed25519PrivateKey.generate().private_bytes_raw())
                handle.flush()
                os.fsync(handle.fileno())
            except BaseException:
                handle.close()
                temporary.unlink(missing_ok=True)
                raise
        try:
            os.link(temporary, key_file)
        except FileExistsError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return Ed25519PrivateKey.from_private_bytes(key_file.read_bytes())


def parser():
    root = argparse.ArgumentParser(prog="python -m workexchange")
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("init", "audit", "metrics", "wallet", "receipts", "key"):
        commands.add_parser(command)
    server = commands.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8080)
    enroll = commands.add_parser("enroll")
    enroll.add_argument("name")
    enroll.add_argument("operator")
    enroll.add_argument("role", choices=("member", "worker", "verifier"))
    grant = commands.add_parser("grant")
    grant.add_argument("name")
    grant.add_argument("amount", type=int)
    commands.add_parser("quote").add_argument("csv", type=Path)
    commands.add_parser("submit").add_argument("quote")
    commands.add_parser("status").add_argument("job")
    download = commands.add_parser("download")
    download.add_argument("job")
    download.add_argument("output", type=Path)
    verify = commands.add_parser("verify-receipts")
    verify.add_argument("public_key")
    verify.add_argument("file", type=Path)
    worker = commands.add_parser("work")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--local", action="store_true", help="Run the built-in transform directly for local development")
    return root


def run(args):
    command = args.command
    if command in ("init", "serve", "enroll", "grant", "audit", "metrics"):
        database = Database(DATABASE_URL)
        if command == "serve" and not database.local and not IMAGE_DIGEST.fullmatch(IMAGE):
            raise ValueError("Set WORK_IMAGE to a published image digest for a PostgreSQL pilot")
        if command == "serve" and database.local and args.host != "127.0.0.1":
            raise ValueError("Local mode requires the default loopback bind address")
        exchange = Exchange(database, ObjectStore(STATE / "objects"), signer(command == "init"), IMAGE)
        match command:
            case "init":
                exchange.initialize()
                return {"public_key": exchange.signer.public_key().public_bytes_raw().hex()}
            case "serve":
                serve(exchange, args.host, args.port)
            case "enroll":
                return {"account": args.name, "token": exchange.enroll(args.name, args.operator, args.role)}
            case "grant":
                return exchange.grant(args.name, args.amount)
            case "audit":
                result = exchange.audit()
                if not result["balanced"]:
                    raise ValueError(f"Ledger reconciliation failed: {result}")
                return result
            case "metrics":
                return exchange.metrics()
        return None
    if command == "verify-receipts":
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(args.public_key))
        receipts = json.loads(args.file.read_bytes())
        for item in receipts:
            key.verify(base64.b64decode(item["signature"], validate=True), canonical(item["document"]))
        return {"verified": len(receipts)}
    client = Client(os.getenv("WORK_URL", "http://127.0.0.1:8080"), os.getenv("WORK_TOKEN", ""))
    match command:
        case "quote":
            with args.csv.open("rb") as handle:
                return client.json("POST", "/v1/quotes", handle.read(MAX_INPUT + 1))
        case "submit":
            return client.json("POST", f"/v1/jobs/{args.quote}")
        case "status":
            return client.json("GET", f"/v1/jobs/{args.job}")
        case "download":
            manifest = client.json("GET", f"/v1/jobs/{args.job}")
            if manifest["status"] == "running":
                raise ExchangeError(409, "Job is still running")
            args.output.write_bytes(client.request("GET", f"/v1/jobs/{args.job}/result"))
            args.output.with_suffix(args.output.suffix + ".json").write_bytes(canonical(manifest) + b"\n")
            return manifest
        case "wallet" | "receipts" | "key":
            return client.json("GET", f"/v1/{command}")
        case "work":
            if args.local and urlsplit(client.url).hostname not in ("127.0.0.1", "localhost", "::1"):
                raise ValueError("Local execution requires a loopback coordinator")
            if not args.local and not IMAGE_DIGEST.fullmatch(IMAGE):
                raise ValueError("Set WORK_IMAGE to the approved image digest")
            work(client, IMAGE, args.local, args.once)
    return None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = run(parser().parse_args())
        if result is not None:
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, ExchangeError, InvalidSignature) as error:
        raise SystemExit(str(error) or "Receipt signature is invalid") from error
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
