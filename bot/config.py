import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.yaml"
DEFAULT_DB = ROOT / "data" / "state.db"
SCREENSHOTS = ROOT / "data" / "screenshots"


def load_dotenv(path=ROOT / ".env"):
    """Carga KEY=VALUE de .env sin pisar variables ya definidas (systemd, GitHub Actions, shell)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_config(path=DEFAULT_CONFIG):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
