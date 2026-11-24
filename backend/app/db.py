"""PostgreSQL connections. Schema changes run only through migrate.py."""

import os
import re
import psycopg
from psycopg.rows import dict_row
from app import config  # load root .env


def connect():
    schema = os.getenv("LOOPP_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", schema):
        raise ValueError("Invalid database schema")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL in .env")
    return psycopg.connect(
        database_url,
        row_factory=dict_row,
        connect_timeout=5,
        options=f"-c statement_timeout=10000 -c search_path={schema},public",
    )
