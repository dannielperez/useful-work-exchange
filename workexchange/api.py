import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from urllib.parse import urlsplit

from .exchange import ExchangeError
from .workload import MAX_INPUT, MAX_OUTPUT, canonical

LOGGER = logging.getLogger(__name__)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def body(self, maximum):
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as error:
            raise ExchangeError(411, "Content-Length required") from error
        if not 0 <= length <= maximum:
            raise ExchangeError(413, "Request exceeds size limit")
        data = self.rfile.read(length)
        if len(data) != length:
            raise ExchangeError(400, "Incomplete request")
        return data

    def route(self):
        exchange = self.server.exchange
        path = urlsplit(self.path).path.strip("/").split("/")
        if self.command == "GET" and path == ["v1", "key"]:
            return {"ed25519": exchange.signer.public_key().public_bytes_raw().hex()}
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ExchangeError(401, "Participant token required")
        account = exchange.authenticate(auth.removeprefix("Bearer "))
        owner = account["id"]
        if self.command == "POST":
            match path:
                case ["v1", "quotes"]:
                    return exchange.quote(owner, self.body(MAX_INPUT))
                case ["v1", "jobs", quote]:
                    return exchange.submit(owner, quote)
                case ["v1", "leases"]:
                    return exchange.claim(account)
                case ["v1", "leases", lease, "result"]:
                    return exchange.result(owner, lease, self.body(MAX_OUTPUT))
        if self.command == "GET":
            match path:
                case ["v1", "jobs", job]:
                    return exchange.job(owner, job)
                case ["v1", "jobs", job, "result"]:
                    return exchange.job(owner, job, download=True), "text/csv; charset=utf-8"
                case ["v1", "leases", lease, "input"]:
                    return exchange.input(owner, lease), "application/octet-stream"
                case ["v1", "wallet"]:
                    return exchange.wallet(owner)
                case ["v1", "receipts"]:
                    return exchange.receipts(owner)
        raise ExchangeError(404, "Unknown endpoint")

    def handle_request(self):
        status = 200
        content_type = "application/json"
        try:
            result = self.route()
            if isinstance(result, tuple):
                content, content_type = result
            else:
                content = canonical(result)
        except ExchangeError as error:
            status = error.status
            content = canonical({"error": str(error)})
        except TimeoutError:
            status = 408
            content = canonical({"error": "Request timed out"})
        except Exception:
            LOGGER.exception("Request failed")
            status = 500
            content = canonical({"error": "Coordinator could not complete the request"})
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            pass

    do_GET = handle_request
    do_POST = handle_request


def serve(exchange, host, port):
    stop = Event()

    def expire_work():
        while not stop.wait(2):
            try:
                exchange.tick()
            except Exception:
                LOGGER.exception("Deadline sweep failed")

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.exchange = exchange
        janitor = Thread(target=expire_work, daemon=True)
        janitor.start()
        try:
            server.serve_forever()
        finally:
            stop.set()
            janitor.join()
