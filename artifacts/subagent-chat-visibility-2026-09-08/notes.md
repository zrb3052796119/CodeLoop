# Investigation notes

Current Web chat renders bounded provisional assistant/tool events through ChatStreamWriter and app.js. Tool names may include a sub-agent prefix (explore▸read_file), but there is no distinct agent lifecycle UI. Need to preserve actual agent identity, show roles rather than underlying model aliases, and distinguish deterministic Verifier from model workers.

Shared workspace is heavily dirty. Prior multi-agent correctness fixes are complete; a separate Skill task modifies overlapping runtime and UI areas. Keep patches local and preserve unrelated changes. No applicable AGENTS.md was found in the previous audit or current local instruction search.
