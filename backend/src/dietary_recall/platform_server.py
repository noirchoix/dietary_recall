"""Backward-compatible imports for the API-only v0.4.3 server.

The HTTP/static-file server was retired in v0.4.3. New integrations should
import :func:`dietary_recall.api_app.create_app` or ``serve_api`` directly.
"""

from .api_app import create_app, serve_api


serve_platform = serve_api

__all__ = ["create_app", "serve_api", "serve_platform"]
