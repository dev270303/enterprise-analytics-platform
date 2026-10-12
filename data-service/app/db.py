"""Small psycopg 3 helpers. Each call opens a short-lived connection: the database is
serverless (scales to zero), traffic is low, and short connections let it sleep."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


@contextmanager
def connect(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(get_settings().database_url, autocommit=autocommit, row_factory=dict_row,
                           connect_timeout=30, application_name="eap-data-service")
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params: Any = None) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: Any = None) -> dict | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None
