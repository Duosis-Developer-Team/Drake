"""Catalog bounded module: projects, environments, services, clusters.

The repository manifest remains the intent authority (ADR-0014); this module
stores accepted revisions with explicit provenance. Every catalog row is
created atomically with its RBAC scope node through the application service —
other modules never write catalog tables directly.
"""
