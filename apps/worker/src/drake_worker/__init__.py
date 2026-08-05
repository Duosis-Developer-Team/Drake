"""Drake background job runner.

Redis-backed job transport with a strict envelope contract: every job carries
an idempotency key, correlation ID, timeout, retry policy, and dead-letter
metadata. Redis is transport/cache only — never an authoritative business
store; anything durable belongs in PostgreSQL behind the API's modules.
"""
