import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, url):
        self.url = url
        self.local = url.startswith("sqlite:///")

    @contextmanager
    def connect(self):
        if self.local:
            path = Path(self.url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
        else:
            import psycopg
            from psycopg.rows import dict_row

            connection = psycopg.connect(self.url, row_factory=dict_row)
        try:
            with connection:
                yield connection
        finally:
            if self.local:
                connection.close()

    def initialize(self):
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)

    @contextmanager
    def transaction(self):
        with self.connect() as connection:
            # One lock orders scheduler and balance changes for the small pilot.
            if self.local:
                connection.execute("BEGIN IMMEDIATE")
            else:
                connection.execute("SELECT id FROM exchange_lock WHERE id = 1 FOR UPDATE")

            def query(sql, *values):
                return connection.execute(sql if self.local else sql.replace("?", "%s"), values)

            yield query
