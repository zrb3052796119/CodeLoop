---
name: readme-authoring
description: Write concise README or guide content grounded in the actual project.
directory: documentation
domains: [code, file, analysis]
scopes: [readonly, write]
purposes: ["README authoring", "README revision"]
supported_operations: [document.create, document.update]
tools: [read_file, grep_files, edit_file]
keywords: [readme, documentation, guide, usage]
examples:
  - "Update the README for this feature"
  - "Write a README usage guide for this project"
---

# README Authoring

Use this workflow for README and guide updates.

1. Inspect the real project structure and commands.
2. Prefer accurate usage examples over broad marketing text.
3. Keep setup, usage, and verification instructions concrete.
4. Avoid documenting behavior that is not implemented.
