---
name: secret-and-injection-review
description: Audit code for hardcoded credentials, injection risks, unsafe deserialization, and path traversal.
directory: security
domains: [code, file, search, analysis]
scopes: [readonly]
purposes: ["security audit", "secret and injection review"]
supported_operations: [review.analyze]
tools: [read_file, grep_files, find_references, code_review]
keywords: [security, secret, credential, injection, sql, traversal, unsafe, sanitize, vulnerability]
examples:
  - "Check this module for security problems"
  - "Are there any hardcoded secrets or injection risks here?"
---

# Secret and Injection Review

Use this workflow when auditing for security rather than correctness.

1. Search for credential-shaped literals: API keys, tokens, passwords,
   private keys, connection strings — including in tests and fixtures.
2. Trace untrusted input to dangerous sinks: shell invocation, SQL, `eval`
   or deserialization, file paths, and outbound URLs.
3. For every path built from user input, check whether it can escape its
   intended root (`..`, absolute paths, symlinks).
4. Distinguish a real reachable vulnerability from a theoretical one, and
   say which you found. An unreachable pattern is a note, not a finding.
5. Report findings with the concrete input that triggers them. Do not
   propose fixes unless asked — an accurate finding is the deliverable.
