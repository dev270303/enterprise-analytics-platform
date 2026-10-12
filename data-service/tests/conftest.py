"""Test database: a fresh Postgres database built from the real Flyway migrations
(backend/src/main/resources/db/migration), so Python tests run against exactly the
schema production uses.  Needs a server with the pgvector extension available
(CI uses the pgvector/pgvector:pg16 image)."""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "backend" / "src" / "main" / "resources" / "db" / "migration"
ADMIN_URL = os.getenv("TEST_ADMIN_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
TEST_DB = os.getenv("TEST_DATABASE_NAME", "eap_pytest")


def _version(path: Path) -> int:
    return int(re.match(r"V(\d+)__", path.name).group(1))


@pytest.fixture(scope="session", autouse=True)
def database():
    with psycopg.connect(ADMIN_URL, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{TEST_DB}"')
    test_url = ADMIN_URL.rsplit("/", 1)[0] + "/" + TEST_DB
    with psycopg.connect(test_url, autocommit=True) as conn:
        for path in sorted(MIGRATIONS.glob("V*__*.sql"), key=_version):
            conn.execute(path.read_text(encoding="utf-8"))
    os.environ["DATABASE_URL"] = test_url
    os.environ.pop("LLM_API_KEY", None)
    os.environ["INTERNAL_API_TOKEN"] = "test-token"
    yield test_url
