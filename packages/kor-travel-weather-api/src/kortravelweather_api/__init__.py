"""Public REST API package for kor-travel-weather."""

__version__ = "0.1.0.dev0"

from .app import app, create_app

__all__ = ["__version__", "app", "create_app"]
