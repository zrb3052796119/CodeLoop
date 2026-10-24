"""Original report hashes and safe offline recovery are archive contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.restore_archived_artifacts import (
    DEFAULT_MANIFEST,
    ROOT,
    restore_reports,
    verified_reports,
)


def test_checked_in_reports_restore_original_manifest_hashes_without_git(tmp_path):
    reports = verified_reports(ROOT)
    assert len(reports) == 4
    assert restore_reports(reports, tmp_path) == 4
    assert restore_reports(reports, tmp_path) == 0
    source_manifest = json.loads((ROOT / "artifacts/intent-optimization-phase4-2026-09-07/manifest.json").read_text(encoding="utf-8"))
    for relative, original in reports:
        recovered = tmp_path / relative
        assert recovered.read_bytes() == original
        if relative.parent.name == "intent-optimization-phase4-2026-09-07":
            assert hashlib.sha256(recovered.read_bytes()).hexdigest() == source_manifest[relative.name]
    # The archived semantic report was a failed evaluation; archival must not
    # replace that historical outcome with a successful rerun or summary.
    semantic = tmp_path / "artifacts/memory-retrieval-semantic-gap-evaluation.json"
    assert json.loads(semantic.read_text(encoding="utf-8"))["evaluation_passed"] is False


def test_restore_preflights_conflicts_without_overwriting_or_partial_output(tmp_path):
    reports = [(Path("artifacts/first.json"), b"first"),
               (Path("artifacts/existing.json"), b"original")]
    existing = tmp_path / reports[1][0]
    existing.parent.mkdir()
    existing.write_bytes(b"local newer report")
    with pytest.raises(FileExistsError):
        restore_reports(reports, tmp_path)
    assert existing.read_bytes() == b"local newer report"
    assert not (tmp_path / reports[0][0]).exists()


def test_corrupted_compressed_report_is_rejected_before_restore(tmp_path):
    payload = json.loads((ROOT / DEFAULT_MANIFEST).read_text(encoding="utf-8"))
    entry = payload["entries"][0]
    payload["entries"] = [entry]
    archive = tmp_path / entry["archive_path"]
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"corrupt")
    manifest = tmp_path / DEFAULT_MANIFEST
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="compressed archive hash/size mismatch"):
        verified_reports(tmp_path)


@pytest.mark.parametrize("path", ["../outside.json", "/tmp/outside.json", "artifacts/../../outside.json"])
def test_manifest_rejects_escaping_original_paths(tmp_path, path):
    manifest = tmp_path / DEFAULT_MANIFEST
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"schema_version": 1, "entries": [{"original_path": path}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="relative paths"):
        verified_reports(tmp_path)


def test_restore_rejects_symlink_parent_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    output = tmp_path / "output"
    output.mkdir()
    try:
        (output / "artifacts").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("host does not permit creating directory symlinks")
    with pytest.raises(ValueError, match="escapes"):
        restore_reports([(Path("artifacts/report.json"), b"report")], output)
    assert not (outside / "report.json").exists()
