---
name: regression-from-bug-report
description: Turn a reported bug into a failing test first, then fix it and prove the test now passes.
directory: testing
domains: [code, file, execution]
scopes: [readonly, write, destructive]
purposes: ["bug reproduction", "regression test and repair"]
supported_operations: [debug.analyze, debug.update, test.create, test.execute]
required_operations: [test.create, test.execute]
activation_terms: [bug, report, reported, reproduce, repro, regression, failing test, 用户反馈, 用户报告, 回归测试, 复现]
tools: [read_file, grep_files, write_file, edit_file, test_runner, run_command]
keywords: [bug, reproduce, regression, failing test, fix, report, repro]
examples:
  - "Users report deleting the second item removes the wrong one — fix it"
  - "Write a regression test for this bug then fix it"
  - "Investigate this user-reported bug"
  - "Run the regression test for the reported deletion bug"
---

# Regression From Bug Report

Use this workflow when given a bug report rather than a feature request.

1. Reproduce first. Write a test that fails *for the reported reason* before
   changing any implementation code.
2. Confirm the test fails with the expected error, not an unrelated one — a
   test failing for the wrong reason proves nothing.
3. Only then fix the implementation, keeping the change as narrow as the
   evidence supports.
4. Re-run: the new test must pass and every existing test must still pass. A
   fix that breaks another test is not a fix.
5. Keep the regression test permanently. Its purpose is to fail loudly if
   the bug is ever reintroduced.
