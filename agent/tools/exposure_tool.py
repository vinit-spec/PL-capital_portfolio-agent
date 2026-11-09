"""
agent/tools/exposure_tool.py
-----------------------------
Sector Exposure Calculator.

Given a portfolio name (resolved fuzzily), calculates:
  - raw_pct    : sector weight as % of the WHOLE portfolio (equity + bond)
  - equity_pct : sector weight renormalized across equity-only holdings

Bonds (asset_type='Bond' or NULL sector_id) are excluded from equity_pct.
Holdings with NULL sector_id but asset_type='Stock' land in 'Unclassified'.
"""

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
_ALL_PORTFOLIOS_SQL = "SELECT portfolio_id, portfolio_name FROM portfolios ORDER BY portfolio_name"

_HOLDINGS_SQL = """
SELECT
    COALESCE(sec.sector_name, 'Unclassified')  AS sector_name,
    s.asset_type,
    SUM(h.current_weight)                       AS weight_sum
FROM holdings h
JOIN portfolios p  ON h.portfolio_id  = p.portfolio_id
JOIN securities s  ON h.security_id   = s.security_id
LEFT JOIN sectors sec ON s.sector_id  = sec.sector_id
WHERE p.portfolio_id = ?
GROUP BY sector_name, s.asset_type
ORDER BY weight_sum DESC
"""

# Detect weight scale: if max weight > 1 we're on 0-100 scale
_SCALE_SQL = "SELECT MAX(current_weight) FROM holdings WHERE portfolio_id = ?"


class ExposureCalculatorTool:
    """
    Sector exposure calculator for a named portfolio.

    Returns both:
      raw_pct    — % of the whole portfolio (equity + bond)
      equity_pct — % renormalized over equity positions only

    Portfolio name is resolved fuzzily (case-insensitive substring match).
    """

    def __init__(self, db_path: Path):
        self.db_path = db_path

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def run(self, portfolio_name: str) -> dict[str, Any]:
        """
        Returns:
        {
            portfolio_name   : str (canonical),
            raw_exposures    : {sector: pct_of_whole_portfolio},
            equity_exposures : {sector: pct_of_equity_only},
            equity_sum       : float  (~100.0),
            result_text      : str,
            error            : str | None,
        }
        """
        out: dict[str, Any] = {
            "portfolio_name":   portfolio_name,
            "raw_exposures":    {},
            "equity_exposures": {},
            "equity_sum":       0.0,
            "result_text":      "",
            "error":            None,
        }

        if not portfolio_name or not portfolio_name.strip():
            out["error"]       = "Portfolio name must not be empty."
            out["result_text"] = "Error: empty portfolio name."
            return out

        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            try:
                pid, canonical = self._resolve(conn, portfolio_name)
                out["portfolio_name"] = canonical

                # Detect scale
                max_w = conn.execute(_SCALE_SQL, (pid,)).fetchone()[0] or 0
                scale = 100.0 if max_w > 1.0 else 1.0   # normalise to fraction

                rows = conn.execute(_HOLDINGS_SQL, (pid,)).fetchall()
            finally:
                conn.close()

            if not rows:
                out["result_text"] = f"No holdings found for '{canonical}'."
                return out

            # Build raw (whole-portfolio) weights
            sector_weights: dict[str, float] = {}
            equity_weights: dict[str, float] = {}

            for r in rows:
                sector  = r["sector_name"]
                atype   = r["asset_type"]
                weight  = (r["weight_sum"] or 0.0) / scale   # fraction 0-1

                sector_weights[sector] = sector_weights.get(sector, 0.0) + weight

                if atype == "Stock" and sector != "Unclassified":
                    equity_weights[sector] = equity_weights.get(sector, 0.0) + weight
                elif atype == "Stock" and sector == "Unclassified":
                    equity_weights["Unclassified"] = equity_weights.get("Unclassified", 0.0) + weight

            total_all    = sum(sector_weights.values()) or 1.0
            total_equity = sum(equity_weights.values()) or 1.0

            raw_pct = {
                s: round(w / total_all * 100, 2)
                for s, w in sorted(sector_weights.items(), key=lambda x: -x[1])
            }
            equity_pct = {
                s: round(w / total_equity * 100, 2)
                for s, w in sorted(equity_weights.items(), key=lambda x: -x[1])
            }

            out["raw_exposures"]    = raw_pct
            out["equity_exposures"] = equity_pct
            out["equity_sum"]       = round(sum(equity_pct.values()), 2)
            out["result_text"]      = self._fmt(canonical, raw_pct, equity_pct)

        except Exception as exc:
            logger.error("[exposure_tool] %s", exc)
            out["error"]       = str(exc)
            out["result_text"] = f"Error: {exc}"

        return out

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _resolve(self, conn: sqlite3.Connection, name: str) -> tuple[int, str]:
        """
        Exact match first, then case-insensitive substring, then first token.
        Raises ValueError with helpful message if nothing matches.
        """
        rows = conn.execute(_ALL_PORTFOLIOS_SQL).fetchall()
        name_lower = name.strip().lower()

        # 1. Exact (case-insensitive)
        for r in rows:
            if r["portfolio_name"].lower() == name_lower:
                return r["portfolio_id"], r["portfolio_name"]

        # 2. Substring
        matches = [r for r in rows if name_lower in r["portfolio_name"].lower()]
        if len(matches) == 1:
            logger.info("[exposure_tool] Fuzzy matched '%s' → '%s'",
                        name, matches[0]["portfolio_name"])
            return matches[0]["portfolio_id"], matches[0]["portfolio_name"]
        if len(matches) > 1:
            names = [r["portfolio_name"] for r in matches]
            raise ValueError(
                f"Ambiguous portfolio name '{name}' — matches: {names}. "
                "Please be more specific."
            )

        # 3. First significant token
        tokens = [t for t in name_lower.split() if len(t) > 3]
        for token in tokens:
            matches = [r for r in rows if token in r["portfolio_name"].lower()]
            if len(matches) == 1:
                logger.info("[exposure_tool] Token-matched '%s' via '%s' → '%s'",
                            name, token, matches[0]["portfolio_name"])
                return matches[0]["portfolio_id"], matches[0]["portfolio_name"]

        all_names = [r["portfolio_name"] for r in rows]
        raise ValueError(
            f"Portfolio '{name}' not found. Available: {all_names}"
        )

    @staticmethod
    def _fmt(name: str, raw: dict, equity: dict) -> str:
        lines = [
            f"Sector Exposures — {name}",
            "=" * 55,
            f"  {'Sector':<28s} {'Raw %':>7s}  {'Equity-only %':>13s}",
            "-" * 55,
        ]
        all_sectors = sorted(set(raw) | set(equity),
                             key=lambda s: -equity.get(s, raw.get(s, 0)))
        for s in all_sectors:
            r = raw.get(s, 0.0)
            e = equity.get(s, "-")
            e_str = f"{e:>12.2f}%" if isinstance(e, float) else f"{'—':>13s}"
            lines.append(f"  {s:<28s} {r:>6.2f}%  {e_str}")
        lines += [
            "-" * 55,
            f"  {'Equity total':>36s}  {sum(v for v in equity.values()):>12.2f}%",
        ]
        return "\n".join(lines)
