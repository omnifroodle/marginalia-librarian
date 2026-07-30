"""HTTP API adapter (FastAPI + SSE). Requires the [api] extra."""

from .app import create_app

__all__ = ["create_app"]
