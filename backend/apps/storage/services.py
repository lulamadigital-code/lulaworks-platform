"""Storage quota enforcement (DATA_MODEL §10 / SAAS_PLATFORM §3).

Quota checked BEFORE every upload. `Company.storage_used_bytes` is the cached
aggregate; kept in sync as files are registered/removed.
"""

from dataclasses import dataclass

from apps.core.context import system_scope


@dataclass
class QuotaResult:
    allowed: bool
    warn: bool = False
    reason: str = ""


def check_quota(company, incoming_bytes: int) -> QuotaResult:
    quota = company.storage_quota_bytes
    used = company.storage_used_bytes
    if used + incoming_bytes > quota:
        return QuotaResult(False, reason="Storage quota exceeded — buy more storage or free space.")
    if used + incoming_bytes > int(quota * 0.9):
        return QuotaResult(True, warn=True, reason="Storage is over 90% full.")
    return QuotaResult(True)


def register_upload(company, size_bytes: int) -> None:
    """Increment the cached storage usage. Called after a StorageFile is saved."""
    with system_scope():
        company.storage_used_bytes = company.storage_used_bytes + size_bytes
        company.save(update_fields=["storage_used_bytes"])
