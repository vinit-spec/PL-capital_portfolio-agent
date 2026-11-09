import sys
from pathlib import Path

# Add portfolio_agent root directory to sys.path
AGENT_ROOT = Path(__file__).parent.parent.resolve()
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

