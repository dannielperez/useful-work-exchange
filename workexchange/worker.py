import logging
import subprocess
import time
from uuid import uuid4

from .exchange import ExchangeError
from .workload import MAX_OUTPUT, VERSION, compute, digest

LOGGER = logging.getLogger(__name__)


def container_compute(image, data):
    name = "useful-work-" + uuid4().hex
    command = ["docker", "run", "--rm", "--name", name, "--pull=never", "--network=none", "--read-only",
               "--cpus=1", "--memory=128m", "--memory-swap=128m", "--shm-size=1m", "--pids-limit=32", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--log-driver=none", "--user=65534:65534", "-i", image]
    try:
        result = subprocess.run(command, input=data, capture_output=True, timeout=60, check=True)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            LOGGER.exception("Could not remove workload container %s", name)
        raise
    if len(result.stdout) > MAX_OUTPUT:
        raise ValueError("Worker output exceeds size limit")
    return result.stdout


def work(client, image, local, once=False):
    while True:
        try:
            lease = client.json("POST", "/v1/leases")
            if lease:
                if lease["workload"] != VERSION or lease["image"] != image:
                    raise ValueError("Assignment does not match the configured workload and image")
                path = "/v1/leases/" + lease["id"]
                data = client.request("GET", path + "/input")
                if digest(data) != lease["input_hash"]:
                    raise ValueError("Assignment input hash did not match")
                started = time.monotonic()
                result = compute(data) if local else container_compute(image, data)
                elapsed = time.monotonic() - started
                client.request("POST", path + "/result", result)
                LOGGER.info("Submitted %s block %s after %.3fs of execution", lease["job"], lease["number"], elapsed)
                if not once:
                    continue
        except ExchangeError as error:
            if once or error.status in (401, 403):
                raise
            LOGGER.warning("Worker request failed (%s): %s", error.status, error)
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            if once:
                raise
            LOGGER.warning("Worker could not complete assignment: %s", error)
        if once:
            return
        time.sleep(2)
