# Independent review policy

Policy version 5 keeps independent semantic review as the default for behavior changes. Green tests and model-declared criterion IDs do not establish that all requirements were implemented.

A user can supply a concrete acceptance command in the original conversation:

```text
Set the returned value to 3.
Acceptance command: `pytest -q tests/test_value.py`
```

`验收命令：` is also supported. The directive must be an unquoted top-level line in the actual user message. The documentation example above is fenced; quoting that example is not itself an instruction to waive review. A child task prompt, tool output, model-declared coverage, indented text, or block quote cannot provide this authority.

The host may then use the shorter Executor → final Verifier route only if all these conditions hold:

- The user command names concrete relative pytest `.py` positional targets and exactly matches a requested test command. Only `pytest`, `python -m pytest`, or `python3 -m pytest` with the no-value flags `-q`, `-qq`, `-s`, `-v`, `-vv`, `-x`, `--disable-warnings`, `--strict-config`, and `--strict-markers` qualify. Selectors such as `-k`, option values, directories, unknown options, or default discovery retain Reviewer; they can still execute through the ordinary verifier.
- The host confirms the declared write paths and acceptance targets are existing regular files. Directories (even names ending in `.py`), missing/new files, symlink paths or parents, and multiply linked files retain Reviewer. The host checks the source workspace because snapshot copying can change hardlink identity.
- The accepted test target and pytest configuration are outside the Executor’s write scope. These filesystem facts are host observations bound to the baseline, never model-supplied request fields.
- There is exactly one ordinary behavior criterion. Multiple requirements linked to one blanket test retain Reviewer.
- The change is not marked complex and passes the existing low-risk scope limits: at most two write paths and two verification checks, with no broad or sensitive scope. Effective risk remains the maximum of declared and host-inferred risk.
- No review signal appears in the original user request, delegated task, or criterion. English `review`/`audit` and their ordinary inflections, or Chinese `审查`/`审核`/`审计`/`审阅`/`复核`/`评审`, conservatively retain Reviewer even in a policy discussion. This deliberately accepts unnecessary review rather than interpreting ambiguous language as a waiver. Quality, safety, and fact criteria retain Reviewer.

This is an explicit bounded-risk acceptance policy, not a proof of semantic completeness. The user-provided acceptance test can itself miss behavior. The host records this limitation as `semanticCoverageProven: false` in `ReviewRoutingDecision.v1`, along with the original-user digest, exact acceptance commands, request and objective digests, baseline digest, and reasons for retaining or omitting independent review. Internal route name `route.verified_change` means that this accepted test obligation still has to pass final verification; it does not mean that every natural-language requirement has been proven.

The final Verifier, complete frozen diff, current patch and baseline bindings, concurrency protections, and persisted commit-ready seal remain mandatory. Before promotion, the actual frozen paths must be exact declared files, at most two, and remain non-sensitive; directory-prefix matching is insufficient. Current filesystem identities are checked again. Sensitive component recognition includes plural and CamelCase forms such as `permissions.py`, `payments.py`, and `Credentials.ts`. A failed check returns to the existing Executor and receives fresh verification. A checked-but-unsubmitted candidate still requires Reviewer. The reviewer-free route never manufactures an approval verdict.

Policy versions 2, 3, and 4 replay their original stage sequences and artifact shapes. New decisions use version 5 and cannot be reinterpreted as historical exemptions.
