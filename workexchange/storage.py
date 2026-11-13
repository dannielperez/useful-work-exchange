import os
import re
import tempfile
from pathlib import Path

from .workload import digest


class ObjectStore:
    """Private, immutable objects on the coordinator's storage volume."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, data):
        key = digest(data)
        target = self.root / key
        if not target.exists():
            with tempfile.NamedTemporaryFile(dir=self.root, delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                try:
                    os.replace(temporary, target)
                except PermissionError:
                    if not target.exists():
                        raise
                if os.name == "posix":
                    fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
            finally:
                temporary.unlink(missing_ok=True)
        return key

    def get(self, key):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("Invalid object digest")
        data = (self.root / key).read_bytes()
        if digest(data) != key:
            raise OSError(f"Object integrity check failed for {key}")
        return data
