/* Waku-inspired MiniCode dashboard prototype. Read-only mock data. */

const DATA = {
  workspace: '/Users/zhourunbo/code/coding agent/MiniCode-Python-main',
  summary: {
    cost: 0.8426,
    avgTurn: 1840,
    runs: 5,
    toolCalls: 24,
    memories: 5,
    skills: 8,
    tokensIn: 184320,
    tokensOut: 14820,
    errors: 1,
    context: 63,
    contextLimit: 85,
  },
  runs: [
    { id: 'run-7F3A', title: '实现 memory reranker 的回归测试', status: 'running', source: 'web', session: 'f4517bd1', started: '14:32', elapsed: '01:42', steps: 8, tokens: 18432, cost: 0.1268, tools: 6, errors: 1, model: 'claude-sonnet-4', progress: 78 },
    { id: 'run-7F39', title: '检查 cost tracker 接线', status: 'complete', source: 'tui', session: '4830a9a9', started: '14:16', elapsed: '00:58', steps: 5, tokens: 10284, cost: 0.0741, tools: 4, errors: 0, model: 'claude-sonnet-4', progress: 100 },
    { id: 'run-7F38', title: '分析 Agent loop 控制器', status: 'complete', source: 'gateway', session: 'aa32c2c1', started: '13:44', elapsed: '02:14', steps: 11, tokens: 24118, cost: 0.1835, tools: 9, errors: 2, model: 'gpt-4o-mini', progress: 100 },
    { id: 'run-7F37', title: '同步 MCP capability registry', status: 'waiting', source: 'headless', session: '204ed28d', started: 'queued', elapsed: '—', steps: 0, tokens: 0, cost: 0, tools: 0, errors: 0, model: 'claude-sonnet-4', progress: 0 },
    { id: 'run-7F36', title: '修复 session delta 序列化', status: 'failed', source: 'tui', session: '55fa239d', started: '12:08', elapsed: '01:08', steps: 6, tokens: 12806, cost: 0.0964, tools: 5, errors: 3, model: 'claude-sonnet-4', progress: 64 },
  ],
  runSteps: [
    ['01', '解析意图', 'coding · confidence 0.96', '42ms', 'complete'],
    ['02', '技能路由', 'tdd · daily-coding', '18ms', 'complete'],
    ['03', '记忆检索', '5 candidates · 3 selected · 2 rendered', '71ms', 'complete'],
    ['04', '检查工作区', '14 files · 3 anchors', '1.2s', 'complete'],
    ['05', '实现修改', 'memory_reranker.py', '24.7s', 'complete'],
    ['06', '最小验证', '1 failed · 12 passed', '8.4s', 'failed'],
    ['07', '自愈重试', 'isolated fixture', '3.1s', 'complete'],
    ['08', '应用补丁', '+22 −3', 'running', 'running'],
  ],
  sessions: [
    { id: 'f4517bd1', title: 'memory reranker 回归测试', source: 'web', age: '现在', messages: 18, cost: 0.1268, status: 'active', last: 'edit_file · +22 −3' },
    { id: '4830a9a9', title: 'cost tracker 接线检查', source: 'tui', age: '12 分钟前', messages: 12, cost: 0.0741, status: 'saved', last: 'verification passed' },
    { id: 'aa32c2c1', title: 'Agent loop 控制器分析', source: 'gateway', age: '48 分钟前', messages: 27, cost: 0.1835, status: 'saved', last: 'report generated' },
    { id: '204ed28d', title: 'MCP 资源发现失败', source: 'tui', age: '昨天', messages: 9, cost: 0.0422, status: 'saved', last: 'connector disabled' },
    { id: '55fa239d', title: '整理 session delta', source: 'headless', age: '昨天', messages: 14, cost: 0.0964, status: 'saved', last: 'retry exhausted' },
    { id: '91d03b4e', title: '生成 capability 清单', source: 'gateway', age: '3 天前', messages: 22, cost: 0.1184, status: 'archived', last: 'exported JSON' },
  ],
  memories: [
    { id: 'mem-project-18', scope: 'project', category: 'convention', tier: 'long_term', title: '修改后必须完成最小验证', source: '.mini-code-memory/MEMORY.md', score: 0.96, tokens: 312, selected: true, status: 'rendered', age: '2 天前', detail: '涉及 Python 与前端脚本时，至少运行 py_compile、node --check，并检查本地页面健康状态。', reason: 'phrase_match · file_match · project scope · within render budget', approval: 'approved', safety: 'safe' },
    { id: 'mem-user-07', scope: 'user', category: 'preference', tier: 'long_term', title: '优先复用现有模块', source: '~/.mini-code/memory/MEMORY.md', score: 0.89, tokens: 184, selected: true, status: 'rendered', age: '12 天前', detail: '先沿用 MiniCode 当前结构与标准库能力，确有必要时再引入新的依赖或运行服务。', reason: 'user_context_object_match · approved · within render budget', approval: 'approved', safety: 'safe' },
    { id: 'mem-local-24', scope: 'local', category: 'testing', tier: 'short_term', title: 'Reranker 阈值实验记录', source: '.mini-code-memory-local/memory.json', score: 0.74, tokens: 240, selected: true, status: 'suppressed', age: '3 周前', detail: '候选阈值从 0.72 下调到 0.68 后，召回率提升，但重复结果增加。', reason: 'selected after gate · skipped by total-token render budget', approval: 'approved', safety: 'safe' },
    { id: 'mem-project-33', scope: 'project', category: 'architecture', tier: 'short_term', title: 'MemoryPipeline 的外部接口', source: 'reflection', score: 0.68, tokens: 266, selected: false, status: 'suppressed', age: '5 天前', detail: '所有记忆操作通过 read、inject、write、maintain 四个方法进入统一管线。', reason: 'relevance gate rejected · optional reranker is not a default stage', approval: 'approved', safety: 'safe' },
    { id: 'mem-local-11', scope: 'local', category: 'failure-recovery', tier: 'short_term', title: '失败后先隔离 fixture', source: 'reflection', score: 0.61, tokens: 204, selected: false, status: 'suppressed', age: '1 个月前', detail: '共享 fixture 失败时，先用最小 fixture 验证因果，再扩大到完整套件。', reason: 'candidate consolidation · duplicate guidance suppressed', approval: 'approved', safety: 'safe' },
  ],
  memorySnapshot: {
    candidates: 5,
    selected: 3,
    rendered: 2,
    suppressed: 3,
    totalTokens: 496,
    controller: { mode: 'standard', maxMemories: 5, minRelevance: 0.30, maxTokensPerMemory: 200, contextUsage: 0.63, reason: 'standard memory injection' },
    pipeline: { reads: 42, writes: 9, maintains: 3, reranker: 'disabled', vector: 'disabled' },
    workingMemory: { entries: 3, maxEntries: 15, tokens: 812, maxTokens: 4000 },
  },
  skills: [
    { name: 'tdd', source: 'project', score: 0.92, uses: 8, state: 'active', description: '以红—绿—重构节奏实现功能并保留回归保护。' },
    { name: 'daily-coding', source: 'user', score: 0.88, uses: 31, state: 'loaded', description: '日常代码修改的最小质量与安全检查。' },
    { name: 'bug-detective', source: 'user', score: 0.71, uses: 12, state: 'loaded', description: '从症状、证据和最小复现中定位根因。' },
    { name: 'verification-loop', source: 'user', score: 0.66, uses: 15, state: 'loaded', description: '按风险分层运行语法、测试、构建与质量验证。' },
    { name: 'codebase-design', source: 'compat', score: 0.44, uses: 5, state: 'loaded', description: '设计更深的模块边界与更小的公共接口。' },
    { name: 'frontend-design', source: 'user', score: 0.31, uses: 3, state: 'idle', description: '构建具有明确视觉语言的高质量前端界面。' },
    { name: 'planning-with-files', source: 'user', score: 0.28, uses: 19, state: 'loaded', description: '用持久化计划与笔记管理复杂任务。' },
    { name: 'code-review-excellence', source: 'user', score: 0.16, uses: 7, state: 'idle', description: '按风险和证据审查代码差异。' },
  ],
  gateways: [
    { name: 'Web', status: 'connected', sessions: 3, latency: '18ms', detail: '127.0.0.1:8765' },
    { name: 'TUI', status: 'connected', sessions: 8, latency: 'local', detail: 'interactive terminal' },
    { name: 'Gateway', status: 'connected', sessions: 4, latency: '42ms', detail: 'agent protocol' },
    { name: 'Headless', status: 'idle', sessions: 2, latency: 'queue', detail: 'cron / automation' },
  ],
  connectors: [
    { name: 'filesystem', protocol: 'stdio', scope: 'project', tools: 14, resources: 3, status: 'connected', latency: '12ms', note: '项目文件与资源访问' },
    { name: 'github', protocol: 'stdio', scope: 'user', tools: 9, resources: 0, status: 'connected', latency: '84ms', note: '仓库、Issue 与 Pull Request' },
    { name: 'playwright', protocol: 'stdio', scope: 'project', tools: 7, resources: 0, status: 'connected', latency: '31ms', note: '本地页面自动化与视觉验证' },
    { name: 'knowledge', protocol: 'http', scope: 'user', tools: 5, resources: 18, status: 'degraded', latency: '420ms', note: '检索响应偏慢，仍可使用' },
    { name: 'slack', protocol: 'stdio', scope: 'user', tools: 0, resources: 0, status: 'disabled', latency: '—', note: '未启用' },
  ],
  events: [
    ['turn_start', '实现 memory reranker 的回归测试', '14:32:18'],
    ['skill_route', 'tdd · score 0.92', '14:32:19'],
    ['memory', '5 candidates · 3 selected · 2 rendered', '14:32:19'],
    ['model', '8,420 in · 612 out · $0.0344', '14:32:20'],
    ['tool', 'grep_files · 14 matches', '14:32:21'],
    ['tool', 'read_file · memory_reranker.py', '14:32:24'],
    ['verify', 'pytest · 1 failed, 12 passed', '14:32:31'],
    ['recovery', 'isolated fixture retry', '14:32:34'],
    ['tool', 'edit_file · +22 −3', '14:32:39'],
  ],
};

const TITLES = {
  overview: '概览', runs: '运行', sessions: '会话', memory: '记忆',
  skills: '技能', connections: '连接', ops: 'LLM 运维', system: '系统',
};

const VIEW_IDS = new Set(Object.keys(TITLES));
const state = {
  selectedRun: DATA.runs[0].id,
  currentSession: DATA.sessions[0].id,
  lastRefresh: Date.now(),
  messages: [
    { role: 'assistant', text: '这是只读页面原型。你可以在这里体验会话栏，但消息不会发送给真实 Agent。' },
  ],
};

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const money = (value) => `$${Number(value).toFixed(value < 0.01 ? 4 : 2)}`;
const tokens = (value) => Number(value).toLocaleString('zh-CN');
const statusText = { running: '运行中', complete: '完成', waiting: '等待', failed: '失败', active: '活跃', saved: '已保存', archived: '归档', connected: '已连接', degraded: '降级', disabled: '未启用', idle: '空闲', loaded: '已加载', injected: '已注入', rendered: '已渲染', selected: '已选择', suppressed: '已抑制', pending: '待审批', approved: '已批准', safe: '安全' };
const MEMORY_SCOPES = {
  user: { label: 'User', path: '~/.mini-code/memory/', description: '跨项目持久化；保存用户偏好与通用约定。', sharing: 'cross-project' },
  project: { label: 'Project', path: '.mini-code-memory/', description: '项目共享；可随仓库版本化的架构与约定。', sharing: 'shared / versionable' },
  local: { label: 'Local', path: '.mini-code-memory-local/', description: '当前项目私有；不提交到版本控制的本地决定。', sharing: 'project-local' },
};
const MEMORY_TIERS = {
  working: { label: 'Working', description: '当前会话的完整细节与快速访问。' },
  short_term: { label: 'Short-term', description: '近期记忆，保留完整内容。' },
  long_term: { label: 'Long-term', description: '高复用记忆，由使用次数晋升。' },
  archival: { label: 'Archival', description: '低频永久记录；压缩且默认不参与注入。' },
};

function statusPill(status) {
  return `<span class="pill ${esc(status)}">${esc(statusText[status] || status)}</span>`;
}

function sourceTag(source) {
  return `<span class="source-tag ${esc(source)}">${esc(source)}</span>`;
}

function table(headings, rows) {
  if (!rows.length) return '<div class="card empty">暂无数据</div>';
  return `<div class="table-wrap"><table><thead><tr>${headings.map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;
}

function subtabBar(view, tabs, active) {
  return `<div class="subtabs">${tabs.map(([key, label, count]) => `<a class="subtab ${key === active ? 'on' : ''}" href="#${view}/${key}">${esc(label)}${count == null ? '' : `<span>${count}</span>`}</a>`).join('')}</div>`;
}

function metricTiles(items) {
  return `<div class="tiles">${items.map(([value, label, tone = '']) => `<div class="tile"><b class="${tone}">${value}</b><span>${esc(label)}</span></div>`).join('')}</div>`;
}

function pressureBar() {
  const s = DATA.summary;
  const headroom = s.contextLimit - s.context;
  return `<div class="pressure-bar" aria-label="上下文压力 ${s.context}%">
      <div class="used" style="width:${s.context}%">${s.context}% 已用</div>
      <div class="headroom" style="width:${headroom}%">${headroom}% 余量</div>
      <div class="limit" style="width:${100 - s.contextLimit}%"></div>
    </div>
    <div class="meta">当前仍在安全区；达到 ${s.contextLimit}% 时触发压缩控制。</div>`;
}

function runtimeMap() {
  const box = (title, detail, hash = '') => `<${hash ? 'a' : 'div'} class="flow-box" ${hash ? `href="#${hash}"` : ''}><b>${esc(title)}</b><span>${esc(detail)}</span></${hash ? 'a' : 'div'}>`;
  return `<div class="runtime-map">
    <div class="lane"><span class="lane-label">request</span>${box('Gateway', 'web · tui · api', 'connections/gateways')}<i>→</i>${box('Session', 'f4517bd1', 'sessions')}<i>→</i>${box('Agent Loop', 'step 08', 'runs')}</div>
    <div class="lane"><span class="lane-label">reason</span>${box('Context', '63% · 18.4k tok')}<i>→</i>${box('Model', 'sonnet-4')}<i>→</i>${box('Tools', '6 calls · 1 error', 'connections')}</div>
    <div class="lane"><span class="lane-label">recall</span>${box('Memory', '5 candidates · 2 rendered', 'memory')}<i>→</i>${box('Reply', 'streaming')}<i>→</i>${box('Trace', 'always on', 'ops')}</div>
  </div>`;
}

function runCard(run, expanded = false) {
  return `<article class="run-card ${expanded ? 'selected' : ''}">
    <button class="run-summary" onclick="selectRun('${esc(run.id)}')">
      <span class="run-main"><b>${esc(run.title)}</b><small><code>${esc(run.id)}</code> · ${sourceTag(run.source)} · ${esc(run.model)}</small></span>
      <span class="run-numbers"><span>${run.steps} steps</span><span>${tokens(run.tokens)} tok</span><span>${money(run.cost)}</span>${statusPill(run.status)}</span>
    </button>
    ${expanded ? `<div class="run-expanded">
      <div class="progress"><i style="width:${run.progress}%"></i></div>
      ${table(['步骤', '阶段', '结果', '耗时'], DATA.runSteps.map(([step, label, detail, duration, status]) => `<tr><td class="mono">${step}</td><td>${esc(label)}</td><td>${esc(detail)} ${status === 'failed' ? statusPill('failed') : ''}</td><td class="meta">${esc(duration)}</td></tr>`))}
    </div>` : ''}
  </article>`;
}

function memoryRows(items) {
  return items.map((memory) => `<div class="memory-row">
    <div class="memory-score"><b>${memory.score.toFixed(2)}</b><span>${memory.tokens} tok</span></div>
    <div class="memory-copy"><small>${esc(memory.scope.toUpperCase())} · ${esc(memory.category)} · ${esc(MEMORY_TIERS[memory.tier].label)}${memory.selected ? ' · selected' : ''}</small><b>${esc(memory.title)}</b><p>${esc(memory.detail)}</p>
      <details><summary>查看检索诊断</summary><div>${esc(memory.reason)}<br>${esc(memory.source)} · ${esc(memory.approval)} · ${esc(memory.safety)} · ${esc(memory.age)}</div></details>
    </div>
    ${statusPill(memory.status)}
  </div>`).join('') || '<div class="card empty">暂无数据</div>';
}

function memoryScopeCards() {
  return `<div class="pillar-grid">${Object.entries(MEMORY_SCOPES).map(([scope, meta]) => {
    const count = DATA.memories.filter((memory) => memory.scope === scope).length;
    return `<a href="#memory/scopes" class="flow-box scope-card"><b>${esc(meta.label)} · ${count}</b><span>${esc(meta.description)}</span><code>${esc(meta.path)}</code><small>${esc(meta.sharing)}</small></a>`;
  }).join('')}</div>`;
}

function memoryFunnel() {
  const snapshot = DATA.memorySnapshot;
  const stages = [
    ['Candidates', snapshot.candidates, 'active entries ranked'],
    ['Selected', snapshot.selected, 'gate + consolidation passed'],
    ['Rendered', snapshot.rendered, `${snapshot.totalTokens} tokens in prompt`],
    ['Suppressed', snapshot.suppressed, 'gate / duplicate / budget'],
  ];
  return `<div class="memory-funnel">${stages.map(([label, value, detail], index) => `<div class="funnel-stage ${label.toLowerCase()}"><small>${esc(label)}</small><b>${value}</b><span>${esc(detail)}</span></div>${index < stages.length - 1 ? '<i>→</i>' : ''}`).join('')}</div>`;
}

function memoryPipelineCards() {
  const methods = [
    ['read', '任务 + 文件', '作用域检索 → 确定性门控 → 候选合并'],
    ['inject', '消息 + 上下文压力', '控制注入模式与 token 预算，写入 system message'],
    ['write', '任务 + 执行 trace', 'Reflection + value/safety gate → Project / Short-term'],
    ['maintain', '后台周期', '合并、验证、晋升/归档、关联记忆'],
  ];
  return `<div class="memory-methods">${methods.map(([name, input, behavior]) => `<div class="method-card"><code>${esc(name)}()</code><small>${esc(input)}</small><p>${esc(behavior)}</p></div>`).join('')}</div>`;
}

function memoryTierCards() {
  return `<div class="tier-grid">${Object.entries(MEMORY_TIERS).map(([tier, meta]) => {
    const count = DATA.memories.filter((memory) => memory.tier === tier).length;
    return `<div class="tier-card"><small>${esc(tier)}</small><b>${count}</b><strong>${esc(meta.label)}</strong><p>${esc(meta.description)}</p></div>`;
  }).join('')}</div>`;
}

const VIEWS = {
  overview() {
    const s = DATA.summary;
    return `${metricTiles([
      [money(s.cost), '今日花费', 'money'], [`${(s.avgTurn / 1000).toFixed(1)}s`, '平均回合'], [s.runs, '运行'], [s.toolCalls, '工具调用'], [s.memories, '记忆'], [s.skills, '已发现技能'],
    ])}
    <h2>上下文压力 — 当前控制信号</h2>
    ${pressureBar()}
    <h2>运行路径 — 点击进入对应分区</h2>
    ${runtimeMap()}
    <h2>当前运行</h2>
    ${runCard(DATA.runs[0], true)}`;
  },

  runs(_, sub = 'all') {
    const tabs = [['all', '全部', DATA.runs.length], ['running', '运行中', 1], ['complete', '已完成', 2], ['failed', '失败', 1]];
    const items = sub === 'all' ? DATA.runs : DATA.runs.filter((run) => run.status === sub);
    return `${subtabBar('runs', tabs, sub)}
      <div class="intro">每次任务只占一行；选择一条运行后，执行路径在原位展开，不再复制到右侧 Inspector。</div>
      <div class="stack">${items.map((run) => runCard(run, run.id === state.selectedRun)).join('')}</div>`;
  },

  sessions() {
    return `<div class="intro">来自 Web、TUI、Gateway 与 Headless 的统一收件箱。选择会话后在右侧继续。</div>
      <div class="stack">${DATA.sessions.map((session) => `<button class="session-row ${session.id === state.currentSession ? 'selected' : ''}" onclick="openSession('${esc(session.id)}')">
        <span><b>${esc(session.title)}</b><small>${sourceTag(session.source)} ${esc(session.last)}</small></span>
        <span><b>${session.messages} msg</b><small>${money(session.cost)} · ${esc(session.age)}</small></span>
      </button>`).join('')}</div>`;
  },

  memory(_, sub = 'overview') {
    const snapshot = DATA.memorySnapshot;
    const tabs = [
      ['overview', '概览'],
      ['scopes', '作用域', DATA.memories.length],
      ['retrieval', '检索', snapshot.candidates],
      ['injection', '注入', snapshot.rendered],
      ['lifecycle', '生命周期'],
    ];
    let body = '';
    if (sub === 'scopes') {
      body = `<div class="intro">Scope 决定持久化位置与共享范围；category 只是每条记忆的业务标签。</div>${Object.entries(MEMORY_SCOPES).map(([scope, meta]) => {
        const items = DATA.memories.filter((memory) => memory.scope === scope);
        return `<section class="scope-section"><h2>${esc(meta.label)} scope · ${items.length}</h2><div class="scope-path"><code>${esc(meta.path)}</code><span>${esc(meta.description)}</span><small>${esc(meta.sharing)} · memory.json + MEMORY.md</small></div><div class="memory-list">${memoryRows(items)}</div></section>`;
      }).join('')}`;
    } else if (sub === 'retrieval') {
      body = `<div class="intro"><code>CanonicalMemoryRetriever</code> 先读取三个 scope 的 active entries，再依次完成确定性相关性门控、去重、候选合并与渲染预算控制。</div>
        ${memoryFunnel()}
        <div class="meta memory-caption">Suppressed 是跨阶段结果：可能发生在相关性门控、重复合并或最终 token 预算阶段，因此不与 Selected 简单相加。</div>
        <h2>候选与诊断</h2>
        <div class="memory-list">${memoryRows(DATA.memories)}</div>
        <h2>检索实现状态</h2>
        ${table(['组件', '状态', '职责'], [
          ['Canonical retriever', 'active', '统一检索入口与结果快照'],
          ['Deterministic gate', 'active', '按任务、文件、短语和 scope 过滤'],
          ['Candidate consolidator', 'active', '去重并合并相近候选'],
          ['LLM reranker', snapshot.pipeline.reranker, '可选实现；当前不在默认主路径'],
          ['Vector store', snapshot.pipeline.vector, '可选实现；当前不在默认主路径'],
        ].map(([component, status, detail]) => `<tr><td><code>${esc(component)}</code></td><td>${statusPill(status)}</td><td class="meta">${esc(detail)}</td></tr>`))}`;
    } else if (sub === 'injection') {
      const controller = snapshot.controller;
      body = `${metricTiles([
        [controller.mode, '注入模式'],
        [controller.maxMemories, '最大条目'],
        [controller.maxTokensPerMemory, '单条 Token 上限'],
        [snapshot.totalTokens, '本轮渲染 Token'],
      ])}
        <h2>控制器决策</h2>
        <div class="card accent-card controller-card"><b><code>MemoryInjectionController</code> · ${esc(controller.mode)}</b><p>上下文占用 ${Math.round(controller.contextUsage * 100)}%，最低相关度 ${controller.minRelevance.toFixed(2)}；当前采用 ${esc(controller.reason)}。</p><small>可选模式：none / summary / standard / strong</small></div>
        <h2>实际写入 system message</h2>
        <div class="memory-list">${memoryRows(DATA.memories.filter((memory) => memory.status === 'rendered'))}</div>
        <div class="meta memory-caption">只有 rendered IDs 会增加 injection_count，并在任务结束后接收本轮反馈。</div>`;
    } else if (sub === 'lifecycle') {
      const working = snapshot.workingMemory;
      body = `<div class="intro">Tier 是每条持久记忆在任意 scope 内部的生命周期状态，不是另一套存储目录。</div>
        ${memoryTierCards()}
        <h2>Working memory protection — 独立模块</h2>
        <div class="card working-card"><div><b><code>WorkingMemoryTracker</code></b><span>${working.entries} / ${working.maxEntries} entries</span></div><p>在上下文压缩时保护当前任务连续性；它不属于 User / Project / Local 三个持久化 scope。</p><div class="working-meter"><i style="width:${Math.round((working.tokens / working.maxTokens) * 100)}%"></i></div><small>${tokens(working.tokens)} / ${tokens(working.maxTokens)} tokens</small></div>
        <h2>写入与维护</h2>
        ${memoryPipelineCards()}
        <h2>安全与审批</h2>
        ${table(['关卡', '当前策略', '结果'], [
          ['Reflection value gate', '写入前评估复用价值', '低价值 reflection 不落盘'],
          ['Safety gate', '检查敏感与危险内容', '不安全内容被拒绝或隔离'],
          ['Approval state', '按来源记录审批状态', '只有合规条目进入后续检索'],
        ].map(([gate, policy, result]) => `<tr><td><code>${esc(gate)}</code></td><td>${esc(policy)}</td><td class="meta">${esc(result)}</td></tr>`))}`;
    } else {
      body = `<div class="card accent-card"><b>MiniCode 按作用域组织持久记忆。</b><p>User / Project / Local 决定记忆存在哪里、被谁共享；category 负责分类，tier 负责生命周期。</p></div>
        <h2>三个持久化作用域</h2>
        ${memoryScopeCards()}
        <h2>本轮 canonical retrieval</h2>
        ${memoryFunnel()}
        <div class="meta memory-caption">5 条候选中选择 3 条，最终向 system message 渲染 2 条，共 ${snapshot.totalTokens} tokens。</div>
        <h2>MemoryPipeline — 对外四个方法</h2>
        ${memoryPipelineCards()}`;
    }
    return subtabBar('memory', tabs, sub) + body;
  },

  skills(_, sub = 'available') {
    const tabs = [['available', '可用', DATA.skills.length], ['routing', '本轮路由']];
    if (sub === 'routing') {
      return subtabBar('skills', tabs, sub) + table(['技能', '匹配分', '状态', '依据'], DATA.skills.slice(0, 5).map((skill) => `<tr><td><code>${esc(skill.name)}</code></td><td class="mono">${skill.score.toFixed(2)}</td><td>${statusPill(skill.state)}</td><td class="meta">${esc(skill.description)}</td></tr>`));
    }
    const groups = ['project', 'user', 'compat'];
    return subtabBar('skills', tabs, sub) + groups.map((source) => `<h2>${source}</h2>${DATA.skills.filter((skill) => skill.source === source).map((skill) => `<div class="tool-card"><div><code>${esc(skill.name)}</code>${skill.state === 'active' ? statusPill('active') : ''}</div><p>${esc(skill.description)}</p><small>score ${skill.score.toFixed(2)} · ${skill.uses} uses</small></div>`).join('')}`).join('');
  },

  connections(_, sub = 'gateways') {
    const tabs = [['gateways', 'Gateway', DATA.gateways.length], ['mcp', 'MCP', DATA.connectors.length]];
    if (sub === 'mcp') {
      return subtabBar('connections', tabs, sub) + `<div class="intro">外部能力按服务器归组；延迟与状态保持在一行，具体工具数作为次级信息。</div>${DATA.connectors.map((item) => `<div class="tool-card"><div><code>${esc(item.name)}</code>${statusPill(item.status)}<span class="meta right">${esc(item.latency)}</span></div><p>${esc(item.note)}</p><small>${esc(item.protocol)} · ${esc(item.scope)} · ${item.tools} tools · ${item.resources} resources</small></div>`).join('')}`;
    }
    return subtabBar('connections', tabs, sub) + `<div class="pillar-grid">${DATA.gateways.map((gateway) => `<div class="flow-box"><b>${esc(gateway.name)} ${statusPill(gateway.status)}</b><span>${gateway.sessions} sessions · ${esc(gateway.latency)}</span><small>${esc(gateway.detail)}</small></div>`).join('')}</div>`;
  },

  ops() {
    const s = DATA.summary;
    return `${metricTiles([[money(s.cost), '今日花费', 'money'], [tokens(s.tokensIn), 'Tokens in'], [tokens(s.tokensOut), 'Tokens out'], [s.toolCalls, '工具调用'], [`${(s.avgTurn / 1000).toFixed(1)}s`, '平均回合'], [s.errors, '错误']])}
      <h2>花费 — 按运行</h2>
      ${table(['运行', '模型', 'Tokens', '花费'], DATA.runs.map((run) => `<tr><td><code>${esc(run.id)}</code></td><td>${esc(run.model)}</td><td class="meta">${tokens(run.tokens)}</td><td class="meta">${money(run.cost)}</td></tr>`))}
      <h2>控制信号</h2>
      ${table(['控制器', '当前值', '状态', '动作'], [
        ['Context PID', '63 / 85%', 'stable', 'no compaction'], ['Cost control', '$0.18 / min', 'stable', 'observe'], ['Progress', '8 / 12', 'healthy', 'continue'], ['Memory gate', '2 injected', 'quiet', 'trim'], ['Recovery', '1 recovered', 'engaged', 'retry'],
      ].map(([name, value, status, action]) => `<tr><td>${esc(name)}</td><td class="mono">${esc(value)}</td><td>${esc(status)}</td><td class="meta">${esc(action)}</td></tr>`))}
      <h2>Trace — 最近事件</h2>
      ${table(['事件', '详情', '时间'], DATA.events.slice().reverse().map(([type, detail, time]) => `<tr><td><code>${esc(type)}</code></td><td>${esc(detail)}</td><td class="meta">${esc(time)}</td></tr>`))}`;
  },

  system() {
    return `<div class="card accent-card"><b>当前运行时</b><p>Anthropic · <code>claude-sonnet-4-20250514</code> · fallback <code>gpt-4o-mini</code></p></div>
      <h2>健康状态</h2>
      ${table(['模块', '状态', '说明'], [
        ['Agent loop', 'healthy', 'step 08 · running'], ['Session store', 'healthy', '6 sessions'], ['Memory pipeline', 'healthy', 'last retrieval 71ms'], ['MCP registry', 'degraded', 'knowledge connector 420ms'], ['Cost ledger', 'healthy', 'updated this turn'],
      ].map(([name, status, note]) => `<tr><td>${esc(name)}</td><td>${statusPill(status === 'healthy' ? 'connected' : 'degraded')}</td><td class="meta">${esc(note)}</td></tr>`))}
      <h2>页面边界</h2>
      <div class="card"><p>此版只定义 UI 规范与数据形状。真实接入时，页面通过只读 snapshot API 获取状态，不直接调用 Agent Loop。</p><div class="meta"><code>GET /api/dashboard/snapshot</code> · localhost only · secrets redacted</div></div>`;
  },
};

function currentRoute() {
  const [viewRaw, subRaw] = (location.hash || '#overview').slice(1).split('/');
  return [VIEW_IDS.has(viewRaw) ? viewRaw : 'overview', subRaw || null];
}

function render() {
  const [view, sub] = currentRoute();
  document.querySelectorAll('#nav a[data-view]').forEach((link) => link.classList.toggle('on', link.dataset.view === view));
  document.querySelector('#title').textContent = TITLES[view];
  document.querySelector('#view').innerHTML = VIEWS[view](DATA, sub);
  tickMeta();
}

function tickMeta() {
  const seconds = Math.round((Date.now() - state.lastRefresh) / 1000);
  document.querySelector('#page-meta').innerHTML = `<span class="live"><i></i>live</span> · updated ${seconds}s ago · ${esc(DATA.workspace)}`;
}

function selectRun(id) {
  state.selectedRun = id;
  render();
}

function openSession(id) {
  const session = DATA.sessions.find((item) => item.id === id);
  if (!session) return;
  state.currentSession = id;
  state.messages = [
    { role: 'assistant', text: `已打开「${session.title}」。这里是原型会话，不会写入真实 Session。` },
  ];
  document.body.classList.remove('dock-closed');
  document.querySelector('#dock-status').textContent = `${session.id} · ${session.source}`;
  renderChat();
  renderSessionMenu();
  if (currentRoute()[0] === 'sessions') render();
}

function renderChat() {
  const log = document.querySelector('#chat-log');
  log.innerHTML = state.messages.map((message) => `<div class="chat-message ${message.role}"><small>${message.role === 'user' ? 'YOU' : 'MINICODE'}</small><div>${esc(message.text)}</div></div>`).join('');
  log.scrollTop = log.scrollHeight;
}

function renderSessionMenu() {
  document.querySelector('#session-menu').innerHTML = DATA.sessions.map((session) => `<button class="${session.id === state.currentSession ? 'on' : ''}" onclick="openSession('${esc(session.id)}')"><b>${esc(session.title)}</b><small>${esc(session.age)} · ${session.messages} msg</small></button>`).join('');
}

function showToast(message) {
  const toast = document.querySelector('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('show'), 1800);
}

function wireResize(id, variable, storageKey, fromRight, min, max) {
  const handle = document.querySelector(`#${id}`);
  handle.addEventListener('mousedown', (event) => {
    event.preventDefault();
    document.body.classList.add('resizing');
    const move = (moveEvent) => {
      const raw = fromRight ? window.innerWidth - moveEvent.clientX : moveEvent.clientX;
      const value = Math.max(min, Math.min(max, raw));
      document.documentElement.style.setProperty(variable, `${value}px`);
      localStorage.setItem(storageKey, value);
    };
    const up = () => {
      document.body.classList.remove('resizing');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
}

function wireShell() {
  const navWidth = localStorage.getItem('miniNavW');
  const dockWidth = localStorage.getItem('miniDockW');
  if (navWidth) document.documentElement.style.setProperty('--nav-w', `${navWidth}px`);
  if (dockWidth) document.documentElement.style.setProperty('--dock-w', `${dockWidth}px`);
  if (window.innerWidth <= 1100) document.body.classList.add('dock-closed');
  if (window.innerWidth <= 640) document.body.classList.add('nav-hidden');
  wireResize('nav-resizer', '--nav-w', 'miniNavW', false, 160, 320);
  wireResize('dock-resizer', '--dock-w', 'miniDockW', true, 280, 620);

  document.querySelector('#nav-toggle').addEventListener('click', () => document.body.classList.add('nav-hidden'));
  document.querySelector('#nav-reopen').addEventListener('click', () => document.body.classList.remove('nav-hidden'));
  document.querySelector('#dock-close').addEventListener('click', () => document.body.classList.add('dock-closed'));
  document.querySelector('#dock-reopen').addEventListener('click', () => document.body.classList.remove('dock-closed'));
  document.querySelector('#history-toggle').addEventListener('click', () => {
    const menu = document.querySelector('#session-menu');
    menu.hidden = !menu.hidden;
  });
  document.querySelector('#new-chat').addEventListener('click', () => {
    state.currentSession = 'new';
    state.messages = [{ role: 'assistant', text: '新会话已在原型中创建。输入框仍不会调用真实 Agent。' }];
    document.querySelector('#dock-status').textContent = 'new · draft';
    renderChat();
    showToast('已创建原型会话');
  });
  document.querySelector('#chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    const input = document.querySelector('#message');
    const value = input.value.trim();
    if (!value) return;
    state.messages.push({ role: 'user', text: value });
    input.value = '';
    document.querySelector('#dock-status').textContent = 'mock · thinking';
    renderChat();
    setTimeout(() => {
      state.messages.push({ role: 'assistant', text: '原型已收到。真实接入后，这里会显示 MiniCode 的流式回复与工具状态。' });
      document.querySelector('#dock-status').textContent = 'mock · complete';
      renderChat();
    }, 450);
  });
}

const querySection = new URLSearchParams(location.search).get('section');
if (!location.hash && VIEW_IDS.has(querySection)) history.replaceState({}, '', `${location.pathname}${location.search}#${querySection}`);

window.addEventListener('hashchange', render);
wireShell();
renderSessionMenu();
renderChat();
render();
setInterval(tickMeta, 1000);
setInterval(() => {
  state.lastRefresh = Date.now();
  tickMeta();
}, 5000);
