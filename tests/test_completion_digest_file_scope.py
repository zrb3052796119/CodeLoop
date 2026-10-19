"""A file-scoped reader must still bind nested candidate bytes."""

from pathlib import Path

from minicode.deterministic_verifier import VerificationCheck
from minicode.implementation_execution import ImplementationSpec, implementation_candidate_digest
from minicode.permissions import PermissionDeniedError
from minicode.tooling import ToolContext


def test_nested_file_read_authority_does_not_turn_candidate_digest_into_names_only(tmp_path):
    implementation = tmp_path / "pkg" / "app.py"
    regression = tmp_path / "tests" / "test_app.py"
    implementation.parent.mkdir()
    regression.parent.mkdir()
    implementation.write_text("def quantity():\n    return 2\n")
    regression.write_text("def test_quantity():\n    assert True\n")
    allowed = {implementation, regression}

    class FileScopedPermissions:
        def ensure_path_access(self, target, intent):
            if intent != "read" or Path(target) not in allowed:
                raise PermissionDeniedError("only assigned files are readable",
                                            code="path_assignment_scope_denied")

    spec = ImplementationSpec(("pkg/app.py", "tests/test_app.py"), (
        VerificationCheck("public", "tests", ("python3", "-m", "pytest", "-q", "tests/test_app.py"), 20),
    ), "Change quantity in pkg/app.py and verify its regression.")
    context = ToolContext(cwd=str(tmp_path), permissions=FileScopedPermissions())
    original = implementation_candidate_digest(spec, context)
    implementation.write_text("def quantity():\n    return 999\n")
    changed_source = implementation_candidate_digest(spec, context)
    assert changed_source != original, (
        "Directory permission is not a prerequisite for reading an explicitly allowed file. "
        "Skipping pkg/ drops the actual candidate bytes from the completion identity.")
    regression.write_text("def test_quantity():\n    assert False\n")
    changed_regression = implementation_candidate_digest(spec, context)
    assert changed_regression != changed_source, "test bytes are also completion inputs"
