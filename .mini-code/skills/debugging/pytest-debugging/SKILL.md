---
name: pytest-debugging
description: Debug pytest failures by reading error output, locating failing code, and rerunning focused tests.
directory: debugging
domains: [code, file, search, execution]
scopes: [readonly, destructive]
purposes: ["pytest failure diagnosis", "pytest failure repair"]
supported_operations: [debug.analyze, debug.update, test.execute]
tools: [read_file, grep_files, run_command]
keywords: [pytest, failure, assertion, traceback, test]
examples:
  - "Debug this pytest failure"
  - "Why is tests/test_agent_loop.py failing?"
  - "Repair this broken pytest test"
  - "Run pytest on the failing assertion"
---

# Pytest Debugging

Use this workflow for pytest failures.

1. Read the exact failure output before changing code.
2. Locate the failing test and the code under test.
3. Form one hypothesis from the assertion or traceback.
4. Make the smallest fix.
5. Run the focused pytest command first, then broader tests if needed.
