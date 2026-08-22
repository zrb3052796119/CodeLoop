# Hybrid Memory v6 Qwen Holdout

Untouched synthetic promotion holdout for the remote Qwen
`text-embedding-v3` adapter under the existing `hybrid-canonical-v4`
admission protocol. It was authored and byte-frozen after the v5 diagnostic
had established feasibility, but before any v6 embedding or verifier call.

There are eight positives, eight hard negatives, and 24 universally irrelevant
shared backgrounds. A single hard-negative render fails the gate. No threshold,
prompt, provider identity, canary, label, or fixture may change after the first
v6 model call.
