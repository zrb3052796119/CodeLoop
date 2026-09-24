---
name: concurrency-debugging
description: Diagnose race conditions, deadlocks, and thread-safety bugs that appear intermittently.
directory: debugging
domains: [code, file, search, execution]
scopes: [readonly, destructive]
purposes: ["concurrency diagnosis", "race and deadlock analysis"]
supported_operations: [debug.analyze]
tools: [read_file, grep_files, find_references, run_command]
keywords: [race, deadlock, thread, concurrency, lock, intermittent, flaky, async, parallel]
examples:
  - "This test fails intermittently, I think it's a race"
  - "Find the deadlock in the worker pool"
---

# Concurrency Debugging

Use this workflow for bugs that appear only sometimes, or only under load.

1. Establish whether it is actually nondeterministic: run the failing case
   repeatedly before theorizing. An "intermittent" bug that reproduces every
   time is an ordinary bug.
2. Identify the shared mutable state and enumerate who writes it. Most races
   are one unguarded write, not exotic memory ordering.
3. Check lock acquisition order across all call paths — deadlocks come from
   inconsistent ordering, which is visible statically.
4. Look for state captured before a lock and used after it, and for
   check-then-act sequences that are not atomic.
5. Prefer narrowing the shared state over adding another lock. More locks
   usually means more orderings to get wrong.
6. Verify by running the reproduction many times, not once — a single green
   run proves nothing about a race.
