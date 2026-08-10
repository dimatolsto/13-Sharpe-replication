from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .certification import certify_report
from .io import read_table, sha256_file, write_json
from .normalize import (
    normalize_corporate_actions,
    normalize_daily_panel,
    normalize_membership,
    normalize_security_master,
)
from .schema import CertificationReport, Status, ValidationReport
from .validation import validate_dataset

DATASET_FILES = {
    "daily_panel": "daily_panel.parquet",
    "membership": "membership.parquet",
    "security_master": "security_master.parquet",
    "corporate_actions": "corporate_actions.parquet",
}


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _write_dataset(snapshot_dir: Path, name: str, frame: pd.DataFrame | None) -> dict[str, Any] | None:
    if frame is None:
        return None
    path = snapshot_dir / DATASET_FILES[name]
    frame.to_parquet(path, index=False)
    return {
        "file": path.name,
        "sha256": sha256_file(path),
        "rows": len(frame),
        "columns": list(frame.columns),
    }


def _existing_snapshot_status(snapshot_dir: Path) -> str | None:
    manifest = snapshot_dir / "manifest.json"
    if not manifest.exists():
        return None
    import json

    data = json.loads(manifest.read_text(encoding="utf-8"))
    return data.get("certification_status")


def write_snapshot(
    snapshot_dir: str | Path,
    *,
    daily_panel: pd.DataFrame,
    membership: pd.DataFrame | None = None,
    security_master: pd.DataFrame | None = None,
    corporate_actions: pd.DataFrame | None = None,
    provider: str = "local",
    source_description: str = "local file import",
    requested_date_range: dict[str, str | None] | None = None,
    provider_capabilities: dict[str, Any] | None = None,
    raw_close_semantics: str = "unknown",
    total_return_source: str = "unknown",
    normalization_version: str = "phase1-v1",
    known_limitations: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    snapshot_dir = Path(snapshot_dir)
    if snapshot_dir.exists():
        existing_status = _existing_snapshot_status(snapshot_dir)
        if not force:
            raise FileExistsError(f"Snapshot already exists: {snapshot_dir}")
        if existing_status == Status.PASS.value:
            raise FileExistsError("Refusing to overwrite a certified snapshot")
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    panel = normalize_daily_panel(daily_panel, source=provider)
    member_frame = normalize_membership(membership, source=provider) if membership is not None else None
    master_frame = (
        normalize_security_master(security_master, source=provider)
        if security_master is not None
        else None
    )
    action_frame = (
        normalize_corporate_actions(corporate_actions, source=provider)
        if corporate_actions is not None
        else None
    )

    validation = validate_dataset(panel, member_frame, master_frame, action_frame)
    certification = certify_report(
        validation,
        raw_close_semantics=raw_close_semantics,
        total_return_source=total_return_source,
        membership_present=member_frame is not None,
        security_master_present=master_frame is not None,
        provider_capabilities=provider_capabilities,
    )

    files = {
        "daily_panel": _write_dataset(snapshot_dir, "daily_panel", panel),
        "membership": _write_dataset(snapshot_dir, "membership", member_frame),
        "security_master": _write_dataset(snapshot_dir, "security_master", master_frame),
        "corporate_actions": _write_dataset(snapshot_dir, "corporate_actions", action_frame),
    }
    files = {key: value for key, value in files.items() if value is not None}
    validation_path = snapshot_dir / "validation_report.json"
    certification_path = snapshot_dir / "certification_report.json"
    write_json(validation_path, validation.to_dict())
    write_json(certification_path, certification.to_dict())
    files["validation_report"] = {
        "file": validation_path.name,
        "sha256": sha256_file(validation_path),
    }
    files["certification_report"] = {
        "file": certification_path.name,
        "sha256": sha256_file(certification_path),
    }

    actual_start = panel["date"].min().date().isoformat() if len(panel) else None
    actual_end = panel["date"].max().date().isoformat() if len(panel) else None
    manifest = {
        "snapshot_id": snapshot_dir.name,
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "schema_version": 1,
        "providers": [provider],
        "source_description": source_description,
        "requested_date_range": requested_date_range or {"start": None, "end": None},
        "actual_date_range": {"start": actual_start, "end": actual_end},
        "row_counts": {key: int(value["rows"]) for key, value in files.items() if "rows" in value},
        "unique_security_counts": {
            "daily_panel": int(panel["security_id"].nunique()),
            "membership": int(member_frame["security_id"].nunique()) if member_frame is not None else 0,
            "security_master": int(master_frame["security_id"].nunique()) if master_frame is not None else 0,
        },
        "files": files,
        "provider_capabilities": provider_capabilities or {},
        "raw_close_semantics": raw_close_semantics,
        "total_return_source": total_return_source,
        "normalization_version": normalization_version,
        "git_commit_sha": _git_commit(),
        "validation_status": validation.status.value,
        "certification_status": certification.status.value,
        "known_limitations": known_limitations or [],
    }
    write_json(snapshot_dir / "manifest.json", manifest)
    return manifest


def hash_snapshot(snapshot_dir: str | Path) -> dict[str, str]:
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.exists():
        raise FileNotFoundError(snapshot_dir)
    hashes: dict[str, str] = {}
    for path in sorted(snapshot_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            hashes[path.name] = sha256_file(path)
    return hashes


def inspect_snapshot(snapshot_dir: str | Path) -> dict[str, Any]:
    import json

    manifest_path = Path(snapshot_dir) / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "snapshot_id": manifest.get("snapshot_id"),
        "providers": manifest.get("providers", []),
        "actual_date_range": manifest.get("actual_date_range"),
        "row_counts": manifest.get("row_counts", {}),
        "unique_security_counts": manifest.get("unique_security_counts", {}),
        "validation_status": manifest.get("validation_status"),
        "certification_status": manifest.get("certification_status"),
        "known_limitations": manifest.get("known_limitations", []),
    }


def load_snapshot(snapshot_dir: str | Path) -> dict[str, pd.DataFrame | ValidationReport | CertificationReport]:
    snapshot_dir = Path(snapshot_dir)
    data: dict[str, pd.DataFrame | ValidationReport | CertificationReport] = {}
    for name, filename in DATASET_FILES.items():
        path = snapshot_dir / filename
        if path.exists():
            data[name] = read_table(path)
    return data
