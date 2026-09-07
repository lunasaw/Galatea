import hashlib
import json
import math


def download_json_view(ref, s3_client):
    response = s3_client.get_object(Bucket=ref["bucket"], Key=ref["key"], VersionId=ref["version_id"])
    if response.get("VersionId") != ref["version_id"] or response.get("ContentLength") != ref["size_bytes"]:
        raise ValueError("S3 object identity does not match frozen view")
    body = response["Body"]
    try:
        raw = body.read(ref["size_bytes"] + 1)
    finally:
        body.close()
    if len(raw) != ref["size_bytes"] or hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("frozen dataset view failed integrity verification")
    value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    if not isinstance(value, list) or len(value) > 1_000_000:
        raise ValueError("dataset view must be a JSON row array")
    return value


def load_role_views(binding, loader):
    expected = ["test"] if binding["role"] == "evaluate" else ["train", "validation"]
    if set(binding["views"]) != set(expected):
        raise ValueError("unexpected role view")
    return {name: loader(binding["views"][name]) for name in expected}


def matrix(rows, feature_names, target_name):
    if not rows or not feature_names or len(feature_names) > 1024 or len(set(feature_names)) != len(feature_names):
        raise ValueError("invalid matrix dimensions")
    expected = set(feature_names) | {target_name}
    if any(not isinstance(row, dict) or set(row) != expected for row in rows):
        raise ValueError("row schema mismatch")
    features = [[float(row[name]) for name in feature_names] for row in rows]
    targets = [float(row[target_name]) for row in rows]
    if not all(math.isfinite(value) for row in features for value in row) or not all(map(math.isfinite, targets)):
        raise ValueError("non-finite numeric value")
    return features, targets
