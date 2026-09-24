# Network payload audit — 2026-09-07

Scope: read-only review of `evaluator.py`, `model_probe.py`, all 20 synthetic task texts and their supplied synthetic history, all 17 actual Skill bodies (27,875 bytes total), all 11 `SKILL_DIR.md` markers, and the relevant production prompt/loader/provider/embedding code. This audit made no network call, read no `.env` text, and printed no credential values.

**Conclusion:** the inspected probe has a bounded, task-relevant outbound payload and no execution route for the 20 sample tasks. The only tool-dispatch site in the real model probe is `tool_registry.execute("load_skill", ...)`, guarded by the true route-time Skill load policy. Every non-load request stops the probe; mixed batches are not replayed as if unexecuted calls succeeded. Independent local loader checks do not enter the model's messages or tracker.

| Destination | Outbound material |
| --- | --- |
| `open.bigmodel.cn`, `/api/coding/paas/v4/chat/completions` | Actual production system prompt, current synthetic task and supplied synthetic prior conversation, full built-in tool schemas, model-generated text/tool calls, and exact real `load_skill` results on subsequent calls |
| `dashscope.aliyuncs.com`, `/compatible-mode/v1/embeddings` | Affirmed routing-query text/safe context topics and catalog matching metadata: names, qualified names, descriptions, directories, domains/scopes, keywords/examples and tool names |

The evaluator asserts these configured destinations, model identities, frozen case digest and source inputs before live evaluation. Runtime credentials are used by their existing provider clients solely for authentication; they are not added to prompt text, embedding input, or persisted runtime JSON. Expected Skill labels, rationale and scorer fields remain local. The complete Skill body is not part of the embedding metadata projection.

No real credentials, private keys, customer records or other task-specific private data were found in the reviewed cases, Skills or directory markers. The compatibility `minicode` Skill contains a literal `your-token` documentation placeholder, not an actual token. Credential-shaped literal scanning of all 17 files found no private-key block or common API-key/token pattern; all body hashes matched the frozen catalog. This supports the reviewed payload scope, not a general claim that the whole repository contains no sensitive data.

Ordinary local metadata is present in the outbound prompt: absolute current workspace path, and absolute Skill `PATH` headers after a load. These disclose the username/project path as part of the actual production prompt. Global `~/.claude/CLAUDE.md` and project `CLAUDE.md` were independently checked absent; project memory context is explicitly empty. No arbitrary repository source, user memory or tool-result content is obtained because `read_file`, `run_command`, `git`, `task`, browser and all other task tools are never dispatched.

The full core tool schema remains model-visible; this preserves normal competition with `load_skill`. MCP creation is disabled (`include_mcp=False`), so the configured MCP process/server is neither started nor called. This evaluates the built-in core-tool path rather than the complete MCP-enabled environment.

Only normal local bookkeeping may write outside the run artifact subfolders: catalog construction can observe missing versions in workspace `.mini-code/skill_versions.json` with `.skill_versions.lock`. Live embedding copies the existing cache into the evaluation output and updates that isolated cache. Probe journals use an evaluation-local `RunJournal(data_dir=...)`; no synthetic completed-task outcome is attributed to the live Skill evidence ledger.

Each case permits at most four model calls, each capped at 30 seconds and 2,048 output tokens with no retry. The evaluator uses a 40-second routing deadline. This is a bounded observation of spontaneous Skill loading and subsequent body handoff, not execution or verification of the synthetic task. A successful provider response does not imply correct retrieval or workflow adherence.

## Freeze review

An initial evaluator freeze defect was reported to the root: it searched for `SKILL_DIRECTORY.md`, while production uses `SKILL_DIR.md`, and initially omitted `catalog.json`. **Resolved before live execution:** the final evaluator freezes the actual markers and `catalog.json` and asserts the current discovered catalog equals the snapshot. Independent recheck found 290 frozen files, including all 17 Skill bodies and 11 directory markers; all 290 current file hashes matched. The final evaluator also requires completed model observations before scoring negative/no-load cases as successful, so transport failure cannot masquerade as correct suppression.

Final reviewed file identities:

| File | SHA-256 |
| --- | --- |
| `evaluator.py` | `87273901f39fb3a15ffe2adde1e24f16659ee313edb11b5aed010a9528361715` |
| `model_probe.py` inspected version | `c2630d0b5f0ff00f309b18d3824e1af76119277fce8ddbfce9d2b6a7ab2490c1` |
| `catalog.json` | `48cc132ac292c40fd4b5a0d18797c4bfdcd0bd7d77307e82155e3364382b4238` |
| `cases.json` | `c5930a29a4b8289600df4f48eab3caeb8c4a0f15e0c76d9c0b6ed4e153fcb2cc` |
| `input-freeze.json` | `61108d3a2fabcfdc394de9f24361f291ab4218fb143c22132bba849ad8981bbd` |

Required lock set: all production `minicode/**/*.py`; actual 17 Skill bodies and 11 directory markers; exact discovered catalog; cases; evaluator and model probe; the resolved provider identities and model/embedding configuration allowlist. Do not persist authentication material to establish that lock.
