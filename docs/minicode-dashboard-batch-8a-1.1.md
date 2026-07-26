# MiniCode Dashboard Batch 8A-1.1 Command Review Projection Hardening

## Outcome

The existing Gateway permission authority now classifies command reviews while
`command` and `args` are still structured. Credential-bearing, local-path,
complex-shell, ambiguous, redacted, or truncated requests use one fixed
low-information preview, are marked `reviewable=false`, and expose only
`deny_once`. This is a blocking hardening of Batch 8A-1; it adds no Dashboard
permission UI and does not enter Batch 8A-2.

## Original RED

The previous projector flattened argv with `shlex.join()` before applying a
small set of final-string regular expressions. Split values, mixed flag forms,
compact short options, URL userinfo, and several header/assignment forms could
therefore survive into `commandPreview`. Local absolute arguments could also be
returned or only partially substituted. The original RED was 22 failures and
36 passes, including a real pending HTTP response containing seeded markers.

The old truncator first consumed the complete byte allowance and then appended
a three-byte ellipsis. A declared 4 KiB preview was consequently observed at
4,097 bytes. ASCII, Chinese, emoji, and tiny-budget boundary tests reproduced
the same invariant failure.

## Token-aware fail-closed projection

Classification happens before flattening and checks every structured command
token plus the generated reason. The bounded sensitive-name family covers
password/passwd, token/access-token/auth-token, API key variants, secret,
credential, authorization, cookie, and user/userinfo forms. It recognizes
split and joined long options, colon/equals assignments, case and separator
variants, compact short options, common authentication headers, environment
assignments, bearer/key patterns, and URL userinfo.

The projector does not try to preserve a partially understood command by
hiding only a value. Any match becomes:

- `commandPreview="[REDACTED SENSITIVE REVIEW]"`;
- a fixed reason with no original content;
- `redacted=true`, `complete=false`, and `reviewable=false`;
- `choices=["deny_once"]`;
- HTTP `allow_once` rejected with the existing
  `permission_not_reviewable` error.

Malformed versions, bool-as-version, wrong types, extra fields, and incomplete
reviews remain deny-only and echo none of the rejected object.

## Local paths and shell forms

Absolute command names, standalone absolute arguments, absolute option values,
HOME forms, Workspace-internal absolute paths, Workspace-external paths,
POSIX/macOS paths, Windows drive paths, UNC paths, and paths embedded in shell
snippets are never returned. The safe behavior is the same fixed deny-only
projection. Normal Web URL paths are removed from local-path classification;
URLs with userinfo remain sensitive and deny-only.

Shell composition, redirection, environment assignment, command substitution,
explicit shell interpreter execution, control characters, and other forms that
cannot be safely represented without a shell parser are also deny-only. No
third-party parser or sanitizer was added.

Simple commands with ordinary arguments, Workspace-relative paths, normal
development arguments, ordinary reasons, and Web URLs without userinfo remain
reviewable. Their cwd is projected as a Workspace-relative value. Browser
allow still maps only to the internal `allow_operation`; it executes the
current protected operation once and does not update TUI, Turn, Session, or
persistent allow caches.

## Strict UTF-8 budgets

Truncation now reserves the UTF-8 bytes for its marker before selecting the
payload prefix, discards only an incomplete trailing code point, and then
appends the marker. For budgets smaller than the marker it returns an ASCII
marker that fits the exact allowance. The invariant is:

```text
len(result.encode("utf-8")) <= max_bytes
```

Untruncated values remain byte-identical. Truncation state is truthful, and any
truncated diff, command, or reason remains non-reviewable. The public limits
stay unchanged: 32 KiB diff preview, 4 KiB command preview, 40 KiB review, and
128 KiB snapshot.

## Compatibility and no-content boundaries

`PermissionManager` remains the sole judge, and the broker state machine,
Turn/permission binding, timeout, cancel, capacity, close, tombstones, restart,
late-decision behavior, and final side-effect checkpoints are unchanged.
Existing TUI allow/deny and persistent allow-always/deny-always semantics are
untouched. Headless and non-loopback Gateway remain fail-closed.

Permission Run events still pass through the exact content-free shared event
contract. RunJournal and Run Detail expose only opaque IDs, low-cardinality
kind/Tool/decision fields, and reviewability; no preview, reason, command,
argument, path, Tool input/output, prompt, transcript, credential, or exception
body is accepted.

## Certification and Batch 8A-2 handoff

The production delta is exactly `minicode/permission_approval.py`. The formal
HTML, JavaScript, and CSS are byte-identical and no browser UI claim is made.
Runtime dependencies remain empty. Installed-wheel smoke covers both a safe
command allow and sensitive command allow refusal through the real Gateway
permission endpoints, alongside retained health, static, Chat, Cancel, Status,
Run, SSE, and read-only API surfaces.

The stable Batch 8A-2 interfaces remain unchanged: broker `revision()`, strict
pending GET, strict operation-bound decision POST, and the two content-free
permission Run events. Batch 8A-2 may add invalidation mapping, a pending store,
and explicit Allow/Deny presentation, but must keep the backend authority and
deny-only treatment of non-reviewable commands.
