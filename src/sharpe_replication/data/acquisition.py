from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .io import sha256_file, write_json
from .snapshot import _git_commit

SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "authorization",
    "auth",
    "signature",
    "sig",
    "secret",
    "password",
}


def sanitize_url(url: str) -> str:
    parts = urlsplit(url)
    if not parts.query:
        return url
    sanitized = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            sanitized.append((key, "<redacted>"))
        else:
            sanitized.append((key, value))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sanitized, safe="<>"), parts.fragment))


def _relative_file_hashes(acquisition_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(acquisition_dir.rglob("*")):
        if path.is_file() and path.name != "metadata.json":
            hashes[str(path.relative_to(acquisition_dir))] = sha256_file(path)
    return hashes


def write_raw_acquisition_metadata(
    acquisition_dir: str | Path,
    *,
    provider: str,
    request_type: str,
    requested_date_range: dict[str, str | None],
    requested_symbols: list[str] | None = None,
    source_urls: list[str] | None = None,
    documentation_urls: list[str] | None = None,
    response_count: int | None = None,
    limitations: list[str] | None = None,
    extra_metadata: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Write immutable metadata for a raw acquisition directory.

    The function hashes raw files already present in the directory. Credentials and signed URL
    parameters are redacted before metadata is persisted.
    """

    root = Path(acquisition_dir)
    metadata_path = root / "metadata.json"
    if metadata_path.exists() and not force:
        raise FileExistsError(metadata_path)
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "provider": provider,
        "acquired_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "request_type": request_type,
        "requested_date_range": requested_date_range,
        "requested_symbols": sorted(requested_symbols or []),
        "response_count": response_count,
        "source_urls": [sanitize_url(url) for url in source_urls or []],
        "documentation_urls": [sanitize_url(url) for url in documentation_urls or []],
        "file_hashes": _relative_file_hashes(root),
        "tool_git_commit_sha": _git_commit(),
        "limitations": limitations or [],
        **(extra_metadata or {}),
    }
    write_json(metadata_path, payload)
    return payload
