"""Open the local DuckDB warehouse (data/warehouse/fsa.duckdb, git-ignored)."""
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[3]
DB_PATH = ROOT / "data" / "warehouse" / "fsa.duckdb"
SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Path = DB_PATH) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA.read_text())
    return con
