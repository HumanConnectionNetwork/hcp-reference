from pathlib import Path
from typing import Final


APP_NAME: Final[str] = "HCP Reference Node"
APP_VERSION: Final[str] = "0.1.0"
APP_DESCRIPTION: Final[str] = (
    "Canonical reference implementation of the "
    "Humanitarian Connection Protocol."
)

API_PREFIX: Final[str] = "/hcp"

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DATA_DIRECTORY: Final[Path] = PROJECT_ROOT / "data"
RECORDS_FILE: Final[Path] = DATA_DIRECTORY / "hcp_records.json"


def ensure_data_directory() -> None:
    """
    Ensure that the local data directory exists.

    The reference implementation uses a local JSON file for persistence.
    Production HCP Nodes may replace this storage mechanism without changing
    protocol semantics.
    """
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)

