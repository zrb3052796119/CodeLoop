"""Offline probe: disjoint writes can invalidate each other's read dependencies.

Run from the repository root with:
    python3 artifacts/multi-agent-review-2026-09-08/probe_concurrent_write_skew.py

All mutable probe data lives in an automatically removed temporary directory.
Two overlapping transaction lifetimes deterministically model concurrent
workflows that snapshot the same parent before either promotes. This requires
concurrent workflow callers (for example, separate sessions sharing a workspace);
a single parent's sequential workflow calls do not trigger this interleaving.
"""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from minicode.workflow_workspace import WorkflowWorkspaceTransaction


def acceptance_invariant(workspace: Path) -> bool:
    """Represent an acceptance check reading both independently written files."""
    a = int((workspace / "a.txt").read_text(encoding="utf-8"))
    b = int((workspace / "b.txt").read_text(encoding="utf-8"))
    return a + b <= 1


def main() -> None:
    with TemporaryDirectory(prefix="minicode-write-skew-probe-") as directory:
        parent = Path(directory)
        (parent / "a.txt").write_text("0\n", encoding="utf-8")
        (parent / "b.txt").write_text("0\n", encoding="utf-8")
        with (
            WorkflowWorkspaceTransaction(
                directory, baseline_scope_paths=("a.txt",)
            ) as workflow_a,
            WorkflowWorkspaceTransaction(
                directory, baseline_scope_paths=("b.txt",)
            ) as workflow_b,
        ):
            (workflow_a.cwd / "a.txt").write_text("1\n", encoding="utf-8")
            (workflow_b.cwd / "b.txt").write_text("1\n", encoding="utf-8")
            frozen_a = workflow_a.freeze(allowed_paths=("a.txt",))
            frozen_b = workflow_b.freeze(allowed_paths=("b.txt",))

            # Check the exact frozen snapshots used for deterministic checks.
            with frozen_a.verification_workspace() as verification_a:
                a_passes = acceptance_invariant(verification_a)
            with frozen_b.verification_workspace() as verification_b:
                b_passes = acceptance_invariant(verification_b)
            print(f"Isolated acceptance checks: A={a_passes}, B={b_passes}")
            assert a_passes and b_passes

            frozen_a.promote(approved_patch_digest=frozen_a.patch_digest)
            frozen_b.promote(approved_patch_digest=frozen_b.patch_digest)
            print(
                "Promotions completed: "
                f"A={workflow_a.parent_promotion_completed}, "
                f"B={workflow_b.parent_promotion_completed}"
            )
            parent_passes = acceptance_invariant(parent)
            print(f"Combined parent acceptance check: {parent_passes}")
            assert workflow_a.parent_promotion_completed
            assert workflow_b.parent_promotion_completed
            assert not parent_passes, "Write-skew behavior was not reproduced"


if __name__ == "__main__":
    main()
