"""Vercel entrypoint for the HireProof FastAPI app."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.main import app  # noqa: E402
