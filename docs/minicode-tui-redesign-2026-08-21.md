# MiniCode Conversation-First TUI Redesign

Status: completed and regression-verified on 2026-08-21.

## Outcome

MiniCode now opens directly into one responsive, conversation-first terminal
surface. The legacy banner/quick-start shell no longer flashes before the TUI,
the absolute workspace path cannot break the header, and the transcript owns
the available vertical space instead of competing with three framed panels.

The interaction contract is also repaired: Enter submits an exact slash
command, Ctrl+J inserts a newline, and bracketed multi-line paste no longer
constructs an invalid input event.

## Experience contract

- One shell: no duplicate startup banner or session notice before alternate
  screen mode.
- Conversation first: compact project/model header, dominant transcript,
  neutral composer and one contextual status line.
- Responsive: steady-state chrome remains bounded at 60, 80 and 120 columns;
  the header shows a project basename rather than an absolute path.
- Progressive disclosure: the slash palette is height-bounded and its exact
  line count is removed from the transcript budget.
- Quiet visual hierarchy: one slate-indigo structural accent, semantic colors
  for conversation/tool states, no random emoji tips or idle animation.
- Safety stays prominent: the permission decision surface keeps its stronger
  bordered treatment and existing keyboard controls.

## Production changes

- `minicode/main.py`: suppress the legacy prelude only for interactive TTY;
  retain concise output for non-interactive line mode.
- `minicode/tui/chrome.py`, `renderer.py`, `navigation.py`: introduce
  borderless sections, responsive header, stable footer, bounded slash menu
  and viewport-aware transcript sizing.
- `minicode/tui/input.py`, `theme.py`, `ui_hints.py`: neutral composer,
  terminal-adaptive cursor, compact shortcuts and restrained palette.
- `minicode/tui/input_parser.py`, `event_flow.py`: repair Ctrl+J/paste event
  construction and exact slash-command submission.
- `minicode/tui/session_flow.py`, `minicode/cli_commands.py`: remove
  pre-screen session noise and replace boxed help with a 60-column command map.
- `tests/test_tui.py`, `tests/test_tty_app.py`: freeze narrow-header, bounded
  palette, help width, startup, Enter, Ctrl+J and paste behavior.

## Real terminal acceptance

The installed `minicode-py` entry point was
exercised from the MiniCode workspace in real PTYs at 60x24, 80x28 and
120x36. All three sizes rendered without horizontal overflow or duplicate
startup chrome.

At 60x24 the slash palette showed four visible commands and stayed within the
viewport. `/help` submitted on a single Enter and rendered without splitting
command names. A real bracketed two-line paste preserved both lines without a
crash; Ctrl+J was separately exercised as a non-submitting newline.

## Verification

- Focused TUI/session suite: 56 passed.
- Real-environment TUI/permission conversation suite: 60 passed.
- CLI/help compatibility slice after the final copy fix: 40 passed.
- Ruff, `compileall` and scoped `git diff --check`: passed.
- Complete repository suite after the initial redesign: **4032 passed, 2
  skipped** in 220.91 seconds.

The first complete run exposed one help-contract regression: the compact map
still needed to describe `/patch` as applying multiple replacements. The copy
was shortened without exceeding 60 columns; the second complete run was green.

### Post-redesign run-command incident

A subsequent real review in `Multimodal_RAG` reached a shell-backed
`run_command` and appeared to freeze. The saved session ended with the tool in
`running` state, while the file log contained only repeated cybernetic health
warnings and no tool exception. Two defects were confirmed:

- interactive startup still attached a WARNING-level stderr handler, allowing
  background health logs to corrupt the alternate-screen permission surface;
- the reusable approval event was cleared after publishing and requesting a
  render, so a fast approval could be erased and wait indefinitely.

Interactive diagnostics now remain in `~/.mini-code/minicode.log`, while
non-interactive/headless console logging is unchanged. The approval channel is
reset before `pending_approval` becomes observable. Both defects have dedicated
regression tests.

Because the real VLM RAG workspace could contain sensitive context, the live
post-fix test used an empty temporary workspace and a synthetic prompt. A real
80x30 PTY displayed the command approval surface without warning pollution;
`y` released the wait, `run_command` produced `MINICODE_APPROVAL_OK`, the model
returned `done`, and the UI returned to `Ready`. The focused permission/TUI
suite passed 72 tests. The final complete suite passed **4034 tests with two
skips** in 224.78 seconds.

## Remaining boundaries

- Product naming remains `CodeLoop` in the UI and `minicode-py` as the command,
  matching the current README decision but still a branding choice worth
  unifying separately.
- The renderer assumes a Unicode/ANSI-capable terminal, as before; a plain
  terminal fallback is not part of this redesign.
- Permission overlays intentionally remain denser than steady-state chrome
  because action scope and denial choices must stay explicit.
- Real PTY exits used normal persistence and therefore saved small test
  sessions in the user's existing MiniCode session store; none were deleted.
