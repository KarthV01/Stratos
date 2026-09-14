BASELINE = {
    "receipt_ttl_seconds": 2,
    "retention_limit": 2_500,
    "resolve_recovered_pending": False,
}

# This intentionally begins with the same behavior as the broken baseline.
CANDIDATE = {
    "receipt_ttl_seconds": 2,
    "retention_limit": 2_500,
    "resolve_recovered_pending": False,
}
