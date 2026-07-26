# MiniCode Dashboard Batch 8A-2.2 Plan

## Purpose

Repair the real Dashboard Tool-approval card that shows only Reject for a safe
workspace-local file edit. The repair must also prevent equivalent filesystem
aliases from leaking an absolute local path in an otherwise allowable Diff.

## Confirmed call graph

```text
write_file / edit_file / patch_file
  -> resolve_tool_path(context, input path, "write")
  -> apply_reviewed_file_change(context, original input path, resolved target)
  -> build_unified_diff(original input path, before, after)
  -> PermissionManager.ensure_edit(resolved target, diff)
  -> PermissionApprovalBroker projection
  -> pending REST / Dashboard card
```

The target projector already emits a relative `targetPath`. The defect is that
the Diff labels still inherit the original input spelling.

## Required behavior

- A resolved target inside the Workspace produces labels such as
  `--- a/code/hello.py` and `+++ b/code/hello.py` for relative, absolute,
  dot-segment, and equivalent local spellings.
- The resulting safe, complete Diff is `reviewable=true`, `redacted=false`,
  with `allow_once` and `deny_once`.
- No absolute Workspace, HOME, temporary, or fixture path appears in broker
  snapshots, HTTP responses, DOM, logs, or Run events.
- Only labels are normalized. Changed content containing a real sensitive path,
  credential, control character, or oversize payload remains deny-only.
- Targets outside the Workspace remain fail-closed and cannot be normalized
  into an apparently local label.
- Approval remains operation-scoped; no allow cache, state-machine, REST, SSE,
  frontend-validator, or TUI decision semantics change.

## Required RED/GREEN matrix

Cover all three real Tools and both new/existing files where applicable:

1. Relative path.
2. Workspace-local absolute path.
3. `.` and safe `..` normalization that resolves inside the Workspace.
4. Filesystem alias/canonical path spelling.
5. Spaces, Unicode, and leading-dash path segments.
6. Escaping symlink or external absolute target.
7. Absolute path or secret in changed file content.
8. Truncated Diff.
9. Real pending GET and decision POST.
10. Allow-before-write / Deny-no-write / cancel-timeout-no-write.
11. Browser card has both buttons only for the safe normalized case.
12. Installed-wheel execution uses the same canonical labels.

## Certification order

Start from active v27. Preserve v1-v27 and accepted semantic gold, freeze the
exact next production delta only after implementation stabilizes, then run
focused Permission/Tool/HTTP/frontend tests, wheel isolation, official semantic
evaluation, two complete pytest runs, static checks, and real 1280x900 browser
acceptance. Batch 8C-2 remains out of scope and resumes after this repair.
