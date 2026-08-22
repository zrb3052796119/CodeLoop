# Hybrid Memory v7 Qwen Holdout

Untouched synthetic promotion holdout for the remote Qwen
`text-embedding-v3` adapter after a transport-only TLS defect invalidated v6
before any verifier response was received. v7 uses new objects, wording,
background memories, and case identifiers. It was authored and byte-frozen
before any v7 embedding or verifier call.

There are eight positives, eight hard negatives, and 24 universally irrelevant
shared backgrounds. A single hard-negative render fails the gate. No threshold,
prompt, provider identity, canary, label, fixture, or implementation behavior
may change after the first v7 model call.
