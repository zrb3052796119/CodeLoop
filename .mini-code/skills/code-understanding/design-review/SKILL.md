---
name: design-review
description: Review existing code for design issues, inconsistencies, dead code, and missing test coverage without changing it.
directory: code-understanding
domains: [code, analysis]
scopes: [readonly]
purposes: ["design review", "code audit"]
supported_operations: [review.analyze, review.compare]
tools: [read_file, grep_files, find_symbols]
keywords: [design review, code smell, inconsistency, unused, dead code, technical debt, coverage gap, skill routing]
examples:
  - "Look at this module and tell me if the design has problems"
  - "Is there anything inconsistent or unused in this code?"
  - "Compare the two Skill routing implementations for design tradeoffs"
---

# Design Review

Use this workflow when asked to critique code rather than change it.

1. Read the module(s) end to end before forming an opinion — don't judge a
   function in isolation from its callers.
2. Check for: inconsistent behavior between similar functions/classes, dead
   code (unused functions, unreferenced imports), and public API edges with
   no test coverage.
3. Rank findings by real impact, not by how easy they are to describe —
   a genuine behavioral bug outranks a style nit.
4. State findings as concrete claims ("X and Y disagree on missing-id
   behavior"), not vague impressions ("could be cleaner").
5. Only propose fixes if asked; a design review's job is to surface issues
   accurately, not to rewrite the code uninvited.
