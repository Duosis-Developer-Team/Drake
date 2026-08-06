"""Integrations bounded module.

Owns integration registration/health projection. Never writes catalog
tables directly; attaches to catalog entities only via scope ids. Raw
provider configuration lives behind external references (``config_ref``)
that are never exposed through the API.
"""
