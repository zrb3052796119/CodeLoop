---
name: commit-hygiene
description: Stage, review, and commit changes safely — inspect the real diff before committing and never record secrets.
directory: git
domains: [code, file, execution]
scopes: [readonly, write, destructive]
purposes: ["change review before commit", "record code changes"]
supported_operations: [review.analyze, code.update, document.create]
activation_terms: [commit, git, staging, diff, branch, 提交, 暂存, 分支]
tools: [git, read_file, grep_files, run_command]
keywords: [commit, staging, diff, git, message, branch, push]
examples:
  - "Commit these changes"
  - "Review what I'm about to commit and write a message"
---

# Commit Hygiene

Use this workflow when recording changes in version control.

1. Run `git status` first. Never commit from memory of what you changed —
   untracked files and unrelated edits hide there.
2. Read the actual staged diff before writing the message. A message that
   describes intent you did not implement is worse than no message.
3. Scan the diff for credentials, API keys, tokens, and absolute local paths
   before committing. A filename looking innocuous is not evidence its
   contents are.
4. Write the message to answer *why*, not *what* — the diff already shows
   what. State the problem being solved and any tradeoff taken.
5. Report honestly what is in the commit, including anything deliberately
   left out.
