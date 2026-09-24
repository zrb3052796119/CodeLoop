# CodeLoop 同学交付包：独立打包审计

审计日期：2026-09-18。范围为当前工作区文件和构建环境；未读取真实 `.env`、个人全局配置或用户凭据，未改生产代码，未创建 wheel/源码压缩包，未执行测试。下列验证命令是给交付执行者的建议，不代表已通过。

## 当前版本事实

- 根目录 `pyproject.toml` 声明项目 `minicode-py==0.1.0`，Python `>=3.11`，唯一基础运行依赖为 `certifi>=2025.7.14,<2027`。安装发现范围为根目录 `minicode*`，`namespaces=false`。
- canonical Runtime 是根目录 `minicode/`；`py-src/`、`ts-src/`、根 `dashboard_prototype/` 和 `minicode/web/dashboard_prototype/` 是历史/原型，不是日常使用的交付运行时。
- 当前有 38 个未跟踪 Runtime 文件，其中包含新的 intent calibration、memory authority、hybrid release 等模块。只打包 `git archive HEAD` 或只用 `git ls-files` 会漏掉实际运行所需代码。必须按当前工作区白名单快照打包，并在清单里记录每个文件的 SHA-256。
- 五个入口为 `minicode-py`、`minicode-gateway`、`minicode-cron`、`minicode-headless`、`minicode-calibrate-intent`。普通同学首选 CLI；Gateway 默认 `127.0.0.1:8080`。
- Dashboard 必须携带 `minicode/web/static/index.html`、`assets/app.js`、`assets/styles.css`、`assets/cost-format.js`。它们已经由 `pyproject.toml` 的 package-data 通配符覆盖。
- 当前 `minicode/config.py` 的 Skill 选择默认值为 `model`，并按目录预算展示/发现 Skill；`routed` 是可显式启用的模式。原 README 前半段主要介绍历史 routed 能力，交付快速开始不应据此把 routed 写成默认。
- Hybrid Memory 默认关闭；local-e5 需要额外依赖和模型文件，启用还需要 promotion evidence；Qwen embedding 还需额外远程授权。默认日常 CLI 不依赖仓库 `artifacts/`。

## 推荐白名单

### 日常使用源码包

必需：

1. `pyproject.toml`。
2. `minicode/**/*.py` 与上列 `minicode/web/static/` 资源；显式排除 `minicode/web/dashboard_prototype/`、缓存、编译文件、临时输出和符号链接。
3. `README.md`（打包元数据引用它）、`README.zh-CN.md`、`CONTRIBUTIONS.md`、`.env.example`。
4. 新交付手册、安装与验收指引、版本/文件清单和原始授权状态说明。
5. `.mini-code/skills/` 中经过检查的 `SKILL.md`、`SKILL_DIR.md` 和确有引用的附属文本。当前共有约 75 KB 普通文件，其中含 `.DS_Store` 等无用项，应用白名单只收必要文本。

技能目录是项目配置，**不进 wheel**。安装 CodeLoop 后在其他目标仓库运行时，不会自动发现安装源码目录下的 Skill。手册应提供两个任选步骤：把随包 `.mini-code/skills/` 复制到目标仓库的同路径；或检查后合并到个人 `~/.mini-code/skills/`。不要盲目覆盖同学已有 Skill。

可维护增补：

- `docs/USAGE_GUIDE.md`、`docs/subagent-model-routing.md`、`docs/intent-semantic-classifier.md`、`docs/memory-hybrid-retrieval.md`；可带完整 `docs/` 供理解架构，但需说明实验文档链接的研究 artifacts 未随日常包提供。
- 一组明确列出的聚焦 Runtime 测试，而非声称包含完整研究回归环境。建议起步：`tests/test_packaging.py`、`tests/test_config.py`、`tests/test_tools.py`、`tests/test_permissions.py`、`tests/test_agent_flow.py`、`tests/test_context_compactor.py`、`tests/test_session.py`、`tests/test_skills.py`、`tests/test_run_entrypoint_lifecycle.py`。
- 上述 pytest 使用根 `conftest.py`；该文件又导入 `tests/global_state_isolation.py`，两者必须一起带。`test_packaging.py` 的 CI 断言还依赖 `.github/workflows/ci.yml`，可随附此文件，或者明确交付测试命令只选其中安装/资源相关测试；不能悄悄删改测试断言。
- 如需要保留 `tests/test_skill_router_production_catalog.py`，必须保留 `.mini-code/skills/`，它会直接复制该目录做真实目录回归。

明确排除：`.git/`、真实 `.env`、全部个人/项目运行状态（`.mini-code/` 除已审查的 `skills/`）、`.mini-code-memory*`、会话、journal、日志、`.venv*`、模型权重、缓存、`node_modules/`、build/dist/egg-info、`.claude/`/`.codex/`/`.workbuddy/`/`.zcode/`、私人工作笔记和实验目录，以及未经单独审查的 `artifacts/`。

## 为什么不直接附全 tests/scripts 并声称可全量运行

- `scripts/check_clean_archive_package.py` 固定从 **HEAD archive** 构建；适用于有完整提交的发布仓库，不适用于无 `.git` 的交付压缩包，也不能证明当前工作区快照可安装。
- `tests/test_clean_archive_package.py` 中的主测试甚至允许 HEAD 在 `wheel_build` 或 `installed_imports` 阶段失败后返回。因此该测试绿色不等于当前交付可安装。
- `scripts/check_release_reproducibility.py` 强制查询 Git 清洁度，并读取 `artifacts/agent-quality-contract.json`；不适用于此类源码快照。
- `tests/test_portfolio_document_links.py` 遍历 README/作品集的相对链接；如果排除研究 artifacts，就会有缺少研究证据导致的失败。
- Memory phase2、production baseline、semantic gap、context compaction experiment 等测试/脚本直接读冻结 `artifacts/`。例如 `tests/test_memory_retrieval_phase2a_evaluator.py` 读 `artifacts/memory-retrieval-baseline.json`，`scripts/memory_retrieval_phase2b_evaluator.py` 读 `artifacts/memory-retrieval-phase2a.json`。
- 完整保留 `tests/`、`scripts/` 但不带 artifacts，可作为维护参考；交付说明必须给可跑的明确测试清单，并把研究/历史发布门排除出该交付范围。更清楚的日常包是只包含已验证过的聚焦测试集。
- 如要保留确定性质量门，可单独带 `scripts/evaluate_agent_quality.py`、`scripts/agent_quality_evaluator.py`、`scripts/__init__.py`、`artifacts/agent-quality-contract.json`，以及 `tests/fixtures/agent_quality/` 下的七个直接输入：`skill-routing.json`、`skill-routing-joined.json`、`compaction-fidelity.json`、`north-star-manifest.json`、`north-star-baseline-results.json`、`multi-agent-cost-manifest.json`、`multi-agent-cost-baseline-results.json`。需要在干净交付目录实际执行门禁后再宣称通过。

## 离线构建可行性

本机 `python3` 是 Python 3.12.8，但缺少 setuptools、wheel、certifi。现有 `.venv-acceptance/bin/python` 是可用构建器，已探测版本：

| 组件 | 版本 |
| --- | --- |
| Python | 3.12.8 |
| pip | 24.3.1 |
| setuptools | 84.0.0 |
| wheel | 0.48.0 |
| pytest | 9.1.1 |
| certifi | 2026.7.22 |
| ruff | 0.16.4 |
| jsonschema | 4.26.0 |

从白名单 staging 目录构建（路径用实际 stage 替换）：

```bash
/absolute/path/to/.venv-acceptance/bin/python -m pip wheel \
  --no-index --no-deps --no-build-isolation \
  --wheel-dir /absolute/path/to/output \
  /absolute/path/to/staged-source
```

不需要安装 `build` 模块。`--no-build-isolation` 利用现有构建依赖，`--no-index --no-deps` 避免联网。wheel 内只能包含 canonical Runtime 与 package-data；应再次列目录验证。

这只证明 **CodeLoop wheel 可以离线构建**，不等于接收方能离线安装全部依赖。接收方基础环境仍需满足 certifi；若要真正离线安装，需要另附合法来源的 certifi wheel，开发测试还需其他依赖 wheel。当前未确认本机缓存中有可交付 certifi wheel；`pip cache list` 因缓存目录不可写而不可用。不要把现成虚拟环境整体复制给不同操作系统的同学，也不要将本机已安装目录冒称原始发行 wheel。

## 最有价值的交付验证

按价值排序，在临时配置目录和解压后的交付目录执行，避免真实模型凭据与个人记忆参与：

1. 从实际 staging 构建 wheel，然后在源码目录之外的临时目录 `pip install --no-deps --target ...`，导入五个入口和 `minicode.agent_loop`/`minicode.memory`/Hybrid 模块；确认 `minicode.__file__` 指向安装产物，而非当前仓库。
2. 打开 wheel 检查四个 Dashboard 资源；运行 `tests/test_packaging.py::test_dashboard_assets_load_from_an_installed_wheel`。该测试还以已安装产物运行大量 Gateway/Session/Memory/权限相关 smoke。
3. 运行聚焦集，例如：

   ```bash
   python -m pytest -q tests/test_packaging.py tests/test_config.py \
     tests/test_agent_flow.py tests/test_tools.py tests/test_permissions.py
   ```

   只有将该集所需 `conftest.py`、global isolation 和 CI 文件均放入包后，才能直接按此命令在解压目录运行。
4. `python -m compileall -q minicode`；`python -m ruff check minicode/ --select=E,F --ignore=E501`。这些检查辅助发现语法/导入问题，不代替安装测试。
5. 在临时 home 中运行 `python -m minicode.main --help`、无任务的 cron dry-run，以及选用 mock 模型的临时小项目 smoke。真实 API 调用需接收方自行填 key 后单独验证；`--validate-config` 仅验证本地配置结构，不能证明远程凭据有效。

## 交付时需要说清楚的限制

- 这是当前工作区快照，版本 `0.1.0` 没有自动体现未提交代码；用交付日期和文件哈希区分，不应标为 HEAD 的可复现正式 release。
- 根目录不存在 LICENSE，README 也已说明 Python 上游许可待确认。`ts-src/LICENSE` 是该子项目的 MIT 许可，不能据此把整个 Python Runtime 标为 MIT；交付时保留原作者/上游说明，不新增未经授权的许可证。此处仅记录本地文件事实，不做法律结论。
- 默认基础包不含本地 embedding 模型、研究 promotion evidence、私人 Memory、会话历史或远程 API 密钥。
- 只读子 Agent 取消是协作式，审批机制不是操作系统沙箱；Gateway 默认仅 loopback。日常 onboarding 应先用临时练习目录和只读任务，不能宣传为生产隔离环境。
- README 的历史测试数、历史 live 实验和跨平台 CI 矩阵都不能当作本次交付验收结果。报告本次实际执行的系统、Python、命令、通过/失败数和未验证部分。
