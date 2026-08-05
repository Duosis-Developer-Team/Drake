"""Append-only audit foundation.

Audit rows are security evidence: the database enforces append-only via
triggers (see migration 0001), the writer validates inputs, and metadata is
checked so credential-shaped content can never be persisted.
"""

from drake_api.audit.models import AuditEvent
from drake_api.audit.service import AuditEventData, record_audit_event

__all__ = ["AuditEvent", "AuditEventData", "record_audit_event"]
