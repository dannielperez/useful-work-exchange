import csv
import hashlib
import io
import json
import re
import sys
from collections import defaultdict
from itertools import batched

VERSION = "csv-group-sum/v1"
MAX_INPUT = 25 * 1024 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
BLOCK_ROWS = 1000
MAX_GROUP_BYTES = 256
INTEGER = re.compile(r"[+-]?[0-9]{1,19}")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def parse_csv(data):
    if len(data) > MAX_INPUT:
        raise ValueError("CSV exceeds 25 MiB")
    rows = csv.reader(io.StringIO(data.decode("utf-8"), newline=""), strict=True)
    if next(rows, None) != ["group", "value"]:
        raise ValueError("Expected CSV header group,value")
    blocks = []
    for batch in batched(rows, BLOCK_ROWS):
        parsed = []
        for row in batch:
            if len(row) != 2 or not INTEGER.fullmatch(row[1]):
                raise ValueError("Each row requires a group and a signed 64-bit integer")
            group, raw = row
            if not group or len(group.encode()) > MAX_GROUP_BYTES or "\x00" in group:
                raise ValueError("Group must contain 1 to 256 UTF-8 bytes and no NUL")
            value = int(raw)
            if not -(2**63) <= value < 2**63:
                raise ValueError("Integer outside signed 64-bit range")
            parsed.append([group, value])
        blocks.append((len(parsed), canonical(parsed)))
    if not blocks:
        raise ValueError("CSV must contain at least one data row")
    return blocks


def compute(data):
    totals = defaultdict(lambda: [0, 0])
    for group, value in json.loads(data):
        totals[group][0] += 1
        totals[group][1] += value
    return canonical([[group, *totals[group]] for group in sorted(totals)])


def validate_output(data, row_count):
    if len(data) > MAX_OUTPUT:
        raise ValueError("Result exceeds size limit")
    try:
        rows = json.loads(data)
        if type(rows) is not list or len(rows) > row_count:
            raise ValueError
        previous = None
        count_sum = 0
        for row in rows:
            if type(row) is not list or len(row) != 3:
                raise ValueError
            group, count, total = row
            if type(group) is not str or not group or len(group.encode()) > MAX_GROUP_BYTES or "\x00" in group:
                raise ValueError
            if previous is not None and group <= previous:
                raise ValueError
            if type(count) is not int or type(total) is not int or not 1 <= count <= row_count:
                raise ValueError
            if not count * -(2**63) <= total <= count * (2**63 - 1):
                raise ValueError
            previous = group
            count_sum += count
        if count_sum != row_count or canonical(rows) != data:
            raise ValueError
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError("Result is not a canonical aggregate for this row count") from error


def merge(outputs):
    totals = defaultdict(lambda: [0, 0])
    for output in outputs:
        for group, count, total in json.loads(output):
            totals[group][0] += count
            totals[group][1] += total
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\r\n")
    writer.writerow(["group", "count", "sum"])
    writer.writerows((group, *totals[group]) for group in sorted(totals))
    return target.getvalue().encode()


if __name__ == "__main__":
    sys.stdout.buffer.write(compute(sys.stdin.buffer.read(MAX_OUTPUT + 1)))
