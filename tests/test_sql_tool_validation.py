import pytest
from agent.tools.sql_tool import _validate_sql

def test_valid_select():
    _validate_sql("SELECT COUNT(*) FROM portfolios")
    _validate_sql("SELECT s.symbol, sec.sector_name FROM securities s JOIN sectors sec ON s.sector_id = sec.sector_id")

def test_valid_with():
    _validate_sql("WITH tech AS (SELECT * FROM securities WHERE sector_id = 1) SELECT COUNT(*) FROM tech")

def test_blocked_drop():
    with pytest.raises(ValueError, match="forbidden keyword"):
        _validate_sql("DROP TABLE portfolios")

def test_blocked_delete():
    with pytest.raises(ValueError, match="forbidden keyword"):
        _validate_sql("DELETE FROM holdings WHERE portfolio_id = 1")

def test_blocked_insert():
    with pytest.raises(ValueError, match="forbidden keyword"):
        _validate_sql("INSERT INTO sectors (sector_name) VALUES ('Crypto')")

def test_blocked_update():
    with pytest.raises(ValueError, match="forbidden keyword"):
        _validate_sql("UPDATE portfolios SET total_aum = 0")

def test_blocked_pragma():
    with pytest.raises(ValueError, match="forbidden keyword"):
        _validate_sql("PRAGMA database_list")

def test_blocked_multi_statement():
    with pytest.raises(ValueError, match="single SQL statement"):
        _validate_sql("SELECT 1; SELECT 2")

def test_all_ground_truth_sqls_pass_validation():
    import json
    from pathlib import Path
    from agent.tools.sql_tool import _strip_fences

    gt_path = Path(__file__).parent.parent / "ground_truth_dataset.json"
    data = json.loads(gt_path.read_text(encoding="utf-8"))

    for q in data["questions"]:
        if q["type"] == "text2sql":
            raw_sql = q["ground_truth"]["sql_query"]
            clean_sql = _strip_fences(raw_sql)
            # Must not raise
            _validate_sql(clean_sql)

def test_column_names_containing_blocked_words_are_allowed():
    # Words like updated_at, created_date, is_dropped should NOT be blocked
    _validate_sql("SELECT updated_at, is_dropped, user_created FROM portfolios")
    _validate_sql("SELECT price_updated FROM securities WHERE is_deleted = 0")

