---
name: lint-and-static-cleanup
description: Run a linter or static analysis command, then fix the reported warnings without changing behavior.
directory: quality
domains: [code, file, execution]
scopes: [readonly, write, destructive]
purposes: ["lint warning repair", "static analysis cleanup"]
supported_operations: [code.update, debug.update]
tools: [read_file, edit_file, run_command]
keywords: [ruff, lint, linter, static analysis, warning, unused import, style, flake8, mypy, F401, E501, E711, E722, F811, F821, F841, "质量检查", "静态检查", "检查命令"]
examples:
  - "Run ruff check and fix what it finds"
  - "Clean up the lint warnings in this file"
  - "Repair the undefined-name error reported by Ruff"
---

# Lint and Static Cleanup

Use this workflow when asked to run or react to a linter/static analyzer.

1. Run the linter directly (e.g. `ruff check .`) — never wrap it in a shell
   string like `bash -c "..."`; pass the program and arguments separately so
   the result stays a real, attributable verification signal.
2. Read every reported warning before editing anything; don't guess from the
   rule code alone.
3. Fix the narrowest thing that resolves each warning — prefer removing dead
   code over suppressing the rule.
4. Re-run the same linter command to confirm a clean result.
5. If a fix could change behavior (not just style), say so explicitly rather
   than applying it silently.
