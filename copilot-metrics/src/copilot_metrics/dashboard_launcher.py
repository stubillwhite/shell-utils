import subprocess
import sys
from pathlib import Path


def launch(path: Path) -> int:
    arguments = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(Path(__file__).with_name("dashboard.py")),
        "--server.address=127.0.0.1",
        "--server.port=8502",
        "--browser.gatherUsageStats=false",
        "--theme.base=dark",
        "--theme.primaryColor=#6CB6FF",
        "--theme.backgroundColor=#0E1117",
        "--theme.secondaryBackgroundColor=#161B22",
        "--theme.textColor=#E6EDF3",
        "--theme.font=sans-serif",
        "--server.headless=true",
        "--",
        "--db",
        str(path.resolve()),
    ]
    try:
        return subprocess.run(arguments).returncode
    except KeyboardInterrupt:
        return 130
