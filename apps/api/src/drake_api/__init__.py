"""Drake control plane API.

A FastAPI modular monolith. Domain modules live under ``drake_api.modules``
and must not write to each other's tables directly; cross-module access goes
through application services.
"""
