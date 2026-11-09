"""
setup_db.py
-----------
Creates and populates portfolio.db from the bundled schema and CSV data.
Runs automatically if portfolio.db is missing (imported by agent modules).

Usage:
    python setup_db.py          # explicit run / force rebuild
    python setup_db.py --force  # delete existing DB and rebuild
"""

import csv
import logging
import sqlite3
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HERE      = Path(__file__).parent.resolve()
SCHEMA    = HERE / "database_schema.sql"
DATA_DIR  = HERE / "data"
DB_PATH   = HERE / "portfolio.db"

# FK-safe insertion order
CSV_ORDER = [
    ("sectors",              "sectors.csv"),
    ("securities",           "securities.csv"),
    ("benchmarks",           "benchmarks.csv"),
    ("portfolios",           "portfolios.csv"),
    ("holdings",             "holdings.csv"),
    ("transactions",         "transactions.csv"),
    ("historical_prices",    "historical_prices.csv"),
    ("portfolio_performance","portfolio_performance.csv"),
    ("risk_metrics",         "risk_metrics.csv"),
]


def _coerce(val: str):
    """Empty string → None; try numeric coercion."""
    if val == "" or val is None:
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _load_csv(conn: sqlite3.Connection, table: str, csv_file: str) -> tuple[int, int, int]:
    """
    Load one CSV into *table*.  Returns (csv_count, inserted, rejected).
    Verifies that total rows in table matches the count in the CSV file.
    """
    path = DATA_DIR / csv_file
    if not path.exists():
        logger.warning("CSV not found — skipped: %s", path)
        return 0, 0, 0

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        rows = [tuple(_coerce(row[c]) for c in columns) for row in reader]

    csv_count = len(rows)
    if not rows:
        return 0, 0, 0

    ph  = ", ".join("?" * len(columns))
    col = ", ".join(columns)
    inserted = rejected = 0
    for row in rows:
        try:
            conn.execute(f"INSERT OR IGNORE INTO {table} ({col}) VALUES ({ph})", row)
            inserted += 1
        except sqlite3.Error as e:
            logger.warning("Rejected row in %s: %s — %s", table, row, e)
            rejected += 1
    conn.commit()

    # Integrity check: verify actual table count matches CSV count
    actual_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if actual_count != csv_count:
        logger.error("Row mismatch in %s: CSV had %d rows, but table has %d!",
                     table, csv_count, actual_count)
    return csv_count, inserted, rejected


def setup_database(db_path: Path = DB_PATH, force: bool = False) -> sqlite3.Connection:
    """
    Build portfolio.db.  If *force* is True, the existing file is deleted first.
    Returns an open read-write connection (caller must close it).
    """
    if force and db_path.exists():
        db_path.unlink()
        logger.info("Removed existing database.")

    conn = _connect(db_path)

    schema_sql = SCHEMA.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    logger.info("Schema applied.")

    total_csv = total_inserted = total_rejected = 0
    for table, csv_file in CSV_ORDER:
        csv_n, ins, rej = _load_csv(conn, table, csv_file)
        total_csv += csv_n
        total_inserted += ins
        total_rejected += rej
        status = f"{ins:>4} inserted (CSV: {csv_n:>4})"
        if rej:
            status += f", {rej} REJECTED"
        logger.info("  %-26s %s", table, status)

    logger.info("Database ready: %d/%d CSV rows loaded, %d rejected → %s",
                total_inserted, total_csv, total_rejected, db_path)
    return conn


def ensure_db() -> Path:
    """
    Called by agent modules: auto-builds portfolio.db if it is missing.
    Returns the path to the database.
    """
    if not DB_PATH.exists():
        logger.info("portfolio.db not found — running setup_database()...")
        conn = setup_database()
        conn.close()
    return DB_PATH


if __name__ == "__main__":
    force = "--force" in sys.argv
    conn  = setup_database(force=force)

    print("\n=== Table row counts ===")
    for (tbl,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ):
        n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"  {tbl:<30s} {n:>6}")

    conn.close()
