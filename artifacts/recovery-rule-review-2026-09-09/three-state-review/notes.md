# Review notes

Scope: Current fourth-round implementation. Prior review artifacts remain unchanged; new outputs go here.

Verified: 369 tests (16 focused modules) pass. Previous consumer (7), parser, validator/episode cases are closed. New real pytest probes: boolean -q consumes first path; quoted -k expressions collapse to first token; --version gives confirmed recovery without testing. Each match=true/value accepted, negative target control rejects. Multi-recovery validator proof mixes temporal and error relationships; single-recovery controls both reject. Waiting for consumer reviewer final real path/UsageError evidence.

Completed: five P1 finding groups with six real pytest Engine counterexamples and one controlled multi-recovery validator counterexample. Review links and positive/negative controls checked; production hashes in validation.json. Consumer real probes rerun by parent. New findings saved in review.md.

Minor tooling error: initial rg invocation mistakenly treated AGENTS.md as an explicit path; corrected with rg --files -g AGENTS.md, which found none.
