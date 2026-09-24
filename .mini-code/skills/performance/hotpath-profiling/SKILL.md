---
name: hotpath-profiling
description: Find why something is slow by measuring it, before changing any code.
directory: performance
domains: [code, execution, analysis]
scopes: [readonly, destructive]
purposes: ["performance diagnosis", "measured optimization"]
supported_operations: [debug.analyze, review.analyze, refactor.update, test.execute]
activation_terms: [slow, performance, profile, profiling, latency, throughput, bottleneck, timing, seconds, milliseconds, "慢", "性能", "剖析", "延迟", "吞吐", "瓶颈", "耗时", "秒", "毫秒", "热点", "开销", "时间", "分钟", "测速"]
tools: [read_file, grep_files, run_command, test_runner]
keywords: [slow, performance, profiling, benchmark, optimize, latency, bottleneck, timing]
examples:
  - "This command takes 30 seconds, find out why"
  - "Profile the startup path and tell me the bottleneck"
  - "Optimize this module after profiling the startup bottleneck"
  - "Run a benchmark to find which parser stage is slow"
  - "Benchmark parser latency and throughput"
---

# Hot-Path Profiling

Use this workflow when something is slow. Measurement comes before opinion.

1. Reproduce the slowness with a concrete, timed command. "Feels slow" is
   not a baseline; a number is.
2. Measure where the time actually goes (`cProfile`, `-X importtime`, or
   timing around suspected sections). Do not optimize from intuition — the
   bottleneck is routinely not where it looks.
3. Report the measured breakdown before proposing changes.
4. Change one thing, re-measure, and state the delta against the baseline.
   An "optimization" without a before/after number is a guess.
5. Confirm behavior is unchanged: run the tests. A faster wrong answer is
   not an improvement.
6. Stop when the measured gain no longer justifies added complexity, and say
   so rather than continuing to micro-optimize.
