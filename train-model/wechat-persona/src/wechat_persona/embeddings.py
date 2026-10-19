"""Offline-only BGE embeddings for private memory indexing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Sequence


class EmbeddingContractError(ValueError):
    """Raised when a local embedding model violates the pinned-model contract."""


BGE_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_directory_digest(model_path: str | Path) -> str:
    """Digest regular model files without following links or reading file contents into logs."""

    directory = Path(model_path).expanduser().resolve()
    if not directory.is_dir() or directory.is_symlink():
        raise EmbeddingContractError(f"embedding model must be a real local directory: {directory}")
    files = []
    for path in sorted(directory.rglob("*")):
        if ".cache" in path.relative_to(directory).parts:
            continue
        if path.is_symlink():
            raise EmbeddingContractError(f"embedding model must not contain symlinks: {path}")
        if path.is_file():
            files.append({
                "path": path.relative_to(directory).as_posix(),
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
            })
    if not files:
        raise EmbeddingContractError(f"embedding model directory is empty: {directory}")
    encoded = json.dumps(files, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class LocalBgeEncoder:
    """Load a pinned BGE encoder from disk with all network access disabled."""

    def __init__(
        self,
        model_path: str | Path,
        *,
        revision: str,
        model_digest: str | None = None,
        device: str = "cpu",
        max_length: int = 512,
    ) -> None:
        if not revision or revision.endswith("@main"):
            raise EmbeddingContractError("embedding revision must be an immutable commit, not main")
        self.model_path = Path(model_path).expanduser().resolve()
        observed_digest = model_directory_digest(self.model_path)
        if model_digest and observed_digest != model_digest:
            raise EmbeddingContractError("embedding model directory digest does not match the pinned digest")
        self.model_digest = observed_digest
        self.revision = revision
        self.device = device
        self.max_length = int(max_length)

        from transformers import AutoModel, AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            self.model_path,
            local_files_only=True,
            trust_remote_code=False,
        )
        self.model = AutoModel.from_pretrained(
            self.model_path,
            local_files_only=True,
            trust_remote_code=False,
        ).to(device)
        self.model.eval()

    def _encode(self, texts: Sequence[str], *, batch_size: int, query: bool) -> list[list[float]]:
        import torch
        import torch.nn.functional as functional

        values = [f"{BGE_QUERY_INSTRUCTION}{text}" if query else str(text) for text in texts]
        vectors: list[list[float]] = []
        for start in range(0, len(values), max(1, int(batch_size))):
            batch = self.tokenizer(
                values[start:start + batch_size],
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            )
            batch = {key: value.to(self.device) for key, value in batch.items()}
            with torch.inference_mode():
                output = self.model(**batch).last_hidden_state[:, 0]
                output = functional.normalize(output, p=2, dim=1)
            vectors.extend(output.float().cpu().tolist())
        return vectors

    def encode(self, texts: Sequence[str], batch_size: int = 32) -> list[list[float]]:
        return self._encode(texts, batch_size=batch_size, query=False)

    def encode_documents(self, texts: Sequence[str], batch_size: int = 32) -> list[list[float]]:
        return self._encode(texts, batch_size=batch_size, query=False)

    def encode_query(self, text: str) -> list[float]:
        return self._encode([text], batch_size=1, query=True)[0]


__all__ = [
    "BGE_QUERY_INSTRUCTION",
    "EmbeddingContractError",
    "LocalBgeEncoder",
    "model_directory_digest",
]
