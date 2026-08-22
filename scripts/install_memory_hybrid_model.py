"""Install the exact local E5 model certified by Hybrid Memory promotion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import Any


MODEL_ID = "Xenova/multilingual-e5-small"
MODEL_REVISION = "761b726dd34fb83930e26aab4e9ac3899aa1fa78"
EXPECTED_MODEL_FINGERPRINT = (
    "cb55c8134bf02eeff414a6fcb53a88e5160e45cf74e7a7cf1befbc5a9fa2b230"
)
EXPECTED_FILES = {
    "README.md": "561a19594636657fe033f8b4427a7743b5f6f3a12f16cecc5f286feca0453245",
    "config.json": "cb99455288675345e1a4f411438d5d0adbba5fbd3a67ea4fb03c015433b996c1",
    "onnx/model_quantized.onnx": "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193",
    "quant_config.json": "59d175f15264115f18c698d76e443b5d49fc6c8c599911c421405ef4f236e87d",
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    "special_tokens_map.json": "d05497f1da52c5e09554c0cd874037a083e1dc1b9cfd48034d1c717f1afc07a7",
    "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39",
    "tokenizer_config.json": "a1d6bc8734a6f635dc158508bef000f8e2e5a759c7d92f984b2c86e5ff53425b",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest() -> dict[str, Any]:
    payload = {
        "base_model_id": "intfloat/multilingual-e5-small",
        "dependencies": {
            "huggingface-hub": "0.33.4",
            "numpy": "2.3.1",
            "onnxruntime": "1.22.1",
            "tokenizers": "0.21.2",
        },
        "device": "cpu",
        "files": dict(EXPECTED_FILES),
        "license": "MIT",
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "remote_inference": False,
        "schema_version": "1.0",
        "trust_remote_code": False,
    }
    payload["model_fingerprint"] = hashlib.sha256(
        _stable_json(payload).encode("utf-8")
    ).hexdigest()
    if payload["model_fingerprint"] != EXPECTED_MODEL_FINGERPRINT:
        raise RuntimeError("certified model manifest constants are inconsistent")
    return payload


def install(target: Path) -> dict[str, Any]:
    raw_target = target.expanduser()
    if raw_target.is_symlink() or raw_target.exists():
        raise ValueError("target must not already exist")
    parent = raw_target.parent.resolve(strict=True)
    resolved_target = parent / raw_target.name
    if resolved_target == Path(resolved_target.anchor) or resolved_target == Path.home():
        raise ValueError("refusing broad model installation target")
    from huggingface_hub import snapshot_download

    temporary = Path(tempfile.mkdtemp(dir=parent, prefix=".memory-hybrid-model-"))
    package = temporary / "package"
    package.mkdir(mode=0o700)
    try:
        snapshot = Path(
            snapshot_download(
                repo_id=MODEL_ID,
                revision=MODEL_REVISION,
                allow_patterns=sorted(EXPECTED_FILES),
            )
        )
        for relative, expected_digest in EXPECTED_FILES.items():
            source = snapshot / relative
            if not source.is_file():
                raise RuntimeError(f"certified model file is missing: {relative}")
            destination = package / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if _sha256_file(destination) != expected_digest:
                raise RuntimeError(f"certified model file hash mismatch: {relative}")
        manifest = build_manifest()
        (package / "model_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(package, resolved_target)
        return {
            "target": str(resolved_target),
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "model_fingerprint": EXPECTED_MODEL_FINGERPRINT,
            "installer_huggingface_hub_version": version("huggingface-hub"),
        }
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.target), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
