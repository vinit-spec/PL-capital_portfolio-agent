"""
agent/tools/sql_tool.py
-----------------------
Text-to-SQL tool.

Pipeline:  question → Gemini (SQL generation) → validate → execute
           └── on SQL error: one self-repair retry with the error message fed back
"""

import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai

logger = logging.getLogger(__name__)

ROW_CAP = 200          # max rows returned from any query
MAX_RETRIES = 1        # one self-repair attempt on SQL error

# ---------------------------------------------------------------------------
# Safety guard: only allow SELECT / WITH statements
# ---------------------------------------------------------------------------
_UNSAFE = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|CREATE|ALTER|TRUNCATE|REPLACE|PRAGMA|ATTACH|DETACH)\b",
    re.IGNORECASE,
)
_MULTI_STMT = re.compile(r";\s*\S")          # second statement after a semicolon
_ALLOWED_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE)


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:sql)?\s*", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return text.strip().rstrip(";")


def _validate_sql(sql: str) -> None:
    if _UNSAFE.search(sql):
        raise ValueError(f"SQL contains forbidden keyword: blocked.")
    if _MULTI_STMT.search(sql):
        raise ValueError("Only a single SQL statement is allowed.")
    if not _ALLOWED_START.match(sql):
        raise ValueError("Only SELECT / WITH queries are permitted.")


# ---------------------------------------------------------------------------
# Prompt template — schema + categorical values baked in
# ---------------------------------------------------------------------------
_SCHEMA_AND_METADATA = """\
=== DATABASE SCHEMA (SQLite) ===

Tables:
  sectors(sector_id PK, sector_name, sector_description, industry_group)
  securities(security_id PK, symbol, company_name,
             asset_type IN('Bond','Stock'),
             sector_id FK→sectors,         -- NULL for bonds
             market_cap, current_price, currency, exchange,
             country, listing_date, maturity_date, coupon_rate)
  benchmarks(benchmark_id PK, benchmark_name, benchmark_symbol,
             benchmark_type, description, inception_date)
  portfolios(portfolio_id PK, portfolio_name, creation_date,
             target_risk_level, total_aum, strategy_type,
             benchmark_index, status)
  holdings(holding_id PK, portfolio_id FK, security_id FK,
           quantity, purchase_price, purchase_date,
           current_weight REAL,   -- fraction 0–1, sums to 1.0 per portfolio
           cost_basis,
           UNIQUE(portfolio_id, security_id))
  transactions(transaction_id PK, portfolio_id FK, security_id FK,
               transaction_type IN('BUY','SELL'),
               quantity, price, transaction_date, fees,
               settlement_date, notes)
  historical_prices(price_id PK, security_id FK, price_date,
                    open_price, high_price, low_price, close_price,
                    volume, adjusted_close,
                    UNIQUE(security_id, price_date))
  portfolio_performance(performance_id PK, portfolio_id FK,
                        performance_date, nav, total_return_1m,
                        total_return_3m, total_return_6m, total_return_1y,
                        volatility, sharpe_ratio, max_drawdown,
                        UNIQUE(portfolio_id, performance_date))
  risk_metrics(risk_id PK, portfolio_id FK, calculation_date,
               var_95, var_99, cvar_95, beta, correlation_sp500,
               tracking_error, information_ratio, sortino_ratio,
               UNIQUE(portfolio_id, calculation_date))

=== KNOWN CATEGORICAL VALUES ===

portfolios.status            : 'Active', 'Passive'
portfolios.target_risk_level : 'High', 'Low', 'Medium'
portfolios.strategy_type     : 'Balanced', 'Dividend', 'ESG', 'Growth',
                               'Income', 'Index', 'International', 'Sector', 'Value'
securities.asset_type        : 'Bond', 'Stock'
sectors.sector_name          : 'Automotive', 'Consumer Discretionary',
                               'Consumer Staples', 'Energy', 'Financials',
                               'Healthcare', 'Industrials', 'Real Estate',
                               'Technology', 'Utilities'
portfolios.portfolio_name    : 'Balanced Portfolio',
                               'Conservative Income Fund',
                               'Dividend Aristocrats Fund',
                               'ESG Sustainable Fund',
                               'Emerging Markets Fund',
                               'Fixed Income Plus',
                               'Growth Equity Fund',
                               'International Equity Fund',
                               'Small Cap Value Fund',
                               'Tech Innovation Fund',
                               'Total Bond Market Index Fund',
                               'Total International Index Fund',
                               'Total Stock Market Index Fund'

=== IMPORTANT NOTES ===
- Only 7 of the 10 sectors have securities assigned:
  Technology, Consumer Discretionary, Automotive, Financials,
  Healthcare, Consumer Staples, Energy.
  Industrials / Real Estate / Utilities have no securities in this dataset.
- Bonds (asset_type='Bond') have NULL sector_id.
- holdings.current_weight is on the 0–1 scale (e.g. 0.15 = 15%).
- Q8-style queries: only 'Total Stock Market Index Fund' has >5 equity sectors.
- Use CTEs (WITH ...) for multi-step logic.
- For percentages: ROUND(value * 100, 2).
- Do NOT use PRAGMA, DDL, or DML.
"""

_GENERATION_PROMPT = """\
You are an expert SQLite analyst for a portfolio management system.
Given the schema below and a natural language question, generate ONE
valid SQLite SELECT query that answers it exactly.

{schema}

Rules:
1. Return ONLY the raw SQL — no markdown, no backticks, no explanation.
2. Single statement only. End without a semicolon.
3. Use only SELECT or WITH … SELECT.
4. Use LIMIT {row_cap} to cap results unless the question requests a specific count.
5. Match string literals exactly to the KNOWN CATEGORICAL VALUES above
   (case-sensitive). Do not guess spellings.

Question: {question}
SQL:"""

_REPAIR_PROMPT = """\
The SQL query below produced an error when run against SQLite.
Fix the query so it works. Return ONLY the corrected SQL — no explanation.

Original SQL:
{sql}

Error:
{error}

Corrected SQL:"""


class SQLQueryTool:
    """
    Converts a natural-language question to SQL via Gemini,
    validates and executes it, and returns structured results.

    The agent passes the *original user question* to `run()`.
    SQL generation and retry logic are fully internal.
    """

    def __init__(self, db_path: Path, model: genai.GenerativeModel):
        self.db_path = db_path
        self._model  = model

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, question: str) -> dict[str, Any]:
        """
        Returns:
        {
            question    : str,
            sql         : str | None,
            result      : scalar | list | list[dict],
            result_text : str,
            error       : str | None,
        }
        """
        out: dict[str, Any] = {
            "question":    question,
            "sql":         None,
            "columns":     [],
            "rows":        [],
            "result":      None,
            "result_text": "",
            "error":       None,
        }

        if not question or not question.strip():
            out["error"]       = "Empty question."
            out["result_text"] = "Error: empty question."
            return out

        if len(question) > 2000:
            out["error"]       = "Question too long (>2000 chars)."
            out["result_text"] = "Error: question too long."
            return out

        try:
            sql = self._generate(question)
            out["sql"] = sql
            logger.info("[sql_tool] Generated SQL: %s", sql[:200])

            rows, cols = self._execute_with_retry(sql, question, out)
            out["columns"]     = cols
            out["rows"]        = [tuple(r) for r in rows]
            out["result"]      = self._structure(rows, cols)
            out["result_text"] = self._tabulate(rows, cols)

        except Exception as exc:
            logger.error("[sql_tool] %s", exc)
            out["error"]       = str(exc)
            out["result_text"] = f"Error: {exc}"

        return out

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _generate(self, question: str) -> str:
        prompt = _GENERATION_PROMPT.format(
            schema=_SCHEMA_AND_METADATA,
            row_cap=ROW_CAP,
            question=question,
        )
        resp = self._model.generate_content(prompt)
        return _strip_fences(resp.text)

    def _repair(self, bad_sql: str, error: str) -> str:
        prompt = _REPAIR_PROMPT.format(sql=bad_sql, error=error)
        resp = self._model.generate_content(prompt)
        return _strip_fences(resp.text)

    def _execute_with_retry(
        self, sql: str, question: str, out: dict
    ) -> tuple[list, list[str]]:
        for attempt in range(MAX_RETRIES + 1):
            try:
                _validate_sql(sql)
                rows, cols = self._execute(sql)
                if attempt > 0:
                    out["sql"] = sql        # update with repaired version
                return rows, cols
            except (sqlite3.Error, ValueError) as exc:
                err_msg = str(exc)
                if attempt < MAX_RETRIES:
                    logger.warning("[sql_tool] Attempt %d failed (%s). Retrying with repair...",
                                   attempt + 1, err_msg)
                    sql = self._repair(sql, err_msg)
                    out["sql"] = sql
                else:
                    raise

    def _execute(self, sql: str) -> tuple[list, list[str]]:
        # query_only pragma prevents any writes even if validation misses something
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only = ON")
            cur  = conn.execute(sql)
            rows = cur.fetchmany(ROW_CAP)
            cols = [d[0] for d in cur.description] if cur.description else []
            return rows, cols
        finally:
            conn.close()

    @staticmethod
    def _structure(rows, cols: list[str]):
        if not rows:
            return []
        if len(cols) == 1 and len(rows) == 1:
            return rows[0][0]
        if len(cols) == 1:
            return [r[0] for r in rows]
        return [dict(r) for r in rows]

    @staticmethod
    def _tabulate(rows, cols: list[str]) -> str:
        if not rows:
            return "No results found."
        if len(cols) == 1 and len(rows) == 1:
            return str(rows[0][0])
        if len(cols) == 1:
            return "\n".join(f"  • {r[0]}" for r in rows)

        widths = [
            max(len(str(c)), *(len(str(r[i])) for r in rows))
            for i, c in enumerate(cols)
        ]
        hdr = " | ".join(str(c).ljust(w) for c, w in zip(cols, widths))
        sep = "-+-".join("-" * w for w in widths)
        body = [
            " | ".join(str(r[i]).ljust(w) for i, w in enumerate(widths))
            for r in rows
        ]
        return "\n".join([hdr, sep] + body)
