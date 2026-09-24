---
name: dependency-hygiene
description: Add, upgrade, or remove a third-party dependency and verify nothing broke.
directory: dependencies
domains: [code, file, execution]
scopes: [readonly, write, destructive, external]
purposes: ["dependency addition", "dependency upgrade or removal"]
supported_operations: [configure.update, review.analyze, test.execute]
tools: [read_file, grep_files, edit_file, run_command, test_runner, web_fetch]
keywords: [dependency, package, upgrade, requirements, pyproject, version, "dependency pin", "package pin", "version pin", "pinned version", "requirements pin", install, library]
examples:
  - "Add a YAML parser to this project"
  - "Upgrade the pinned pytest version and make sure tests still pass"
  - "Review the dependency config for stale version pins"
---

# Dependency Hygiene

Use this workflow when changing what the project depends on.

1. Check first whether the standard library or an existing dependency
   already covers the need. A new dependency is a permanent maintenance and
   supply-chain cost.
2. Read the project's existing dependency declaration and match its
   conventions — pinning style, optional groups, where dev-only deps go.
3. State the actual version being added and why that constraint. "Latest" is
   not a constraint.
4. Run the full test suite after the change, not just the code that uses the
   new dependency. Upgrades break things transitively.
5. If removing a dependency, search for every import first — including in
   tests, scripts, and optional code paths.
