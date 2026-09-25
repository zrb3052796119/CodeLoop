# Visual-judge feasibility (2026-09-25 research)

> **Status: SHIPPED AND LIVE-VERIFIED.** `inspect_image` shipped
> (`minicode/tools/inspect_image.py`: magic-number + extension + size +
> workspace-path validation, network URLs rejected, explicit vision-model
> configuration with no text-model fallback) and this repository carries a
> working example profile at `.mini-code/agents/visual-judge.md`.
> **Live verification (2026-09-25, final)**: with
> `MINI_CODE_VISION_MODEL=qwen3.8-flash` persisted in the user config
> (plumbed through `load_runtime_config` as runtime `visionModel`), the
> full chain — real config → tool → DashScope vision call — answered
> correctly on a generated test image ("A red circle.", 190 vision
> tokens). Earlier refusals (`qwen-vl-max`/`qwen-vl-plus`/
> `qwen2.5-vl-32b-instruct` → 404 "Model not exist") were account
> availability, not tool defects; out-of-workspace images are rejected
> with `error[path_denied]` (verified live).
>
> Configuration: set `MINI_CODE_VISION_MODEL` (env or `~/.mini-code/.env`)
> or `subagentModels.visual` to a vision-capable model your route serves;
> endpoint/credentials reuse the sub-agent route fields.

Question: can MiniCode borrow ZCode's "visual judge" pattern — a read-only
sub-agent that looks at rendered artifacts (chart/dashboard page images,
exported PDFs) and returns one strict per-page verdict (pass/fail +
evidence)? ZCode ships four plugin variants (documents/pdf/presentations/
spreadsheets visual-judge); our Reviewer covers code and the deterministic
Verifier covers executable checks — nothing covers rendered output.

## Facts established

1. **Local gap**: MiniCode's message pipeline is text-only. `AgentStep.content`
   is a string; neither `openai_adapter` nor `anthropic_adapter` handles
   `image_url` / base64 / vision content blocks. Full multimodal messages
   would be the heavy route (content-block plumbing through both adapters,
   the loop, and compaction).
2. **Model route exists**: the user's sub-agent route is already
   DashScope's OpenAI-compatible endpoint
   (`…/compatible-mode/v1`), which serves `qwen-vl-max` / `qwen-vl-plus`
   and accepts `image_url` content blocks with either public URLs or
   `data:image/jpeg;base64,…` payloads (DashScope compatible-mode docs and
   community walkthroughs, 2026-09-25). One extra `subagentModels` entry
   would light it up.
3. **A light route exists**: an autonomous `inspect_image` tool (the
   dsh-tool-vision pattern) — the tool itself calls the vision model and
   returns the model's text answer into the conversation. The main message
   pipeline stays untouched; no adapter refactor needed.

## Recommendation: GO, via the light route

Build the judge out of parts we already shipped:

1. New read-only tool `inspect_image(path, question)`:
   - local images only (workspace-rooted path resolution like `read_file`;
     absolute/escaping paths rejected), PNG/JPEG/WebP whitelist, size cap
   - file → base64 data URI → one call through the existing
     OpenAI-compatible sub-agent route (same fail-closed credential
     checks; a dedicated `subagentModels["visual"]` style entry or the
     route default)
   - returns the vision model's bounded text answer as the tool result
   - network-URL inputs deliberately NOT supported (no SSRF / exfil channel)
2. A project profile `.mini-code/agents/visual-judge.md`:
   `allowed_tools: inspect_image, read_file, todo_write, subagent_note_*`;
   body = the judge contract (one strict per-page JSON verdict, "judge
   only, never edit").
3. The typed result contract and closing reminder already enforce the
   verdict hand-back; the quality harness measures compliance exactly as
   it does for explore.

Estimated scope: one focused round (tool + profile + tests + a small live
smoke). Risks: prompt injection through image content is contained by the
judge's closed output contract (JSON verdict only, fail-closed
validation); vision-model latency/cost is per-image and explicit.

## Deferred alternative

Full multimodal message content (adapters accept content-block arrays) is
the general solution but touches both adapters, the agent loop, and
compaction. Revisit only if non-judge use cases (screenshot-driven UI
work, diagram reading in explore) show up.

## Sources

- DashScope compatible-mode multimodal usage (base64 `image_url`):
  community walkthrough via [GitCode/CSDN](https://gitcode.csdn.net) and
  [Aliyun Bailian docs](https://docs.bailian.console.aliyun.com).
- Autonomous-tool pattern: dsh-tool-vision `inspect_image` ([GitHub](https://github.com)).
