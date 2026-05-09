"""Vercel ASGI entry: default layouts expect api/main.py; app lives under eosp."""
from eosp.main import app

__all__ = ["app"]
