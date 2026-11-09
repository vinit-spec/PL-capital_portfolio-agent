"""
agent/portfolio_agent.py
------------------------
Portfolio Analytics Agent — orchestrates tool selection via Gemini
function-calling with MANUAL dispatch (automatic function calling disabled).

Usage:
    python -m agent.portfolio_agent
    python -m agent.portfolio_agent --question "How many portfolios do we have?"
    python -m agent.portfolio_agent --question "..." --db path/to/portfolio.db
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
HERE         = Path(__file__).parent.parent.resolve()
DEFAULT_DB   = HERE / "portfolio.db"
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
MAX_INPUT_LEN = 2000

# ---------------------------------------------------------------------------
# Tool declarations (Gemini function-calling schema)
# ---------------------------------------------------------------------------
_SQL_DECL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="sql_query",
            description=(
                "Answer questions about portfolio data by converting the "
                "natural language question into a SQL query and executing it. "
                "Use for counts, lists, rankings, aggregations, comparisons, "
                "risk metrics, performance data, transactions, or any structured "
                "question about portfolios, securities, holdings, or benchmarks."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "question": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="The natural language question to answer.",
                    )
                },
                required=["question"],
            ),
        )
    ]
)

_EXPOSURE_DECL = genai.protos.Tool(
    function_declarations=[
        genai.protos.FunctionDeclaration(
            name="exposure_calculator",
            description=(
                "Calculate the sector exposure breakdown for a named portfolio. "
                "Returns the percentage allocation to each equity sector "
                "(bonds excluded). Use when the user asks about sector exposure, "
                "sector allocation, or sector breakdown of a specific portfolio."
            ),
            parameters=genai.protos.Schema(
                type=genai.protos.Type.OBJECT,
                properties={
                    "portfolio_name": genai.protos.Schema(
                        type=genai.protos.Type.STRING,
                        description="Name of the portfolio (approximate match is fine).",
                    )
                },
                required=["portfolio_name"],
            ),
        )
    ]
)

_SYSTEM_PROMPT = """\
You are a portfolio analytics assistant for PL Capital.
You MUST use one of the two available tools for every question — never answer from memory.

Tool selection guide:
• sql_query          — any structured data question (counts, names, values, rankings,
                       AUM, risk metrics, performance, transactions, etc.)
• exposure_calculator — sector exposure / sector allocation / sector breakdown
                        for a specific named portfolio.

When calling a tool, pass the user's question verbatim to sql_query, or the
portfolio name (as given by the user) to exposure_calculator.
"""


def _call_with_backoff(fn, *args, max_attempts: int = 5, **kwargs):
    """Call *fn* with exponential backoff on 429 / ResourceExhausted."""
    delay = 2.0
    for attempt in range(max_attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            msg = str(exc).lower()
            if "429" in msg or "resource_exhausted" in msg or "quota" in msg:
                if attempt + 1 == max_attempts:
                    raise
                logger.warning("Rate-limited (attempt %d/%d). Sleeping %.1fs…",
                               attempt + 1, max_attempts, delay)
                time.sleep(delay)
                delay = min(delay * 2, 60.0)
            else:
                raise


class PortfolioAgent:
    """
    AI agent that routes portfolio questions to the right tool using
    Gemini function-calling with manual dispatch.
    """

    def __init__(self, db_path: Path = DEFAULT_DB, api_key: str | None = None):
        self.db_path = Path(db_path)

        key = api_key or os.getenv("GEMINI_API_KEY")
        if not key:
            raise EnvironmentError(
                "GEMINI_API_KEY not set. Add it to .env or export it."
            )
        genai.configure(api_key=key)

        # Disable automatic function calling so we handle dispatch ourselves
        self._model = genai.GenerativeModel(
            model_name=DEFAULT_MODEL,
            system_instruction=_SYSTEM_PROMPT,
            tools=[_SQL_DECL, _EXPOSURE_DECL],
        )

        from agent.tools.sql_tool import SQLQueryTool
        from agent.tools.exposure_tool import ExposureCalculatorTool

        # SQL tool gets its own lightweight model (no tools attached)
        self._sql_gen_model = genai.GenerativeModel(model_name=DEFAULT_MODEL)
        self._sql_tool      = SQLQueryTool(db_path=self.db_path, model=self._sql_gen_model)
        self._exp_tool      = ExposureCalculatorTool(db_path=self.db_path)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def answer(self, question: str) -> dict[str, Any]:
        """
        Returns:
        {
            question    : str,
            tool_used   : str | None,
            tool_input  : dict | None,
            tool_result : dict | None,
            answer      : str,
            error       : str | None,
        }
        """
        out: dict[str, Any] = {
            "question":    question,
            "tool_used":   None,
            "tool_input":  None,
            "tool_result": None,
            "answer":      "",
            "error":       None,
        }

        # Input validation
        question = (question or "").strip()
        if not question:
            out["error"] = out["answer"] = "Please enter a question."
            return out
        if len(question) > MAX_INPUT_LEN:
            out["error"] = out["answer"] = (
                f"Input too long ({len(question)} chars). Max {MAX_INPUT_LEN}."
            )
            return out

        try:
            # ── Turn 1: ask Gemini which tool to call ──────────────────
            chat = self._model.start_chat()
            resp = _call_with_backoff(
                chat.send_message,
                question,
                generation_config=genai.types.GenerationConfig(
                    # Disable automatic function calling at request level
                ),
            )

            fn_call = self._extract_fn_call(resp)
            if fn_call is None:
                # Gemini responded with text — log and return as-is
                out["answer"] = resp.text.strip()
                logger.warning("[agent] No tool call in response; returning text.")
                return out

            tool_name = fn_call.name
            tool_args = dict(fn_call.args)
            out["tool_used"]  = tool_name
            out["tool_input"] = tool_args
            logger.info("[agent] Tool: %s | Args: %s", tool_name, tool_args)

            # ── Tool dispatch ──────────────────────────────────────────
            if tool_name == "sql_query":
                tool_result = self._sql_tool.run(tool_args.get("question", question))
            elif tool_name == "exposure_calculator":
                tool_result = self._exp_tool.run(tool_args.get("portfolio_name", ""))
            else:
                raise ValueError(f"Unknown tool name: {tool_name!r}")

            out["tool_result"] = tool_result
            if tool_result.get("error"):
                out["error"]  = tool_result["error"]
                out["answer"] = tool_result["result_text"]
                return out

            # ── Turn 2: send result back for a natural-language answer ─
            fn_resp_part = genai.protos.Part(
                function_response=genai.protos.FunctionResponse(
                    name=tool_name,
                    response={"result": tool_result["result_text"]},
                )
            )
            final = _call_with_backoff(chat.send_message, fn_resp_part)
            out["answer"] = final.text.strip()

        except Exception as exc:
            logger.error("[agent] %s", exc, exc_info=True)
            out["error"]  = str(exc)
            out["answer"] = f"Error: {exc}"

        return out

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_fn_call(response):
        for part in response.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                return part.function_call
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="PL Capital Portfolio Analytics Agent (CLI)"
    )
    p.add_argument("-q", "--question", help="Single question (non-interactive).")
    p.add_argument(
        "--db", default=str(DEFAULT_DB),
        help="Path to portfolio.db (default: auto-detected).",
    )
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show tool name and raw result.")
    return p


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    args = _build_parser().parse_args()
    db   = Path(args.db).resolve()

    if not db.exists():
        print(f"Database not found at {db}. Running setup_db.py …")
        from setup_db import setup_database
        conn = setup_database(db)
        conn.close()

    agent = PortfolioAgent(db_path=db)

    if args.question:
        res = agent.answer(args.question)
        print("\n" + "=" * 60)
        print(f"Q: {res['question']}")
        if args.verbose:
            print(f"Tool: {res['tool_used']} | Input: {res['tool_input']}")
        print("-" * 60)
        print(res["answer"])
        print("=" * 60)
        return

    # Interactive REPL
    print("\n🏦  PL Capital Portfolio Analytics Agent")
    print("    Type 'exit' or Ctrl-C to quit.\n")
    while True:
        try:
            q = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if q.lower() in {"exit", "quit", "q"}:
            print("Goodbye!")
            break
        if not q:
            continue
        res = agent.answer(q)
        if args.verbose and res.get("tool_used"):
            print(f"  [tool: {res['tool_used']}]")
        print(f"\nAgent: {res['answer']}\n")


if __name__ == "__main__":
    main()
