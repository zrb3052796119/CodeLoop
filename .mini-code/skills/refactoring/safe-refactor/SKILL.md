---
name: safe-refactor
description: Refactor code in small behavior-preserving steps with tests and diff review.
directory: refactoring
domains: [code, file, search]
scopes: [readonly, write]
purposes: ["behavior-preserving refactor"]
supported_operations: [refactor.update]
tools: [read_file, grep_files, edit_file]
keywords: [refactor, simplify, migrate, behavior preserving, 行为保持]
examples:
  - "Refactor this module without changing behavior"
---

# Safe Refactor

Use this workflow for behavior-preserving refactors.

1. Read the current behavior and nearby tests.
2. Identify the smallest safe structural change.
3. Avoid mixing refactor and feature changes.
4. Run focused tests or explain the remaining verification gap.
