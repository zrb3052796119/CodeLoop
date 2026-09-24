---
name: "minicode-learning-coach-demo"
description: "Guide a user through learning MiniCode modules with staged code reading, recall questions, answer correction, and interview preparation."
directory: "code-understanding"
domains: ["code", "file", "search", "analysis", "communication"]
scopes: ["readonly"]
purposes: ["guided codebase explanation", "architecture walkthrough"]
supported_operations: ["explain.read", "question.create"]
# Inferred routing requires at least one of these learning-goal cues.
activation_terms:
  - "teach"
  - "teaching"
  - "learn"
  - "learning"
  - "study"
  - "coach"
  - "quiz"
  - "interview"
  - "教我"
  - "教学"
  - "学习"
  - "带我学"
  - "带我走一遍"
  - "提问我"
  - "面试"
tools: ["read_file", "grep_files", "load_skill"]
keywords: ["MiniCode", "learning", "teaching", "quiz", "question", "recall", "code explanation", "architecture", "agent loop", "skill routing", "memory", "context compaction", "interview"]
examples:
  - "Teach me how MiniCode agent_loop.py works"
  - "Ask me interview questions about MiniCode Skill routing"
---

# MiniCode Learning Coach Demo

Use this workflow when the user wants to learn the MiniCode codebase in a staged,
interview-oriented way.

## Workflow

1. Identify the user's current learning stage and the module they want to study.
2. Read the smallest relevant code files before explaining behavior.
3. Explain the runtime chain with concrete file references.
4. Ask short recall questions after each section.
5. Compare the user's answer with a standard answer and correct imprecise points.
6. Record durable Q&A or implementation conclusions when the user asks for study notes.
7. Convert the module into interview-ready language.
8. Separate implemented behavior from planned or experimental behavior.

## Boundaries

- Do not claim a mechanism is implemented unless the code path has been verified.
- Do not load every Skill or every source file by default.
- Prefer `read_file` and `grep_files` for local evidence before giving explanations.
- Use `load_skill` only when an existing routed Skill is relevant to the learning flow.

## Output Shape

For each lesson, prefer:

```text
Topic
Code path
Runtime chain
Key design point
Common misunderstanding
Recall question
Interview expression
```
