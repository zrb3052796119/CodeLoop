---
name: test-driven-development
description: Add or update tests before implementation and verify behavior with focused commands.
directory: testing
domains: [code, file, execution]
scopes: [readonly, write, destructive]
purposes: ["test-first implementation", "test-first code change"]
supported_operations: [test.create, test.execute, code.create, code.update]
tools: [read_file, write_file, edit_file, run_command]
keywords: [test, pytest, tdd, coverage, verify]
examples:
  - "Add tests for this new behavior"
  - "Implement this with test coverage"
  - "Run the focused tests for the new behavior"
  - "Implement a new parser feature test-first"
  - "Update the implementation after a test-first failure"
---

# Test Driven Development

Use this workflow when behavior should be protected by tests.

1. Identify the expected behavior and existing coverage.
2. Add or update the smallest relevant test first.
3. Run the focused test and confirm the failure if appropriate.
4. Implement the behavior.
5. Run focused verification and then adjacent tests.
