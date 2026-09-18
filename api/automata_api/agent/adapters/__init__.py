"""Model adapters.

Each adapter translates one provider's wire format into the internal
message and delta types the turn engine works with. The engine never sees
HTTP, SSE framing or provider specific field names.
"""
