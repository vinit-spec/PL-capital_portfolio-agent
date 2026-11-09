from pathlib import Path
import pytest
from agent.tools.exposure_tool import ExposureCalculatorTool

DB_PATH = Path(__file__).parent.parent / "portfolio.db"

@pytest.fixture
def exposure_tool():
    return ExposureCalculatorTool(DB_PATH)

def test_exact_portfolio_name(exposure_tool):
    res = exposure_tool.run("Tech Innovation Fund")
    assert res["error"] is None
    assert res["portfolio_name"] == "Tech Innovation Fund"
    assert "Technology" in res["equity_exposures"]
    assert res["equity_exposures"]["Technology"] == 100.0
    assert abs(res["equity_sum"] - 100.0) < 0.01

def test_fuzzy_portfolio_name_lowercase(exposure_tool):
    res = exposure_tool.run("international equity")
    assert res["error"] is None
    assert res["portfolio_name"] == "International Equity Fund"
    assert abs(res["equity_sum"] - 100.0) < 0.01
    assert "Technology" in res["equity_exposures"]
    assert "Energy" in res["equity_exposures"]

def test_bonds_excluded_from_equity(exposure_tool):
    # Conservative Income Fund has bonds
    res = exposure_tool.run("Conservative Income Fund")
    assert res["error"] is None
    assert abs(res["equity_sum"] - 100.0) < 0.01
    # Check that bonds are not in equity_exposures
    assert "Bond" not in res["equity_exposures"]
    assert "Unclassified" not in res["equity_exposures"]
    # But raw exposures should sum to less or include all assets
    assert res["raw_exposures"]

def test_empty_portfolio_name(exposure_tool):
    res = exposure_tool.run("")
    assert res["error"] is not None

def test_nonexistent_portfolio(exposure_tool):
    res = exposure_tool.run("NonExistentFund12345")
    assert res["error"] is not None
    assert "not found" in res["error"].lower()

