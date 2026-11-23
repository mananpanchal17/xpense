"""Vercel entry point: exposes the real Flask app (with login), never the preview launcher."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402

app = create_app()
