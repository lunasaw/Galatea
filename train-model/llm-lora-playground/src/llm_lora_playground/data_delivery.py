"""Dataset root resolution and preflight reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .data import DatasetExpectation, DatasetIdentity, DataContractError, validate_dataset


@dataclass(frozen=True)
class DataPreflight:
    root: str
    status: str
    identity: DatasetIdentity | None
    blocked_reasons: list[str]


def resolve_dataset_root(staging_root: Path, explicit_root: Path | None = None) -> Path:
    if explicit_root is not None:
        return explicit_root.expanduser().resolve()
    candidates = [
        staging_root / "output/wechat_aa807aaad90dc4463964",
        staging_root / "data-deal/output/wechat_aa807aaad90dc4463964",
        staging_root / "wechat_aa807aaad90dc4463964",
    ]
    found = [path.resolve() for path in candidates if path.is_dir()]
    if len(found) != 1:
        raise DataContractError(f"expected one dataset root, found {len(found)}")
    return found[0]


def check_data_delivery(root: Path, expectation: DatasetExpectation) -> DataPreflight:
    try:
        identity = validate_dataset(root, expectation)
    except DataContractError as exc:
        return DataPreflight(str(root.resolve()), "blocked", None, [str(exc)])
    reasons: list[str] = []
    source = __import__("json").loads((Path(identity.root) / "manifests/source_manifest.json").read_text(encoding="utf-8"))
    if source.get("authorization_status") != "verified":
        reasons.append(f"authorization_status={source.get('authorization_status')}")
    quality_path = Path(identity.root) / "reports/quality_report.json"
    if quality_path.is_file():
        quality = __import__("json").loads(quality_path.read_text(encoding="utf-8"))
        reasons.extend(str(item) for item in quality.get("blocking_reasons", []))
    return DataPreflight(str(identity.root), "blocked" if reasons else "ok", identity, sorted(set(reasons)))
