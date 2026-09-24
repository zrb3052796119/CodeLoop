---
name: structural-rename
description: Rename or move a module, package, or symbol consistently across its directory, imports, tests, and docs.
directory: refactoring
domains: [code, file, search]
scopes: [readonly, write, destructive]
purposes: ["structural rename", "module move"]
supported_operations: [code.rename, code.update]
tools: [read_file, grep_files, edit_file, write_file, run_command]
keywords: [rename, move, migrate, package rename, module rename, reference, references, import]
examples:
  - "Rename the taskkit package to todokit everywhere"
  - "Move this module and update every import"
---

# Structural Rename

Use this workflow for a rename/move that must stay consistent everywhere.

1. Search for every reference to the old name first — imports, string
   literals, test files, README/docs — before changing anything. Don't rely
   on memory of what "should" reference it.
2. Move/rename the directory or file itself.
3. Update every import and reference found in step 1.
4. Re-run the test suite to confirm nothing was missed; a stale reference
   usually surfaces as an ImportError, not a silent bug.
5. Report exactly what was renamed and where, so the user can spot anything
   intentionally left alone (e.g. a public package name kept for compat).
