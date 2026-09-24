# Skill 评测题作者记录

## 独立性与事前冻结

本题集由独立作者子任务 skill_cases_author 编写。唯一读取的输入为本目录的 `catalog.json` 和 `catalog-descriptions.md`；未读取生产路由代码、既有测试、既有评测题，也未运行被评测系统。没有读取任何 Skill 正文。catalog.json 包含路由相关元数据，但本次预期仅依据目录描述表达的用途与任务语义制定，没有针对关键词、示例或激活词拼题，也没有根据系统输出修改答案。

这些任务中的组件、报错和情境为合成案例，并非对当前项目实际状态的断言。题目冻结发生在作者运行任何路由/加载评测之前。后续若发现题目缺陷，应记录原始分数和变更理由，不能覆盖本冻结版本后声称是事前预期。

- 冻结时间（UTC）：2026-09-07T14:43:53.702068+00:00
- cases.json SHA-256：`c5930a29a4b8289600df4f48eab3caeb8c4a0f15e0c76d9c0b6ed4e153fcb2cc`
- catalog.json SHA-256：`48cc132ac292c40fd4b5a0d18797c4bfdcd0bd7d77307e82155e3364382b4238`
- catalog-descriptions.md SHA-256：`5b5b3265916ac6cb06baa66f340f94e7e074f4c32d908dcb6c0bad4842c5b069`

## 构成

- 总计 20 题，编号 S01–S20。
- 普通自然触发 13 题：S01–S13。
- 显式 qualified 技能请求 2 题：S14、S15。
- 多步骤/竞争 2 题：S16、S17。
- 负例 3 题：S18 只引用名称为文案、S19 明确禁止全部 Skill、S20 普通闲聊。
- 英文 3 题：S03、S07、S13；其余以自然中文表达为主。
- S08 使用近期对话与当前任务上下文，检查后续省略表达能否承接并发诊断意图。
- required_skill_groups 涉及 16 个不同 qualified Skill（包括 S01 的合理二选一）；其余正例保证覆盖至少 15 个不同的必要 Skill。

## 预期解释与计分边界

1. 正例 `expected_outcomes` 为 `route`。负例可接受 `no_skill` 或 `abstain`，但不能实际加载技能；本题集没有必须澄清的题。
2. `required_skill_groups` 组间为 AND、组内为 OR。S01 的代码链路讲解可由通用代码解释或 MiniCode 学习承担；S16、S17 分别需要两类步骤得到覆盖。
3. `allowed_skills` 包含必要 Skill 及事前认可的辅助 Skill；允许辅助项不代表它能替代必需组。没有把目录里全部技能都列入可接受集合。`forbidden_skills` 是本轮目录中其余技能，避免任意候选堆积被当成正确。
4. 三道负例的 required_skill_groups 和 allowed_skills 均为空，所有目录 Skill 均被禁止。
5. 检索命中与模型自发加载应分别报告。仅候选出现在列表、脚本直接调用 load_skill、或强制塞入 Skill 正文，都不能证明模型自发加载成功。多步骤题应在可观察的完整加载窗口中检查必要组，不能仅因第一步尚未进入后续步骤就声称完整任务成功。
6. 作者没有指定内部 prompt mode，避免在不了解实现时把内部表示当成语义要求。

## 必要 Skill 范围

- `code-skills/minicode-study`
- `code-understanding/codebase-explanation`
- `code-understanding/design-review`
- `code-understanding/minicode-learning-coach-demo`
- `debugging/concurrency-debugging`
- `debugging/pytest-debugging`
- `dependencies/dependency-hygiene`
- `documentation/readme-authoring`
- `git/commit-hygiene`
- `performance/hotpath-profiling`
- `quality/lint-and-static-cleanup`
- `refactoring/safe-refactor`
- `refactoring/structural-rename`
- `security/secret-and-injection-review`
- `testing/regression-from-bug-report`
- `testing/test-driven-development`
