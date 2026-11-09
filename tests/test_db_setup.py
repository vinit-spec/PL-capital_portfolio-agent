import sqlite3
from pathlib import Path
import pytest
from setup_db import DB_PATH, ensure_db

def test_database_tables():
    path = ensure_db()
    assert path.exists()

    conn = sqlite3.connect(str(path))
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    expected = [
        "benchmarks",
        "historical_prices",
        "holdings",
        "portfolio_performance",
        "portfolios",
        "risk_metrics",
        "sectors",
        "securities",
        "transactions"
    ]
    for exp in expected:
        assert exp in tables

    # Verify counts
    assert conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0] == 13
    assert conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 30
    assert conn.execute("SELECT COUNT(*) FROM sectors").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM holdings").fetchone()[0] == 95

    conn.close()

