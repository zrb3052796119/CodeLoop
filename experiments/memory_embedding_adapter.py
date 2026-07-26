"""Offline embedding adapters for the Retrieval Phase 3B prototype."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable


Vector = tuple[float, ...]
PINNED_MODEL_ID = "Xenova/multilingual-e5-small"
PINNED_MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
PINNED_MODEL_LICENSE = "MIT"
MODEL_FILES = (
    "README.md",
    "config.json",
    "quant_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "onnx/model_quantized.onnx",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dimension: int,
    require_normalized: bool,
) -> tuple[Vector, ...]:
    if len(vectors) != expected_count:
        raise ValueError("embedding result count mismatch")
    validated: list[Vector] = []
    for vector in vectors:
        if len(vector) != expected_dimension:
            raise ValueError("embedding dimension mismatch")
        values = tuple(float(item) for item in vector)
        if not values:
            raise ValueError("embedding vector is empty")
        if not all(math.isfinite(item) for item in values):
            raise ValueError("embedding vector contains NaN or Inf")
        norm = math.sqrt(sum(item * item for item in values))
        if norm <= 1e-12:
            raise ValueError("embedding vector has zero norm")
        if require_normalized and not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError("embedding vector is not normalized")
        validated.append(values)
    return tuple(validated)


@runtime_checkable
class EmbeddingAdapter(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def model_revision(self) -> str: ...

    @property
    def embedding_dimension(self) -> int: ...

    @property
    def normalize(self) -> bool: ...

    @property
    def batch_size(self) -> int: ...

    @property
    def device(self) -> str: ...

    @property
    def model_fingerprint(self) -> str: ...

    def encode_queries(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...

    def encode_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]: ...


@dataclass(frozen=True)
class DeterministicFakeEmbeddingAdapter:
    """Hash projection used only by unit tests and failure injection."""

    dimension: int = 24
    configured_batch_size: int = 32

    def __post_init__(self) -> None:
        if self.dimension <= 0 or self.configured_batch_size <= 0:
            raise ValueError("fake adapter dimension and batch size must be positive")

    @property
    def model_id(self) -> str:
        return "test-only/deterministic-fake"

    @property
    def model_revision(self) -> str:
        return "v1"

    @property
    def embedding_dimension(self) -> int:
        return self.dimension

    @property
    def normalize(self) -> bool:
        return True

    @property
    def batch_size(self) -> int:
        return self.configured_batch_size

    @property
    def device(self) -> str:
        return "cpu-test-only"

    @property
    def model_fingerprint(self) -> str:
        return hashlib.sha256(
            f"{self.model_id}:{self.model_revision}:{self.dimension}".encode()
        ).hexdigest()

    def _encode(self, texts: Sequence[str], prefix: str) -> tuple[Vector, ...]:
        vectors: list[Vector] = []
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise ValueError("embedding input must be non-empty text")
            values = [0.0] * self.dimension
            normalized = " ".join(text.casefold().split())
            terms = normalized.split() or [normalized]
            for position, term in enumerate(terms):
                digest = hashlib.sha256(f"{prefix}:{position}:{term}".encode()).digest()
                for offset in range(0, len(digest), 2):
                    index = digest[offset] % self.dimension
                    values[index] += 1.0 if digest[offset + 1] & 1 else -1.0
            norm = math.sqrt(sum(item * item for item in values))
            if norm <= 1e-12:
                values[0] = 1.0
                norm = 1.0
            vectors.append(tuple(item / norm for item in values))
        return validate_vectors(
            vectors,
            expected_count=len(texts),
            expected_dimension=self.dimension,
            require_normalized=True,
        )

    def encode_queries(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return self._encode(texts, "query")

    def encode_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return self._encode(texts, "document")


class LocalEmbeddingAdapter:
    """Pinned local ONNX E5 adapter. Construction never downloads files."""

    def __init__(
        self,
        model_path: Path,
        *,
        batch_size: int = 32,
        normalize: bool = True,
        device: str = "cpu",
    ) -> None:
        self._path = Path(model_path).expanduser().resolve(strict=True)
        if not self._path.is_dir():
            raise ValueError("model_path must be a local directory")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if device != "cpu":
            raise ValueError("Phase 3B local adapter supports CPU only")
        manifest_path = self._path / "model_manifest.json"
        if not manifest_path.is_file():
            raise ValueError("local model manifest is missing")
        self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._validate_manifest()
        self._batch_size = int(batch_size)
        self._normalize = bool(normalize)
        self._device = device
        try:
            import numpy as np
            import onnxruntime as ort
            from tokenizers import Tokenizer
        except ImportError as exc:
            raise RuntimeError(
                "LocalEmbeddingAdapter requires numpy, onnxruntime, and tokenizers"
            ) from exc
        self._np = np
        self._tokenizer = Tokenizer.from_file(str(self._path / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding(pad_id=0, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(self._path / "onnx" / "model_quantized.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        config = json.loads((self._path / "config.json").read_text(encoding="utf-8"))
        self._dimension = int(config.get("hidden_size", 0))
        if self._dimension <= 0:
            raise ValueError("model config has no valid hidden_size")

    def _validate_manifest(self) -> None:
        if self._manifest.get("model_id") != PINNED_MODEL_ID:
            raise ValueError("unexpected local model id")
        if self._manifest.get("model_revision") != PINNED_MODEL_REVISION:
            raise ValueError("unexpected local model revision")
        if self._manifest.get("trust_remote_code") is not False:
            raise ValueError("trust_remote_code must remain disabled")
        hashes = self._manifest.get("files")
        if not isinstance(hashes, dict):
            raise ValueError("model manifest file hashes are missing")
        for relative in MODEL_FILES:
            path = self._path / relative
            expected = hashes.get(relative)
            if not path.is_file() or expected != _sha256_file(path):
                raise ValueError(f"local model file hash mismatch: {relative}")

    @property
    def model_id(self) -> str:
        return str(self._manifest["model_id"])

    @property
    def model_revision(self) -> str:
        return str(self._manifest["model_revision"])

    @property
    def embedding_dimension(self) -> int:
        return self._dimension

    @property
    def normalize(self) -> bool:
        return self._normalize

    @property
    def batch_size(self) -> int:
        return self._batch_size

    @property
    def device(self) -> str:
        return self._device

    @property
    def model_fingerprint(self) -> str:
        return str(self._manifest["model_fingerprint"])

    @property
    def dependency_versions(self) -> dict[str, str]:
        return {
            name: importlib.metadata.version(name)
            for name in ("numpy", "onnxruntime", "tokenizers")
        }

    def _encode(self, texts: Sequence[str], prefix: str) -> tuple[Vector, ...]:
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding input must be non-empty text")
        output: list[Vector] = []
        for start in range(0, len(texts), self._batch_size):
            batch = [f"{prefix}: {text}" for text in texts[start : start + self._batch_size]]
            encodings = self._tokenizer.encode_batch(batch)
            input_ids = self._np.asarray([item.ids for item in encodings], dtype=self._np.int64)
            attention = self._np.asarray(
                [item.attention_mask for item in encodings], dtype=self._np.int64
            )
            feeds: dict[str, Any] = {}
            for item in self._session.get_inputs():
                if item.name == "input_ids":
                    feeds[item.name] = input_ids
                elif item.name == "attention_mask":
                    feeds[item.name] = attention
                elif item.name == "token_type_ids":
                    feeds[item.name] = self._np.zeros_like(input_ids)
                else:
                    raise ValueError(f"unsupported ONNX input: {item.name}")
            hidden = self._session.run(None, feeds)[0]
            if hidden.ndim == 3:
                mask = attention[..., None].astype(hidden.dtype)
                pooled = (hidden * mask).sum(axis=1) / self._np.maximum(mask.sum(axis=1), 1.0)
            elif hidden.ndim == 2:
                pooled = hidden
            else:
                raise ValueError("unexpected ONNX embedding output rank")
            if self._normalize:
                norms = self._np.linalg.norm(pooled, axis=1, keepdims=True)
                if self._np.any(norms <= 1e-12):
                    raise ValueError("embedding vector has zero norm")
                pooled = pooled / norms
            output.extend(tuple(float(value) for value in row) for row in pooled)
        return validate_vectors(
            output,
            expected_count=len(texts),
            expected_dimension=self._dimension,
            require_normalized=self._normalize,
        )

    def encode_queries(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return self._encode(texts, "query")

    def encode_documents(self, texts: Sequence[str]) -> tuple[Vector, ...]:
        return self._encode(texts, "passage")


def download_pinned_local_model(target_dir: Path, *, allow_download: bool = False) -> dict[str, Any]:
    """Explicit opt-in download. This function never receives query or memory data."""
    if not allow_download:
        raise PermissionError("model download requires --allow-model-download")
    target = Path(target_dir).expanduser().resolve()
    if target.exists() and target.is_symlink():
        raise ValueError("model directory cannot be a symlink")
    target.mkdir(parents=True, exist_ok=True)
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("huggingface_hub is required for explicit model download") from exc
    snapshot_download(
        repo_id=PINNED_MODEL_ID,
        revision=PINNED_MODEL_REVISION,
        local_dir=target,
        allow_patterns=list(MODEL_FILES),
    )
    files = {relative: _sha256_file(target / relative) for relative in MODEL_FILES}
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "model_id": PINNED_MODEL_ID,
        "model_revision": PINNED_MODEL_REVISION,
        "base_model_id": "intfloat/multilingual-e5-small",
        "license": PINNED_MODEL_LICENSE,
        "device": "cpu",
        "trust_remote_code": False,
        "remote_inference": False,
        "files": files,
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("huggingface-hub", "numpy", "onnxruntime", "tokenizers")
        },
    }
    payload["model_fingerprint"] = hashlib.sha256(_stable_json(payload).encode()).hexdigest()
    temporary = target / ".model_manifest.json.tmp"
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target / "model_manifest.json")
    return payload
