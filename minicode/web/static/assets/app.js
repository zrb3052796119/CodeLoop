/* Waku-inspired MiniCode Dashboard shell. REST authority, live invalidation, connection-scoped Chat presentation. */

const DATA = {
  workspace: 'mock-workspace · data not connected',
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
    { name: 'Web', status: 'connected', sessions: 3, latency: '18ms', detail: '127.0.0.1:8080 (mock)' },
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
};

const TITLES = {
  overview: '概览', runs: '运行', sessions: '会话', memory: '记忆',
  skills: '技能', connections: '连接', ops: 'LLM 运维', system: '系统',
};
const PAGE_KICKERS = {
  overview: 'AGENT OBSERVATORY / LIVE WORKSPACE',
  runs: 'EXECUTION JOURNAL / RETAINED RUNS',
  sessions: 'CONVERSATION AUTHORITY / LOCAL',
  memory: 'CONTEXT SYSTEM / PERSISTENT + RUNTIME',
  skills: 'CAPABILITY CATALOG / LOCAL',
  connections: 'INTEGRATION SURFACE / LOCAL',
  ops: 'RUNTIME OPERATIONS / OBSERVED',
  system: 'LOCAL SYSTEM / HEALTH',
};
const PAGE_DECKS = {
  overview: '沿着当前执行、真实事件与工作区信号观察 CodeLoop。',
  runs: '检查当前 Workspace 中经过裁剪的真实 Run 生命周期与事件。',
  sessions: '查看本地会话权威、可见消息与显式管理边界。',
  memory: '追踪持久作用域、运行时检索、注入与生命周期。',
  skills: '了解当前工作区可发现的本地能力。',
  connections: '观察 Gateway 与 MCP 的配置及当前进程事实。',
  ops: '汇总保留 RunJournal 中的用量、Cost、Tool 与 Failure 观测。',
  system: '检查本地 Gateway、功能与持久数据健康状态。',
};

const VIEW_IDS = new Set(Object.keys(TITLES));
const state = {
  lastRefresh: Date.now(),
};

const SESSION_SELECTION_STORAGE_KEY = 'minicode-dashboard-session-selection-v1';
const ACTIVE_TURN_STORAGE_KEY = 'minicode-dashboard-active-turn-v1';
const ACTIVE_TURN_RECOVERY_VERSION = 1;
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const TURN_ID_PATTERN = /^turn_[0-9a-f]{32}$/;

const snapshotStore = {
  phase: 'loading',
  data: null,
  error: null,
  refreshedAt: null,
  requestId: 0,
};

const runsStore = {
  phase: 'idle',
  items: [],
  page: null,
  source: null,
  coverage: null,
  summary: null,
  diagnostics: [],
  error: null,
  requestId: 0,
  loadingMore: false,
  filters: { status: null, source: null },
};

const runDetailStore = {
  phase: 'idle',
  runId: null,
  data: null,
  error: null,
  requestId: 0,
  loadingMore: false,
};

const observatoryStore = {
  phase: 'idle',
  detailPhase: 'idle',
  items: [],
  source: null,
  diagnostics: [],
  selectedRunId: null,
  detail: null,
  error: null,
  listRequestId: 0,
  detailRequestId: 0,
};

const runtimeTraceStore = {
  phase: 'idle',
  runs: [],
  source: null,
  coverage: null,
  selectedRunId: null,
  detail: null,
  error: null,
  listRequestId: 0,
  detailRequestId: 0,
};

const sessionsStore = {
  phase: 'idle',
  items: [],
  page: null,
  source: null,
  diagnostics: [],
  error: null,
  requestId: 0,
  loadingMore: false,
};

const sessionDetailStore = {
  phase: 'idle',
  sessionId: null,
  data: null,
  error: null,
  requestId: 0,
  loadingMore: false,
  selectionVersion: 0,
};

const chatStore = {
  phase: 'idle',
  requestGeneration: 0,
  operationGeneration: 0,
  draft: '',
  targetMode: 'existing',
  error: null,
  lastSessionId: null,
  activeTurnId: null,
  activeTargetSessionId: null,
  terminalTurnId: null,
  terminalPromise: null,
  recoveryChecked: false,
  feedbackTurnId: null,
  feedbackRunId: null,
  feedbackSessionId: null,
  feedbackPhase: 'idle',
  feedbackSignal: null,
  feedbackError: null,
  feedbackGeneration: 0,
};

const permissionStore = {
  phase: 'idle',
  items: [],
  revision: null,
  error: null,
  requestId: 0,
  actionGeneration: 0,
  actingPermissionId: null,
  lastUpdatedAt: null,
  reconciliationPromise: null,
};

const CHAT_STREAM_SCHEMA_VERSION = 1;
const CHAT_STREAM_MAX_FRAME_BYTES = 4 * 1024;
const CHAT_STREAM_MAX_TAIL_BYTES = 8 * 1024;
// The "▸" separator prefixes sub-agent tool names ("explore▸read_file") so
// parallel sub-agents stay distinguishable in the stream.
const CHAT_STREAM_TOOL_NAME_PATTERN = /^[A-Za-z0-9_.:\u25b8-]{1,128}$/;
const CHAT_STREAM_TOOL_ID_PATTERN = /^toolstream_[0-9a-f]{32}$/;
const CHAT_STREAM_RUN_ID_PATTERN = /^run_[0-9a-f]{32}$/;
const CHAT_STREAM_ERROR_CODES = new Set([
  'session_not_found', 'session_conflict', 'session_busy',
  'runtime_unavailable', 'turn_id_conflict', 'turn_in_progress',
  'turn_interrupted', 'turn_cancelled', 'turn_failed',
]);

function createChatStreamState(turnId = null, generation = 0) {
  return {
    turnId,
    generation,
    phase: turnId ? 'connecting' : 'idle',
    lastSequence: -1,
    provisionalText: '',
    tools: [],
    incomplete: false,
    truncatedAssistant: false,
    truncatedTools: false,
    detached: false,
    terminal: false,
    trusted: true,
    errorCode: null,
  };
}

const chatStreamStore = createChatStreamState();
let activeChatStreamReader = null;
let chatStreamRenderPending = false;

function resetChatStreamState(turnId = null, generation = 0) {
  Object.assign(chatStreamStore, createChatStreamState(turnId, generation));
}

function exactChatStreamFields(value, names) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...names].sort();
  return actual.length === expected.length
    && actual.every((name, index) => name === expected[index]);
}

function validChatStreamTimestamp(value) {
  return typeof value === 'string'
    && value.length <= 64
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value)
    && Number.isFinite(Date.parse(value));
}

function parseChatStreamFrame(line, expectedTurnId) {
  if (typeof line !== 'string' || !line || new TextEncoder().encode(line).byteLength > CHAT_STREAM_MAX_FRAME_BYTES) return null;
  let value;
  try {
    value = JSON.parse(line);
  } catch (_error) {
    return null;
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (value.schemaVersion !== CHAT_STREAM_SCHEMA_VERSION
      || !Number.isSafeInteger(value.sequence) || value.sequence < 0
      || !TURN_ID_PATTERN.test(value.turnId || '')
      || value.turnId !== expectedTurnId
      || typeof value.type !== 'string') return null;
  const common = ['schemaVersion', 'type', 'turnId', 'sequence'];
  if (value.type === 'chat.stream.ready') {
    return exactChatStreamFields(value, common) ? value : null;
  }
  if (value.type === 'chat.assistant.delta') {
    return exactChatStreamFields(value, [...common, 'text'])
      && typeof value.text === 'string' && value.text.length > 0 ? value : null;
  }
  if (value.type === 'chat.tool.started') {
    return exactChatStreamFields(value, [...common, 'toolStreamId', 'toolName'])
      && CHAT_STREAM_TOOL_ID_PATTERN.test(value.toolStreamId || '')
      && CHAT_STREAM_TOOL_NAME_PATTERN.test(value.toolName || '') ? value : null;
  }
  if (value.type === 'chat.tool.finished') {
    const fields = value.paired === true
      ? [...common, 'toolStreamId', 'toolName', 'outcome', 'paired']
      : [...common, 'toolName', 'outcome', 'paired'];
    return exactChatStreamFields(value, fields)
      && typeof value.paired === 'boolean'
      && CHAT_STREAM_TOOL_NAME_PATTERN.test(value.toolName || '')
      && ['success', 'error'].includes(value.outcome)
      && (value.paired === false || CHAT_STREAM_TOOL_ID_PATTERN.test(value.toolStreamId || '')) ? value : null;
  }
  if (value.type === 'chat.stream.truncated') {
    return exactChatStreamFields(value, [...common, 'category'])
      && ['assistant', 'tools'].includes(value.category) ? value : null;
  }
  if (value.type === 'chat.turn.completed') {
    return exactChatStreamFields(value, [...common, 'status', 'sessionId', 'created', 'updatedAt', 'runId'])
      && value.status === 'completed'
      && SESSION_ID_PATTERN.test(value.sessionId || '')
      && typeof value.created === 'boolean'
      && validChatStreamTimestamp(value.updatedAt)
      && (value.runId === null || CHAT_STREAM_RUN_ID_PATTERN.test(value.runId || '')) ? value : null;
  }
  if (value.type === 'chat.turn.error') {
    return exactChatStreamFields(value, [...common, 'code'])
      && CHAT_STREAM_ERROR_CODES.has(value.code) ? value : null;
  }
  return null;
}

function applyChatStreamFrame(store, frame, generation) {
  if (!store || generation !== store.generation || frame.turnId !== store.turnId || store.detached) return 'stale';
  if (store.terminal || frame.sequence <= store.lastSequence) return 'ignored';
  const hasGap = frame.sequence !== store.lastSequence + 1;
  store.lastSequence = frame.sequence;
  if (hasGap) {
    store.incomplete = true;
    store.trusted = false;
  }
  const terminal = ['chat.turn.completed', 'chat.turn.error'].includes(frame.type);
  if (hasGap && !terminal) return 'gap';
  if (frame.type === 'chat.stream.ready') {
    store.phase = 'ready';
  } else if (frame.type === 'chat.assistant.delta') {
    if (!store.trusted) return 'untrusted';
    store.provisionalText += frame.text;
    store.phase = 'generating';
  } else if (frame.type === 'chat.tool.started') {
    if (!store.trusted) return 'untrusted';
    store.tools.push({
      toolStreamId: frame.toolStreamId,
      toolName: frame.toolName,
      status: 'running',
      paired: true,
    });
    store.phase = 'tool';
  } else if (frame.type === 'chat.tool.finished') {
    if (!store.trusted) return 'untrusted';
    const existing = frame.paired
      ? store.tools.find((item) => item.toolStreamId === frame.toolStreamId && item.status === 'running')
      : null;
    if (existing) {
      existing.status = frame.outcome;
    } else {
      store.tools.push({
        toolStreamId: null,
        toolName: frame.toolName,
        status: frame.outcome,
        paired: false,
      });
    }
    store.phase = 'tool';
  } else if (frame.type === 'chat.stream.truncated') {
    if (frame.category === 'assistant') store.truncatedAssistant = true;
    else store.truncatedTools = true;
    store.incomplete = true;
  } else if (frame.type === 'chat.turn.completed') {
    store.terminal = true;
    store.phase = 'completed';
  } else if (frame.type === 'chat.turn.error') {
    store.terminal = true;
    store.phase = 'error';
    store.errorCode = frame.code;
  }
  return frame.type;
}

function detachChatStreamState(store) {
  if (!store || store.terminal) return;
  store.detached = true;
  store.incomplete = true;
  store.trusted = false;
  store.phase = 'disconnected';
}

function createChatNdjsonParser({ turnId, generation, store, onFrame, onInvalid }) {
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const encoder = new TextEncoder();
  let tail = '';
  let invalid = false;

  function reject() {
    if (invalid) return false;
    invalid = true;
    detachChatStreamState(store);
    if (onInvalid) onInvalid();
    return false;
  }

  function parseLine(rawLine) {
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    const frame = parseChatStreamFrame(line, turnId);
    if (!frame) return reject();
    const outcome = applyChatStreamFrame(store, frame, generation);
    if (outcome !== 'stale' && outcome !== 'ignored' && onFrame) onFrame(frame, outcome);
    return true;
  }

  function feed(chunk) {
    if (invalid) return false;
    try {
      tail += decoder.decode(chunk, { stream: true });
    } catch (_error) {
      return reject();
    }
    const lines = tail.split('\n');
    tail = lines.pop();
    for (const line of lines) {
      if (!parseLine(line)) return false;
    }
    if (encoder.encode(tail).byteLength > CHAT_STREAM_MAX_TAIL_BYTES) return reject();
    return true;
  }

  function finish() {
    if (invalid) return false;
    try {
      tail += decoder.decode();
    } catch (_error) {
      return reject();
    }
    if (tail && !parseLine(tail)) return false;
    tail = '';
    return true;
  }

  return { feed, finish, isInvalid: () => invalid };
}

async function consumeChatNdjson(body, options) {
  if (!body || typeof body.getReader !== 'function') throw new Error('stream body unavailable');
  const reader = body.getReader();
  activeChatStreamReader = reader;
  const parser = createChatNdjsonParser(options);
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      if (!parser.feed(value)) break;
    }
    parser.finish();
  } finally {
    if (activeChatStreamReader === reader) activeChatStreamReader = null;
    try {
      reader.releaseLock();
    } catch (_error) {
      // The response connection is already detached.
    }
  }
}

const memoryStore = {
  phase: 'idle',
  data: null,
  error: null,
  requestId: 0,
  loadingMore: false,
  filters: { scope: null, tier: null, category: null },
};

const memoryApprovalStore = {
  phase: 'idle',
  items: [],
  revision: null,
  diagnostics: [],
  error: null,
  requestId: 0,
  actionGeneration: 0,
  actingMemoryId: null,
  selectedMemoryId: null,
  lastUpdatedAt: null,
};
let memoryApprovalReadPromise = null;
let memoryApprovalRefreshQueued = false;

function createDeletionStore(kind) {
  return {
    phase: 'idle',
    kind,
    targetId: null,
    preview: null,
    result: null,
    errorCode: null,
    errorMessage: null,
    requestGeneration: 0,
    actionGeneration: 0,
    opener: null,
    outcomeUnconfirmed: false,
    staleNotice: false,
    localBusy: false,
    convergence: null,
  };
}

const conversationDeletionStore = createDeletionStore('conversation');
const projectMemoryDeletionStore = createDeletionStore('project-memory');
const conversationDeletionTombstones = new Set();
const projectMemoryDeletionTombstones = new Set();

const skillsStore = {
  phase: 'idle',
  data: null,
  error: null,
  requestId: 0,
  loadingMore: false,
  filters: { source: null, directory: null },
};

const connectionsStore = {
  phase: 'idle',
  data: null,
  error: null,
  requestId: 0,
};

const systemStore = {
  phase: 'idle',
  data: null,
  error: null,
  requestId: 0,
};

const dataHealthStore = {
  phase: 'idle',
  data: null,
  error: null,
  requestId: 0,
};

const opsStore = {
  phase: 'idle',
  data: null,
  error: null,
  requestId: 0,
};

const CHANGE_RESOURCE_NAMES = ['runs', 'sessions', 'turns', 'memory', 'skills', 'connections', 'permissions'];
const CHANGE_RESOURCE_STATUSES = ['live', 'partial', 'unavailable', 'error'];
const LIVE_RETRY_DELAYS_MS = [2000, 4000, 8000, 16000, 30000];
const DASHBOARD_EVENT_MAX_BYTES = 4 * 1024;
const DASHBOARD_EVENT_ID_PATTERN = /^evt_([0-9a-f]{32})_([0-9a-f]{16})$/;
const DASHBOARD_STREAM_ID_PATTERN = /^stream_([0-9a-f]{32})$/;
const DASHBOARD_REVISION_PATTERN = /^rev_[0-9a-f]{64}$/;
const DASHBOARD_RESET_REASONS = ['stream_restarted', 'replay_unavailable'];
const PERMISSION_PENDING_SCHEMA_VERSION = 1;
const PERMISSION_PENDING_MAX_BYTES = 128 * 1024;
const PERMISSION_MAX_ITEMS = 16;
const PERMISSION_ID_PATTERN = /^permission_[0-9a-f]{32}$/;
const PERMISSION_TOOL_OPERATION_ID_PATTERN = /^permissiontool_[0-9a-f]{32}$/;
const PERMISSION_TOOL_NAME_PATTERN = /^[A-Za-z0-9_.:-]{1,128}$/;
const PERMISSION_REVISION_PATTERN = /^permissionrev_[0-9a-f]{32}$/;
const PERMISSION_REDACTED_REVIEW = '[REDACTED SENSITIVE REVIEW]';
const PERMISSION_NETWORK_FINGERPRINT_PATTERN = /^networkreq_[0-9a-f]{64}$/;
const PERMISSION_NETWORK_HOST_PATTERN = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
const retiredPermissionTurnIds = new Set();
const MEMORY_APPROVAL_PENDING_SCHEMA_VERSION = 1;
const MEMORY_APPROVAL_MAX_BYTES = 128 * 1024;
const MEMORY_APPROVAL_MAX_ITEMS = 20;
const MEMORY_APPROVAL_MAX_ITEM_BYTES = 12 * 1024;
const MEMORY_APPROVAL_MAX_PREVIEW_BYTES = 8 * 1024;
const MEMORY_APPROVAL_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/;
const MEMORY_APPROVAL_REVISION_PATTERN = /^memoryapprovalrev_[0-9a-f]{64}$/;
const MEMORY_REVIEW_REVISION_PATTERN = /^memoryreviewrev_[0-9a-f]{64}$/;
const MEMORY_APPROVAL_CATEGORIES = new Set([
  'general', 'note', 'directive', 'architecture', 'code-pattern', 'testing',
  'configuration', 'workflow', 'security', 'performance', 'convention',
  'decision', 'preference', 'pattern', 'insight', 'other',
]);
const MEMORY_APPROVAL_SOURCES = new Set(['reflection', 'curator', 'user', 'manual', 'unknown']);
const MEMORY_APPROVAL_HIDDEN_PREVIEWS = new Set([
  '[REDACTED SENSITIVE MEMORY]',
  '[UNSAFE MEMORY CONTENT HIDDEN]',
  '[MEMORY REVIEW TOO LARGE]',
]);
const MEMORY_APPROVAL_DIAGNOSTICS = new Set(['items_limited', 'snapshot_limited']);
const DATA_HEALTH_SCHEMA_VERSION = 1;
const DATA_HEALTH_MAX_BYTES = 256 * 1024;
const DATA_HEALTH_MAX_DIAGNOSTICS = 64;
const DATA_HEALTH_MAX_SAFE_INTEGER = Number.MAX_SAFE_INTEGER;
const DATA_HEALTH_STATUSES = new Set(['live', 'partial', 'unavailable', 'error']);
const DATA_HEALTH_STORE_SPECS = [
  ['sessions', 'workspace', 'persistent', 'planned'],
  ['conversation-turns', 'workspace', 'persistent', 'planned'],
  ['run-journal', 'workspace', 'persistent', 'planned'],
  ['deletion-coordination', 'workspace', 'persistent', 'planned'],
  ['memory-user', 'user', 'persistent', 'excluded'],
  ['memory-project', 'workspace', 'persistent', 'planned'],
  ['memory-local', 'local', 'persistent', 'planned'],
  ['memory-approval-user', 'user', 'persistent', 'excluded'],
  ['memory-approval-project', 'workspace', 'persistent', 'planned'],
  ['memory-approval-local', 'local', 'persistent', 'planned'],
  ['memory-pipeline-state', 'workspace', 'persistent', 'planned'],
  ['tool-results', 'workspace', 'persistent', 'planned'],
  ['permissions', 'user', 'persistent', 'excluded'],
  ['configuration', 'configuration', 'source', 'excluded'],
  ['mcp-configuration', 'configuration', 'source', 'excluded'],
  ['user-profile', 'user', 'source', 'excluded'],
  ['project-profile', 'configuration', 'source', 'excluded'],
  ['skills-user', 'user', 'source', 'excluded'],
  ['skills-project', 'configuration', 'source', 'excluded'],
  ['user-runtime-artifacts', 'user', 'persistent', 'excluded'],
  ['workspace-runtime-artifacts', 'configuration', 'source', 'excluded'],
  ['permission-broker', 'process', 'process-local', 'not-applicable'],
  ['mcp-current-registry', 'process', 'process-local', 'not-applicable'],
  ['gateway-runtime', 'process', 'process-local', 'not-applicable'],
  ['working-memory', 'process', 'process-local', 'not-applicable'],
];
const DATA_HEALTH_STORE_MESSAGES = {
  live: 'The bounded read-only scan completed.',
  partial: 'Some persisted facts could not be verified safely.',
  unavailable: 'This store could not be inspected safely.',
  error: 'Some persisted facts could not be verified safely.',
  process: 'Process-local state is not a disk persistence fact.',
};
const DATA_HEALTH_DIAGNOSTICS = {
  scan_limited: 'The directory entry budget was reached.',
  root_unsafe: 'A configured storage root was not a regular directory.',
  entry_unsafe: 'A symbolic link or special entry was rejected.',
  read_failed: 'A persisted file could not be read safely.',
  oversized_file: 'A file exceeded the bounded parsing limit.',
  invalid_json: 'A persisted JSON document was malformed.',
  invalid_record: 'A persisted record failed bounded validation.',
  index_drift: 'Canonical records and their index did not agree.',
  orphan_reference: 'A cross-store reference had no matching record.',
  temporary_artifact: 'A temporary or backup artifact remains on disk.',
  legacy_source: 'A legacy source exists but was not parsed for record counts.',
  active_writer: 'An active-writer marker is present.',
};

function hasExactKeys(value, expected) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === [...expected].sort()[index]);
}

function validChangeSnapshot(payload) {
  const topKeys = ['schemaVersion', 'generatedAt', 'mode', 'pollAfterMs', 'resources', 'diagnostics'];
  if (!hasExactKeys(payload, topKeys)
      || payload.schemaVersion !== 2
      || payload.mode !== 'read-only'
      || typeof payload.generatedAt !== 'string'
      || payload.generatedAt.length > 64
      || Number.isNaN(Date.parse(payload.generatedAt))
      || !Number.isInteger(payload.pollAfterMs)
      || payload.pollAfterMs < 1000
      || payload.pollAfterMs > 10000
      || !hasExactKeys(payload.resources, CHANGE_RESOURCE_NAMES)
      || !Object.keys(payload.resources).every((name, index) => name === CHANGE_RESOURCE_NAMES[index])
      || !Array.isArray(payload.diagnostics)
      || payload.diagnostics.length > 24) return false;
  const resourcesValid = CHANGE_RESOURCE_NAMES.every((name) => {
    const resource = payload.resources[name];
    return hasExactKeys(resource, ['status', 'revision'])
      && CHANGE_RESOURCE_STATUSES.includes(resource.status)
      && /^rev_[0-9a-f]{64}$/.test(resource.revision);
  });
  const diagnosticsValid = payload.diagnostics.every((item) => hasExactKeys(item, ['resource', 'code', 'message'])
    && CHANGE_RESOURCE_NAMES.includes(item.resource)
    && typeof item.code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(item.code)
    && typeof item.message === 'string' && item.message.length <= 240);
  return resourcesValid && diagnosticsValid;
}

function utf8ByteLength(value) {
  if (typeof TextEncoder === 'function') return new TextEncoder().encode(value).byteLength;
  try {
    return unescape(encodeURIComponent(value)).length;
  } catch (_error) {
    return DASHBOARD_EVENT_MAX_BYTES + 1;
  }
}

function validateDataHealthPayload(payload) {
  const topFields = [
    'schemaVersion', 'generatedAt', 'mode', 'status', 'workspace', 'summary',
    'stores', 'maintenancePlan', 'diagnostics',
  ];
  const validTimestamp = (value) => typeof value === 'string'
    && value.length <= 64
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
  const safeInteger = (value) => Number.isSafeInteger(value) && value >= 0;
  let encoded;
  try {
    encoded = JSON.stringify(payload);
  } catch (_error) {
    return null;
  }
  if (utf8ByteLength(encoded) > DATA_HEALTH_MAX_BYTES
      || !hasExactKeys(payload, topFields)
      || payload.schemaVersion !== DATA_HEALTH_SCHEMA_VERSION
      || typeof payload.schemaVersion === 'boolean'
      || payload.mode !== 'read-only'
      || !DATA_HEALTH_STATUSES.has(payload.status)
      || !validTimestamp(payload.generatedAt)
      || !hasExactKeys(payload.workspace, ['id', 'name'])
      || !/^ws_[0-9a-f]{16}$/.test(payload.workspace.id || '')
      || typeof payload.workspace.name !== 'string'
      || payload.workspace.name.length < 1
      || payload.workspace.name.length > 160
      || /[\\/\0]/.test(payload.workspace.name)
      || !hasExactKeys(payload.summary, [
        'storeCount', 'knownRecordCount', 'knownByteCount', 'issueCount',
      ])
      || !Object.values(payload.summary).every(safeInteger)
      || !Array.isArray(payload.stores)
      || payload.stores.length !== DATA_HEALTH_STORE_SPECS.length
      || !Array.isArray(payload.diagnostics)
      || payload.diagnostics.length > DATA_HEALTH_MAX_DIAGNOSTICS) return null;

  const storeFields = [
    'id', 'scope', 'durability', 'status', 'recordCount', 'byteCount',
    'updatedAt', 'resetDisposition', 'message',
  ];
  for (let index = 0; index < DATA_HEALTH_STORE_SPECS.length; index += 1) {
    const store = payload.stores[index];
    const [id, scope, durability, resetDisposition] = DATA_HEALTH_STORE_SPECS[index];
    const expectedMessage = durability === 'process-local'
      ? DATA_HEALTH_STORE_MESSAGES.process
      : DATA_HEALTH_STORE_MESSAGES[store?.status];
    if (!hasExactKeys(store, storeFields)
        || store.id !== id
        || store.scope !== scope
        || store.durability !== durability
        || store.resetDisposition !== resetDisposition
        || !DATA_HEALTH_STATUSES.has(store.status)
        || store.message !== expectedMessage
        || (store.recordCount !== null && !safeInteger(store.recordCount))
        || (store.byteCount !== null && !safeInteger(store.byteCount))
        || (store.updatedAt !== null && !validTimestamp(store.updatedAt))) return null;
    if (durability === 'process-local'
        && (store.status !== 'live'
          || store.recordCount !== null
          || store.byteCount !== null
          || store.updatedAt !== null)) return null;
  }

  const plan = payload.maintenancePlan;
  const eligible = DATA_HEALTH_STORE_SPECS
    .filter((item) => item[3] === 'planned').map((item) => item[0]);
  const excluded = DATA_HEALTH_STORE_SPECS
    .filter((item) => item[3] === 'excluded').map((item) => item[0]);
  if (!hasExactKeys(plan, [
    'status', 'destructiveActionsAvailable', 'eligibleStoreIds',
    'excludedStoreIds', 'blockers',
  ])
      || plan.status !== 'planning'
      || plan.destructiveActionsAvailable !== false
      || !Array.isArray(plan.eligibleStoreIds)
      || !Array.isArray(plan.excludedStoreIds)
      || !Array.isArray(plan.blockers)
      || plan.blockers.length > DATA_HEALTH_STORE_SPECS.length + 1
      || JSON.stringify(plan.eligibleStoreIds) !== JSON.stringify(eligible)
      || JSON.stringify(plan.excludedStoreIds) !== JSON.stringify(excluded)
      || !plan.blockers.every((blocker) => hasExactKeys(blocker, ['code', 'storeId'])
        && ['store_not_live', 'active_maintenance_fence'].includes(blocker.code)
        && eligible.includes(blocker.storeId)
        && (blocker.code !== 'active_maintenance_fence'
          || blocker.storeId === 'deletion-coordination'))) return null;

  const knownRecordCount = payload.stores.reduce(
    (total, store) => total + (store.recordCount ?? 0), 0,
  );
  const knownByteCount = payload.stores.reduce(
    (total, store) => total + (store.byteCount ?? 0), 0,
  );
  const issueCount = payload.stores.filter((store) => store.status !== 'live').length;
  if (!Number.isSafeInteger(knownRecordCount)
      || !Number.isSafeInteger(knownByteCount)
      || payload.summary.storeCount !== payload.stores.length
      || payload.summary.knownRecordCount !== knownRecordCount
      || payload.summary.knownByteCount !== knownByteCount
      || payload.summary.issueCount !== issueCount) return null;

  const storeIds = new Set(DATA_HEALTH_STORE_SPECS.map((item) => item[0]));
  if (!payload.diagnostics.every((diagnostic) => hasExactKeys(
    diagnostic, ['storeId', 'code', 'message'],
  )
      && storeIds.has(diagnostic.storeId)
      && Object.hasOwn(DATA_HEALTH_DIAGNOSTICS, diagnostic.code)
      && diagnostic.message === DATA_HEALTH_DIAGNOSTICS[diagnostic.code])) return null;
  return payload;
}

function validPermissionTimestamp(value) {
  return typeof value === 'string'
    && value.length <= 64
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
}

function safePermissionRelativePath(value, maxBytes = 2048) {
  if (typeof value !== 'string' || !value || utf8ByteLength(value) > maxBytes
      || value.includes('\\') || value.includes('\0') || value.startsWith('/')
      || /^[A-Za-z]:[\\/]/.test(value)) return false;
  if (value === '.') return true;
  const parts = value.split('/');
  return parts.every((part) => part && part !== '.' && part !== '..');
}

function safePermissionNetworkHostname(value) {
  if (typeof value !== 'string' || !value) return false;
  if (value.includes(':')) {
    if (!/^[0-9a-f:]{2,39}$/.test(value)) return false;
    try {
      const parsed = new URL(`https://[${value}]/`);
      if (!parsed.hostname.startsWith('[') || !parsed.hostname.endsWith(']')) return false;
    } catch (_error) {
      return false;
    }
    return value !== '::'
      && value !== '::1'
      && !/^f[cd]/.test(value)
      && !/^fe[89ab]/.test(value)
      && !/^ff/.test(value)
      && !/^::ffff:/.test(value)
      && !/^2001:db8(?::|$)/.test(value);
  }
  if (!PERMISSION_NETWORK_HOST_PATTERN.test(value)
      || value === 'localhost'
      || value.endsWith('.localhost')) return false;
  if (!/^\d{1,3}(?:\.\d{1,3}){3}$/.test(value)) return true;
  const octets = value.split('.').map(Number);
  if (octets.some((part) => part < 0 || part > 255)) return false;
  const [a, b, c] = octets;
  return !(a === 0
    || a === 10
    || a === 100 && b >= 64 && b <= 127
    || a === 127
    || a === 169 && b === 254
    || a === 172 && b >= 16 && b <= 31
    || a === 192 && b === 0
    || a === 192 && b === 88 && c === 99
    || a === 192 && b === 168
    || a === 198 && [18, 19].includes(b)
    || a === 198 && b === 51 && c === 100
    || a === 203 && b === 0 && c === 113
    || a >= 224);
}

function validPermissionReview(item) {
  const review = item?.review;
  if (!review || typeof review !== 'object' || Array.isArray(review)) return false;
  if (item.kind === 'network') {
    if (item.reviewable === false && hasExactKeys(review, [])) return true;
    return hasExactKeys(review, [
      'reviewVersion', 'method', 'scheme', 'hostname', 'port', 'pathSummary',
      'hasBody', 'hasSensitiveHeaders', 'requestFingerprint',
    ])
      && review.reviewVersion === 1
      && typeof review.reviewVersion !== 'boolean'
      && ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'].includes(review.method)
      && review.scheme === 'https'
      && safePermissionNetworkHostname(review.hostname)
      && Number.isInteger(review.port)
      && review.port >= 1
      && review.port <= 65535
      && typeof review.pathSummary === 'string'
      && review.pathSummary.startsWith('/')
      && utf8ByteLength(review.pathSummary) <= 256
      && !/[?#\u0000-\u001f\u007f-\u009f]/.test(review.pathSummary)
      && typeof review.hasBody === 'boolean'
      && typeof review.hasSensitiveHeaders === 'boolean'
      && PERMISSION_NETWORK_FINGERPRINT_PATTERN.test(
        review.requestFingerprint || '',
      );
  }
  if (item.kind === 'edit') {
    return hasExactKeys(review, ['targetPath', 'diffPreview', 'complete', 'truncated', 'redacted'])
      && safePermissionRelativePath(review.targetPath)
      && typeof review.diffPreview === 'string'
      && utf8ByteLength(review.diffPreview) <= 32 * 1024
      && typeof review.complete === 'boolean'
      && typeof review.truncated === 'boolean'
      && typeof review.redacted === 'boolean';
  }
  if (item.kind === 'command') {
    return hasExactKeys(review, ['commandPreview', 'cwd', 'reason', 'complete', 'truncated', 'redacted'])
      && typeof review.commandPreview === 'string'
      && utf8ByteLength(review.commandPreview) <= 4 * 1024
      && safePermissionRelativePath(review.cwd)
      && typeof review.reason === 'string'
      && utf8ByteLength(review.reason) <= 1024
      && typeof review.complete === 'boolean'
      && typeof review.truncated === 'boolean'
      && typeof review.redacted === 'boolean';
  }
  if (item.kind === 'path') {
    return hasExactKeys(review, ['intent', 'outsideWorkspace'])
      && typeof review.intent === 'string'
      && review.intent.length > 0
      && utf8ByteLength(review.intent) <= 512
      && review.outsideWorkspace === true;
  }
  return false;
}

function permissionReviewConsistent(item) {
  if (!item || !validPermissionReview(item) || !Array.isArray(item.choices)) return false;
  const allowAndDeny = item.choices.length === 2
    && item.choices[0] === 'allow_once'
    && item.choices[1] === 'deny_once';
  const denyOnly = item.choices.length === 1 && item.choices[0] === 'deny_once';
  if (item.kind === 'path') return item.reviewable === false && denyOnly;
  if (item.kind === 'network') {
    if (item.reviewable === false) {
      return hasExactKeys(item.review, []) && denyOnly;
    }
    return item.reviewable === true && allowAndDeny;
  }
  if (!['edit', 'command'].includes(item.kind)) return false;
  if (item.reviewable === false) return denyOnly;
  if (item.reviewable !== true || !allowAndDeny) return false;
  const preview = item.kind === 'command'
    ? item.review.commandPreview
    : item.review.diffPreview;
  const hidden = preview === PERMISSION_REDACTED_REVIEW;
  return item.review.complete === true
    && item.review.truncated === false
    && item.review.redacted === false
    && !hidden;
}

function validPermissionItem(item) {
  const fields = [
    'permissionId', 'turnId', 'runId', 'toolOperationId', 'toolName', 'kind',
    'summary', 'reviewable', 'review', 'choices', 'createdAt', 'expiresAt',
  ];
  if (!hasExactKeys(item, fields)
      || !PERMISSION_ID_PATTERN.test(item.permissionId || '')
      || !TURN_ID_PATTERN.test(item.turnId || '')
      || (item.runId !== null && !CHAT_STREAM_RUN_ID_PATTERN.test(item.runId || ''))
      || !PERMISSION_TOOL_OPERATION_ID_PATTERN.test(item.toolOperationId || '')
      || !PERMISSION_TOOL_NAME_PATTERN.test(item.toolName || '')
      || !['edit', 'command', 'path', 'network'].includes(item.kind)
      || typeof item.summary !== 'string'
      || !item.summary
      || utf8ByteLength(item.summary) > 2048
      || typeof item.reviewable !== 'boolean'
      || !Array.isArray(item.choices)
      || item.choices.length < 1
      || item.choices.length > 2
      || new Set(item.choices).size !== item.choices.length
      || item.choices.some((choice) => !['allow_once', 'deny_once'].includes(choice))
      || !item.choices.includes('deny_once')
      || !validPermissionTimestamp(item.createdAt)
      || !validPermissionTimestamp(item.expiresAt)
      || !permissionReviewConsistent(item)) return false;
  return true;
}

function validatePermissionPendingPayload(payload) {
  const fields = ['schemaVersion', 'generatedAt', 'mode', 'source', 'revision', 'items'];
  if (!hasExactKeys(payload, fields)
      || payload.schemaVersion !== PERMISSION_PENDING_SCHEMA_VERSION
      || typeof payload.schemaVersion === 'boolean'
      || payload.mode !== 'read-only'
      || payload.source !== 'gateway-permission-broker'
      || !validPermissionTimestamp(payload.generatedAt)
      || !PERMISSION_REVISION_PATTERN.test(payload.revision || '')
      || !Array.isArray(payload.items)
      || payload.items.length > PERMISSION_MAX_ITEMS) return null;
  let encoded;
  try {
    encoded = JSON.stringify(payload);
  } catch (_error) {
    return null;
  }
  if (utf8ByteLength(encoded) > PERMISSION_PENDING_MAX_BYTES
      || !payload.items.every(validPermissionItem)) return null;
  return payload;
}

function canAllowPermission(item) {
  return Boolean(item?.reviewable === true
    && ['edit', 'command', 'network'].includes(item.kind)
    && permissionReviewConsistent(item));
}

function validPermissionDecisionPayload(payload, expected) {
  return hasExactKeys(payload, [
    'schemaVersion', 'mode', 'permissionId', 'turnId', 'status', 'decision',
    'decisionAccepted', 'updatedAt',
  ])
    && payload.schemaVersion === 1
    && typeof payload.schemaVersion !== 'boolean'
    && payload.mode === 'read-write'
    && payload.permissionId === expected.permissionId
    && payload.turnId === expected.turnId
    && ['allowed', 'denied'].includes(payload.status)
    && payload.status === (expected.decision === 'allow_once' ? 'allowed' : 'denied')
    && payload.decision === expected.decision
    && typeof payload.decisionAccepted === 'boolean'
    && validPermissionTimestamp(payload.updatedAt);
}

function validMemoryApprovalTimestamp(value) {
  return typeof value === 'string'
    && value.length <= 64
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
}

function validMemoryApprovalReview(review) {
  return hasExactKeys(review, ['contentPreview', 'complete', 'truncated', 'redacted'])
    && typeof review.contentPreview === 'string'
    && review.contentPreview.length > 0
    && utf8ByteLength(review.contentPreview) <= MEMORY_APPROVAL_MAX_PREVIEW_BYTES
    && typeof review.complete === 'boolean'
    && typeof review.truncated === 'boolean'
    && typeof review.redacted === 'boolean';
}

function memoryApprovalReviewConsistent(item) {
  if (!item || !validMemoryApprovalReview(item.review) || !Array.isArray(item.choices)) return false;
  const approvableChoices = item.choices.length === 2
    && item.choices[0] === 'approve'
    && item.choices[1] === 'reject';
  const denyOnlyChoices = item.choices.length === 1 && item.choices[0] === 'reject';
  if (item.reviewable === false) return denyOnlyChoices;
  if (item.reviewable !== true || !approvableChoices) return false;
  return item.review.complete === true
    && item.review.truncated === false
    && item.review.redacted === false
    && ['safe', 'suspicious'].includes(item.safetyStatus)
    && ['low', 'medium'].includes(item.risk)
    && !MEMORY_APPROVAL_HIDDEN_PREVIEWS.has(item.review.contentPreview);
}

function validMemoryApprovalItem(item) {
  const fields = [
    'memoryId', 'scope', 'scopeKind', 'category', 'tier', 'source', 'createdAt',
    'risk', 'safetyStatus', 'reviewable', 'review', 'reviewRevision', 'choices',
  ];
  if (!hasExactKeys(item, fields)
      || !MEMORY_APPROVAL_ID_PATTERN.test(item.memoryId || '')
      || utf8ByteLength(item.memoryId) > 160
      || !['user', 'project', 'local'].includes(item.scope)
      || !['user/global', 'workspace'].includes(item.scopeKind)
      || item.scopeKind !== (item.scope === 'user' ? 'user/global' : 'workspace')
      || typeof item.category !== 'string'
      || !MEMORY_APPROVAL_CATEGORIES.has(item.category)
      || !['working', 'short_term', 'long_term', 'archival'].includes(item.tier)
      || typeof item.source !== 'string'
      || !MEMORY_APPROVAL_SOURCES.has(item.source)
      || !validMemoryApprovalTimestamp(item.createdAt)
      || !['low', 'medium', 'high'].includes(item.risk)
      || !['safe', 'suspicious', 'unsafe'].includes(item.safetyStatus)
      || item.risk !== ({ safe: 'low', suspicious: 'medium', unsafe: 'high' })[item.safetyStatus]
      || typeof item.reviewable !== 'boolean'
      || !MEMORY_REVIEW_REVISION_PATTERN.test(item.reviewRevision || '')
      || !Array.isArray(item.choices)
      || item.choices.length < 1
      || item.choices.length > 2
      || new Set(item.choices).size !== item.choices.length
      || item.choices.some((choice) => !['approve', 'reject'].includes(choice))
      || !item.choices.includes('reject')
      || !memoryApprovalReviewConsistent(item)) return false;
  try {
    return utf8ByteLength(JSON.stringify(item)) <= MEMORY_APPROVAL_MAX_ITEM_BYTES;
  } catch (_error) {
    return false;
  }
}

function validateMemoryApprovalPendingPayload(payload) {
  const fields = ['schemaVersion', 'generatedAt', 'mode', 'source', 'revision', 'items', 'diagnostics'];
  if (!hasExactKeys(payload, fields)
      || payload.schemaVersion !== MEMORY_APPROVAL_PENDING_SCHEMA_VERSION
      || typeof payload.schemaVersion === 'boolean'
      || payload.mode !== 'read-only'
      || !validMemoryApprovalTimestamp(payload.generatedAt)
      || !hasExactKeys(payload.source, ['status', 'updatedAt', 'message'])
      || payload.source.status !== 'live'
      || !validMemoryApprovalTimestamp(payload.source.updatedAt)
      || payload.source.message !== null
      || !MEMORY_APPROVAL_REVISION_PATTERN.test(payload.revision || '')
      || !Array.isArray(payload.items)
      || payload.items.length > MEMORY_APPROVAL_MAX_ITEMS
      || !Array.isArray(payload.diagnostics)
      || payload.diagnostics.length > MEMORY_APPROVAL_MAX_ITEMS
      || !payload.diagnostics.every((diagnostic) => hasExactKeys(diagnostic, ['code'])
        && MEMORY_APPROVAL_DIAGNOSTICS.has(diagnostic.code))) return null;
  let encoded;
  try {
    encoded = JSON.stringify(payload);
  } catch (_error) {
    return null;
  }
  if (utf8ByteLength(encoded) > MEMORY_APPROVAL_MAX_BYTES
      || !payload.items.every(validMemoryApprovalItem)) return null;
  return payload;
}

function canApproveMemory(item) {
  return Boolean(item?.reviewable === true
    && item.choices?.[0] === 'approve'
    && memoryApprovalReviewConsistent(item));
}

function validMemoryApprovalDecisionPayload(payload, expected) {
  return hasExactKeys(payload, [
    'schemaVersion', 'generatedAt', 'mode', 'memoryId', 'status', 'decision',
    'decisionAccepted', 'updatedAt',
  ])
    && payload.schemaVersion === 1
    && typeof payload.schemaVersion !== 'boolean'
    && payload.mode === 'read-write'
    && validMemoryApprovalTimestamp(payload.generatedAt)
    && MEMORY_APPROVAL_ID_PATTERN.test(expected?.memoryId || '')
    && MEMORY_REVIEW_REVISION_PATTERN.test(expected?.reviewRevision || '')
    && ['approve', 'reject'].includes(expected?.decision)
    && payload.memoryId === expected.memoryId
    && payload.status === (expected.decision === 'approve' ? 'approved' : 'rejected')
    && payload.decision === expected.decision
    && typeof payload.decisionAccepted === 'boolean'
    && validMemoryApprovalTimestamp(payload.updatedAt);
}

function validDashboardEventTime(value) {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
    && !Number.isNaN(Date.parse(value))
    && new Date(value).toISOString() === value;
}

function parseDashboardEvent(eventType, eventId, rawData) {
  if (typeof eventType !== 'string'
      || typeof eventId !== 'string'
      || typeof rawData !== 'string'
      || utf8ByteLength(rawData) > DASHBOARD_EVENT_MAX_BYTES) return null;
  const idMatch = DASHBOARD_EVENT_ID_PATTERN.exec(eventId);
  if (!idMatch) return null;
  let payload;
  try {
    payload = JSON.parse(rawData);
  } catch (_error) {
    return null;
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)
      || payload.schemaVersion !== 2
      || !validDashboardEventTime(payload.generatedAt)
      || payload.type !== eventType) return null;
  const common = {
    epoch: idMatch[1],
    sequence: BigInt(`0x${idMatch[2]}`),
  };
  if (eventType === 'stream.ready') {
    const streamMatch = typeof payload.streamId === 'string'
      ? DASHBOARD_STREAM_ID_PATTERN.exec(payload.streamId)
      : null;
    if (!hasExactKeys(payload, ['schemaVersion', 'type', 'streamId', 'generatedAt', 'retryMs'])
        || !streamMatch
        || streamMatch[1] !== common.epoch
        || !Number.isInteger(payload.retryMs)
        || payload.retryMs < 1000
        || payload.retryMs > 30000) return null;
    return { kind: 'ready', ...common };
  }
  if (eventType === 'resources.changed') {
    if (!hasExactKeys(payload, ['schemaVersion', 'type', 'generatedAt', 'resources'])
        || !Array.isArray(payload.resources)
        || payload.resources.length < 1
        || payload.resources.length > CHANGE_RESOURCE_NAMES.length) return null;
    let previousIndex = -1;
    const resources = [];
    for (const item of payload.resources) {
      if (!hasExactKeys(item, ['name', 'status', 'revision'])) return null;
      const index = CHANGE_RESOURCE_NAMES.indexOf(item.name);
      if (index <= previousIndex
          || !CHANGE_RESOURCE_STATUSES.includes(item.status)
          || typeof item.revision !== 'string'
          || !DASHBOARD_REVISION_PATTERN.test(item.revision)) return null;
      previousIndex = index;
      resources.push(item.name);
    }
    return { kind: 'changed', resources, ...common };
  }
  if (eventType === 'stream.reset') {
    if (!hasExactKeys(payload, ['schemaVersion', 'type', 'generatedAt', 'reason', 'resources'])
        || !DASHBOARD_RESET_REASONS.includes(payload.reason)
        || !Array.isArray(payload.resources)
        || payload.resources.length !== CHANGE_RESOURCE_NAMES.length
        || !payload.resources.every((name, index) => name === CHANGE_RESOURCE_NAMES[index])) return null;
    return { kind: 'reset', reason: payload.reason, ...common };
  }
  return null;
}

const DELETION_REVISION_PATTERN = /^delrev_[0-9a-f]{64}$/;
const DELETION_RESPONSE_MAX_BYTES = 64 * 1024;
const DELETION_COUNT_MAX = 1_000_000;
const DELETION_ARRAY_MAX = 8;
const DELETION_PREVIEW_TOP_KEYS = [
  'schemaVersion', 'generatedAt', 'mode', 'kind', 'target', 'status',
  'deletionRevision', 'affected', 'blockers', 'diagnostics',
];
const DELETION_RESULT_TOP_KEYS = [
  'schemaVersion', 'generatedAt', 'mode', 'kind', 'target', 'status',
  'deletionRevision', 'deleted', 'remaining', 'diagnostics',
];
const CONVERSATION_DELETION_DIAGNOSTICS = new Set([
  'session_record_invalid', 'session_index_invalid', 'session_delta_invalid',
  'session_delta_scan_limited', 'session_ownership_unavailable',
  'session_scan_unavailable', 'turn_record_invalid', 'turn_scan_unavailable',
  'turn_scan_limited', 'run_record_invalid', 'run_writer_invalid',
  'run_scan_unavailable', 'run_scan_limited',
]);
const PROJECT_MEMORY_DELETION_DIAGNOSTICS = new Set([
  'memory_metadata_invalid', 'memory_audit_invalid',
]);
const PROJECT_MEMORY_DELETION_CATEGORIES = new Set([
  'general', 'note', 'directive', 'architecture', 'code-pattern', 'testing',
  'configuration', 'workflow', 'security', 'performance', 'convention',
  'decision', 'preference', 'pattern', 'insight',
]);
const PROJECT_MEMORY_DELETION_TIERS = new Set([
  'working', 'short_term', 'long_term', 'archival',
]);
const PROJECT_MEMORY_DELETION_LIFECYCLES = new Set([
  'active', 'pending', 'rejected', 'held', 'archived', 'deprecated',
  'invalid', 'archived_duplicate',
]);
const PROJECT_MEMORY_DELETION_APPROVALS = new Set([
  'pending', 'approved', 'rejected',
]);

function validDeletionTimestamp(value) {
  return typeof value === 'string'
    && value.length <= 64
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)
    && Number.isFinite(Date.parse(value))
    && new Date(value).toISOString() === value;
}

function validDeletionCount(value) {
  return typeof value === 'number'
    && Number.isSafeInteger(value)
    && value >= 0
    && value <= DELETION_COUNT_MAX;
}

function deletionCountKeys(kind) {
  return kind === 'conversation'
    ? ['sessions', 'turns', 'runs']
    : kind === 'project-memory'
      ? ['entries', 'approvalAuditRecords', 'backlinks']
      : null;
}

function validDeletionCounts(value, kind) {
  const keys = deletionCountKeys(kind);
  return Boolean(keys
    && hasExactKeys(value, keys)
    && keys.every((key) => validDeletionCount(value[key])));
}

function validDeletionCodeItems(items, allowed) {
  return Array.isArray(items)
    && items.length <= DELETION_ARRAY_MAX
    && new Set(items.map((item) => item?.code)).size === items.length
    && items.every((item) => hasExactKeys(item, ['code'])
      && typeof item.code === 'string'
      && allowed.has(item.code));
}

function validDeletionTarget(target, kind, targetId, context = {}) {
  if (kind === 'conversation') {
    return hasExactKeys(target, ['sessionId'])
      && SESSION_ID_PATTERN.test(target.sessionId || '')
      && target.sessionId === targetId;
  }
  if (kind !== 'project-memory'
      || !hasExactKeys(target, [
        'memoryId', 'scope', 'category', 'tier', 'lifecycleStatus',
        'approvalStatus',
      ])
      || !MEMORY_APPROVAL_ID_PATTERN.test(target.memoryId || '')
      || utf8ByteLength(target.memoryId) > 160
      || target.memoryId !== targetId
      || target.scope !== 'project') return false;
  const unknownAllowed = context.entries === 0
    || context.diagnosticCodes?.has('memory_metadata_invalid');
  const metadataValid = PROJECT_MEMORY_DELETION_CATEGORIES.has(target.category)
    && PROJECT_MEMORY_DELETION_TIERS.has(target.tier)
    && PROJECT_MEMORY_DELETION_LIFECYCLES.has(target.lifecycleStatus)
    && PROJECT_MEMORY_DELETION_APPROVALS.has(target.approvalStatus);
  const allUnknown = target.category === 'unknown'
    && target.tier === 'unknown'
    && target.lifecycleStatus === 'unknown'
    && target.approvalStatus === 'unknown';
  return metadataValid || (unknownAllowed && allUnknown);
}

function validateDeletionPreview(payload, kind, targetId) {
  if (!hasExactKeys(payload, DELETION_PREVIEW_TOP_KEYS)
      || payload.schemaVersion !== 1
      || typeof payload.schemaVersion === 'boolean'
      || payload.mode !== 'read-write'
      || payload.kind !== kind
      || !validDeletionTimestamp(payload.generatedAt)
      || !DELETION_REVISION_PATTERN.test(payload.deletionRevision || '')
      || !validDeletionCounts(payload.affected, kind)
      || !Array.isArray(payload.blockers)
      || !Array.isArray(payload.diagnostics)) return null;
  const statuses = kind === 'conversation'
    ? ['ready', 'busy', 'partial', 'unavailable', 'completed']
    : ['ready', 'partial', 'unavailable', 'completed'];
  const blockersValid = kind === 'conversation'
    ? validDeletionCodeItems(payload.blockers, new Set(['active_turn', 'active_run']))
    : payload.blockers.length === 0;
  const diagnosticSet = kind === 'conversation'
    ? CONVERSATION_DELETION_DIAGNOSTICS
    : PROJECT_MEMORY_DELETION_DIAGNOSTICS;
  if (!statuses.includes(payload.status)
      || !blockersValid
      || !validDeletionCodeItems(payload.diagnostics, diagnosticSet)) return null;
  const semanticStatusValid = (
    payload.status === 'ready'
      ? payload.blockers.length === 0 && payload.diagnostics.length === 0
      : payload.status === 'busy'
        ? payload.blockers.length > 0 && payload.diagnostics.length === 0
        : payload.status === 'unavailable'
          ? payload.diagnostics.length > 0
          : payload.status === 'partial'
            ? payload.blockers.length === 0 && payload.diagnostics.length === 0
            : payload.status === 'completed'
              ? payload.blockers.length === 0
                && payload.diagnostics.length === 0
                && Object.values(payload.affected).every((count) => count === 0)
              : false
  );
  if (!semanticStatusValid) return null;
  const diagnosticCodes = new Set(payload.diagnostics.map((item) => item.code));
  if (!validDeletionTarget(payload.target, kind, targetId, {
    entries: payload.affected.entries,
    diagnosticCodes,
  })) return null;
  try {
    if (utf8ByteLength(JSON.stringify(payload)) > DELETION_RESPONSE_MAX_BYTES) return null;
  } catch (_error) {
    return null;
  }
  return payload;
}

function validateDeletionResult(payload, kind, targetId, expectedRevision) {
  if (!hasExactKeys(payload, DELETION_RESULT_TOP_KEYS)
      || payload.schemaVersion !== 1
      || typeof payload.schemaVersion === 'boolean'
      || payload.mode !== 'read-write'
      || payload.kind !== kind
      || !validDeletionTimestamp(payload.generatedAt)
      || !DELETION_REVISION_PATTERN.test(expectedRevision || '')
      || payload.deletionRevision !== expectedRevision
      || !['completed', 'partial', 'already_absent'].includes(payload.status)
      || !validDeletionCounts(payload.deleted, kind)
      || !validDeletionCounts(payload.remaining, kind)
      || !validDeletionCodeItems(payload.diagnostics, new Set(['deletion_retry_required']))
      || (payload.status === 'partial'
        ? payload.diagnostics.length !== 1
        : payload.diagnostics.length !== 0)) return null;
  const resultStatusValid = payload.status === 'partial'
    ? Object.values(payload.remaining).some((count) => count > 0)
    : Object.values(payload.remaining).every((count) => count === 0)
      && (payload.status !== 'already_absent'
        || Object.values(payload.deleted).every((count) => count === 0));
  if (!resultStatusValid) return null;
  const targetValid = kind === 'conversation'
    ? validDeletionTarget(payload.target, kind, targetId)
    : hasExactKeys(payload.target, ['memoryId', 'scope'])
      && MEMORY_APPROVAL_ID_PATTERN.test(payload.target.memoryId || '')
      && utf8ByteLength(payload.target.memoryId) <= 160
      && payload.target.memoryId === targetId
      && payload.target.scope === 'project';
  if (!targetValid) return null;
  try {
    if (utf8ByteLength(JSON.stringify(payload)) > DELETION_RESPONSE_MAX_BYTES) return null;
  } catch (_error) {
    return null;
  }
  return payload;
}

function conversationDeletionTombstoned(sessionId) {
  return typeof conversationDeletionTombstones !== 'undefined'
    && conversationDeletionTombstones.has(sessionId);
}

function projectMemoryDeletionTombstoned(memoryId) {
  return typeof projectMemoryDeletionTombstones !== 'undefined'
    && projectMemoryDeletionTombstones.has(memoryId);
}

function conversationDeletionConvergenceStore() {
  return typeof conversationDeletionStore !== 'undefined'
    && conversationDeletionStore.convergence
    ? conversationDeletionStore
    : null;
}

function projectMemoryDeletionConvergenceStore() {
  return typeof projectMemoryDeletionStore !== 'undefined'
    && projectMemoryDeletionStore.convergence
    ? projectMemoryDeletionStore
    : null;
}

function createResourceRefreshQueue({ refreshResources, onError = () => {} }) {
  const pending = new Set();
  let fullPending = false;
  let scheduled = false;
  let draining = false;
  let stopped = false;
  let generation = 0;
  let drainPromise = null;

  const hasWork = () => fullPending || pending.size > 0;

  const kick = () => {
    if (stopped || draining || scheduled || !hasWork()) return;
    scheduled = true;
    drainPromise = Promise.resolve().then(drain);
  };

  async function drain() {
    scheduled = false;
    if (stopped || draining) return;
    draining = true;
    const drainGeneration = generation;
    try {
      while (!stopped && generation === drainGeneration && hasWork()) {
        const names = fullPending
          ? [...CHANGE_RESOURCE_NAMES]
          : CHANGE_RESOURCE_NAMES.filter((name) => pending.has(name));
        fullPending = false;
        pending.clear();
        try {
          await refreshResources(names);
        } catch (error) {
          try {
            onError(error);
          } catch (_ignored) {
            // Refresh errors are transport-local and never break queue progress.
          }
        }
      }
    } finally {
      draining = false;
      if (!stopped && hasWork()) kick();
    }
  }

  function enqueue(resourceNames) {
    if (stopped || !Array.isArray(resourceNames) || fullPending) return;
    resourceNames.forEach((name) => {
      if (CHANGE_RESOURCE_NAMES.includes(name)) pending.add(name);
    });
    kick();
  }

  function full() {
    if (stopped) return;
    pending.clear();
    fullPending = true;
    kick();
  }

  function stop() {
    if (stopped) return;
    stopped = true;
    generation += 1;
    pending.clear();
    fullPending = false;
  }

  async function idle() {
    while (scheduled || draining) {
      if (drainPromise) await drainPromise;
      else await Promise.resolve();
    }
  }

  return {
    enqueue,
    full,
    stop,
    idle,
    state: () => ({ pending: [...pending], fullPending, scheduled, draining, stopped }),
  };
}

function createRealtimeRefreshController({
  createEventSource,
  pollingController,
  refreshQueue,
  isVisible,
  schedule,
  cancelSchedule,
  onState,
  graceMs = 3000,
  rebuildDelayMs = 2000,
}) {
  let running = false;
  let source = null;
  let sourceGeneration = 0;
  let graceTimer = null;
  let rebuildTimer = null;
  let fallbackActive = false;
  let handshakeSeen = false;
  let streamEpoch = null;
  let lastSequence = null;
  let publicState = { phase: 'connecting', label: '正在连接', retryMs: null };

  const publish = (phase, label, retryMs = null) => {
    publicState = { phase, label, retryMs };
    onState(publicState);
  };

  const clearGrace = () => {
    if (graceTimer === null) return;
    cancelSchedule(graceTimer);
    graceTimer = null;
  };

  const clearRebuild = () => {
    if (rebuildTimer === null) return;
    cancelSchedule(rebuildTimer);
    rebuildTimer = null;
  };

  const closeSource = () => {
    sourceGeneration += 1;
    const current = source;
    source = null;
    if (current) current.close();
  };

  const beginFallback = (phase) => {
    if (!running || !isVisible()) return;
    if (!fallbackActive) {
      fallbackActive = true;
      refreshQueue.full();
      pollingController.start();
    }
    publish(
      phase,
      phase === 'reconnecting' ? 'SSE 重连中（轮询备用）' : '轮询备用',
      phase === 'reconnecting' ? rebuildDelayMs : null,
    );
  };

  const recoverRealtime = () => {
    clearGrace();
    clearRebuild();
    if (fallbackActive) pollingController.stop();
    fallbackActive = false;
    publish('realtime', '实时（SSE）');
  };

  const scheduleRebuild = () => {
    if (!running || !isVisible() || rebuildTimer !== null || source !== null) return;
    rebuildTimer = schedule(() => {
      rebuildTimer = null;
      openSource(true);
    }, rebuildDelayMs);
  };

  const malformedEvent = (eventSource, eventGeneration) => {
    if (!running || source !== eventSource || sourceGeneration !== eventGeneration) return;
    clearGrace();
    closeSource();
    handshakeSeen = false;
    streamEpoch = null;
    lastSequence = null;
    beginFallback('reconnecting');
    scheduleRebuild();
  };

  const receive = (eventType, event, eventSource, eventGeneration) => {
    if (!running || source !== eventSource || sourceGeneration !== eventGeneration) return;
    const parsed = parseDashboardEvent(eventType, event.lastEventId, event.data);
    if (!parsed) {
      malformedEvent(eventSource, eventGeneration);
      return;
    }
    if (parsed.kind === 'ready') {
      streamEpoch = parsed.epoch;
      lastSequence = parsed.sequence;
      handshakeSeen = true;
      recoverRealtime();
      refreshQueue.full();
      return;
    }
    if (parsed.kind === 'reset') {
      streamEpoch = parsed.epoch;
      lastSequence = parsed.sequence;
      handshakeSeen = true;
      recoverRealtime();
      refreshQueue.full();
      return;
    }
    if (!handshakeSeen || streamEpoch !== parsed.epoch || lastSequence === null) {
      malformedEvent(eventSource, eventGeneration);
      return;
    }
    if (parsed.sequence <= lastSequence) return;
    const gap = parsed.sequence !== lastSequence + 1n;
    lastSequence = parsed.sequence;
    recoverRealtime();
    if (gap) refreshQueue.full();
    else refreshQueue.enqueue(parsed.resources);
  };

  function openSource(rebuilding = false) {
    if (!running || !isVisible() || source !== null) return;
    clearGrace();
    if (!fallbackActive) publish('connecting', '正在连接');
    else if (rebuilding) publish('reconnecting', 'SSE 重连中（轮询备用）', rebuildDelayMs);
    let nextSource;
    try {
      nextSource = createEventSource();
    } catch (_error) {
      beginFallback('polling');
      return;
    }
    if (!nextSource || typeof nextSource.addEventListener !== 'function') {
      try {
        nextSource?.close();
      } catch (_error) {
        // An unsupported EventSource implementation is ordinary fallback.
      }
      beginFallback('polling');
      return;
    }
    source = nextSource;
    const eventGeneration = sourceGeneration;
    nextSource.addEventListener('stream.ready', (event) => receive('stream.ready', event, nextSource, eventGeneration));
    nextSource.addEventListener('resources.changed', (event) => receive('resources.changed', event, nextSource, eventGeneration));
    nextSource.addEventListener('stream.reset', (event) => receive('stream.reset', event, nextSource, eventGeneration));
    nextSource.onopen = () => {
      if (!running || source !== nextSource || sourceGeneration !== eventGeneration) return;
      if (handshakeSeen) recoverRealtime();
    };
    nextSource.onerror = () => {
      if (!running || source !== nextSource || sourceGeneration !== eventGeneration) return;
      clearGrace();
      beginFallback('reconnecting');
      if (nextSource.readyState === 2) {
        closeSource();
        handshakeSeen = false;
        streamEpoch = null;
        lastSequence = null;
        scheduleRebuild();
      }
    };
    graceTimer = schedule(() => {
      graceTimer = null;
      if (running && source === nextSource && sourceGeneration === eventGeneration && !handshakeSeen) {
        beginFallback('polling');
      }
    }, graceMs);
  }

  function visibilityChanged() {
    if (!running) return;
    if (!isVisible()) {
      clearGrace();
      clearRebuild();
      closeSource();
      pollingController.stop();
      fallbackActive = false;
      handshakeSeen = false;
      streamEpoch = null;
      lastSequence = null;
      publish('paused', '已暂停（页面不可见）');
      return;
    }
    handshakeSeen = false;
    streamEpoch = null;
    lastSequence = null;
    publish('connecting', '正在连接');
    openSource();
  }

  function pollingStateChanged(pollState) {
    if (!running || !fallbackActive || !isVisible()) return;
    if (pollState?.phase === 'stale') {
      publish('stale', '轮询备用 · 数据可能过期', pollState.retryMs ?? null);
    } else if (source) {
      publish('reconnecting', 'SSE 重连中（轮询备用）', pollState?.retryMs ?? null);
    } else {
      publish('polling', '轮询备用', pollState?.retryMs ?? null);
    }
  }

  function start() {
    if (running) return;
    running = true;
    visibilityChanged();
  }

  function stop() {
    if (!running) return;
    running = false;
    clearGrace();
    clearRebuild();
    closeSource();
    pollingController.stop();
    refreshQueue.stop();
    fallbackActive = false;
  }

  return {
    start,
    stop,
    visibilityChanged,
    pollingStateChanged,
    state: () => ({
      ...publicState,
      running,
      fallbackActive,
      handshakeSeen,
      hasEventSource: source !== null,
    }),
  };
}

function createLiveRefreshController({
  fetchChanges,
  refreshResources,
  isVisible,
  schedule,
  cancelSchedule,
  createAbortController,
  onState,
}) {
  let running = false;
  let timerId = null;
  let inFlight = false;
  let generation = 0;
  let abortController = null;
  let previousResources = null;
  let consecutiveFailures = 0;
  let publicState = { phase: 'starting', label: '正在连接', retryMs: null };

  const publish = (phase, label, retryMs = null) => {
    publicState = { phase, label, retryMs };
    onState(publicState);
  };

  const cancelTimer = () => {
    if (timerId === null) return;
    cancelSchedule(timerId);
    timerId = null;
  };

  const plan = (delay) => {
    if (!running || !isVisible()) return;
    cancelTimer();
    timerId = schedule(pollNow, delay);
  };

  async function pollNow() {
    timerId = null;
    if (!running || !isVisible() || inFlight) return;
    const requestGeneration = generation + 1;
    generation = requestGeneration;
    inFlight = true;
    abortController = createAbortController();
    try {
      const payload = await fetchChanges({ signal: abortController.signal });
      if (requestGeneration !== generation || !running || !isVisible()) return;
      if (!validChangeSnapshot(payload)) throw new Error('change feed contract mismatch');
      const changed = previousResources === null
        ? []
        : CHANGE_RESOURCE_NAMES.filter((name) => {
          const previous = previousResources[name];
          const current = payload.resources[name];
          return previous.revision !== current.revision || previous.status !== current.status;
        });
      previousResources = Object.fromEntries(CHANGE_RESOURCE_NAMES.map((name) => [name, { ...payload.resources[name] }]));
      consecutiveFailures = 0;
      const incomplete = CHANGE_RESOURCE_NAMES.some((name) => payload.resources[name].status !== 'live');
      publish(incomplete ? 'stale' : 'live', incomplete ? '数据可能过期' : '实时');
      if (changed.length) await refreshResources(changed);
      if (requestGeneration !== generation || !running || !isVisible()) return;
      plan(payload.pollAfterMs);
    } catch (_error) {
      if (requestGeneration !== generation || !running || !isVisible()) return;
      consecutiveFailures += 1;
      const retryMs = LIVE_RETRY_DELAYS_MS[Math.min(consecutiveFailures - 1, LIVE_RETRY_DELAYS_MS.length - 1)];
      const stale = consecutiveFailures >= LIVE_RETRY_DELAYS_MS.length;
      publish(stale ? 'stale' : 'reconnecting', stale ? '数据可能过期' : '正在重连', retryMs);
      plan(retryMs);
    } finally {
      if (requestGeneration === generation) {
        inFlight = false;
        abortController = null;
      }
    }
  }

  function visibilityChanged() {
    if (!running) return;
    if (!isVisible()) {
      generation += 1;
      cancelTimer();
      if (abortController) abortController.abort();
      abortController = null;
      inFlight = false;
      publish('paused', '已暂停（页面不可见）');
      return;
    }
    publish('starting', '正在连接');
    plan(0);
  }

  function start() {
    if (running) return;
    running = true;
    visibilityChanged();
  }

  function stop() {
    if (!running) return;
    running = false;
    generation += 1;
    cancelTimer();
    if (abortController) abortController.abort();
    abortController = null;
    inFlight = false;
  }

  return {
    start,
    stop,
    pollNow,
    visibilityChanged,
    state: () => ({ ...publicState, running, inFlight, consecutiveFailures }),
  };
}

function captureLiveRefreshInteractionState() {
  const input = document.querySelector('#message');
  const main = document.querySelector('main');
  const view = document.querySelector('#view');
  const chatLog = document.querySelector('#chat-log');
  return {
    messageFocused: document.activeElement === input,
    selectionStart: input?.selectionStart ?? null,
    selectionEnd: input?.selectionEnd ?? null,
    selectionDirection: input?.selectionDirection ?? 'none',
    mainScrollTop: main?.scrollTop ?? 0,
    viewScrollTop: view?.scrollTop ?? 0,
    chatScrollTop: chatLog?.scrollTop ?? 0,
  };
}

function restoreLiveRefreshInteractionState(saved) {
  if (!saved) return;
  const input = document.querySelector('#message');
  const main = document.querySelector('main');
  const view = document.querySelector('#view');
  const chatLog = document.querySelector('#chat-log');
  if (main?.scrollTop === 0) main.scrollTop = saved.mainScrollTop;
  if (view?.scrollTop === 0) view.scrollTop = saved.viewScrollTop;
  if (chatLog?.scrollTop === 0) chatLog.scrollTop = saved.chatScrollTop;
  const focusWasReset = document.activeElement === document.body
    || document.activeElement === document.documentElement;
  if (saved.messageFocused && focusWasReset && input && !input.disabled) {
    input.focus({ preventScroll: true });
    if (saved.selectionStart !== null && saved.selectionEnd !== null) {
      const end = input.value.length;
      input.setSelectionRange(
        Math.min(saved.selectionStart, end),
        Math.min(saved.selectionEnd, end),
        saved.selectionDirection,
      );
    }
  }
}

async function refreshRunsFromChangeFeed() {
  const selectedRunId = runDetailStore.runId;
  await loadRuns(false);
  if (currentRoute()[0] !== 'runs' || runDetailStore.runId !== selectedRunId) return;
  if (selectedRunId && runsStore.items.some((run) => run.id === selectedRunId)) {
    await loadRunDetail(selectedRunId, false, true);
  } else if (selectedRunId) {
    resetRunDetail();
    renderRouteOnly('runs');
  }
}

function invalidateForNextRoute(store, requestKeys) {
  requestKeys.forEach((key) => { store[key] += 1; });
  store.phase = 'idle';
}

async function refreshChangedResources(resourceNames) {
  const changed = new Set(resourceNames.filter((name) => CHANGE_RESOURCE_NAMES.includes(name)));
  if (!changed.size) return;
  const dataHealthInvalidated = changed.size > 0;
  const interaction = captureLiveRefreshInteractionState();
  let completedTurnHandled = false;
  try {
    if (changed.has('turns') && chatStore.activeTurnId) {
      const turnId = chatStore.activeTurnId;
      await checkActiveTurnStatus(true);
      completedTurnHandled = chatStore.activeTurnId !== turnId && chatStore.phase === 'success';
    }
    changed.delete('turns');
    if (completedTurnHandled) {
      changed.delete('runs');
      changed.delete('sessions');
    }

    const [view, sub] = currentRoute();
    const tasks = [];
    const snapshotChanged = [...changed].some((name) => name !== 'turns');
    if (snapshotChanged && !completedTurnHandled) tasks.push(loadDashboardSnapshot());

    if (changed.has('sessions')) {
      tasks.push(loadSessions(false, true));
      if (conversationDeletionStore.targetId
          && !['submitting', 'reconciling', 'completed'].includes(conversationDeletionStore.phase)) {
        tasks.push(loadDeletionPreview(conversationDeletionStore, 'sse'));
      }
    }

    if (changed.has('runs')) {
      if (view === 'runs') tasks.push(refreshRunsFromChangeFeed());
      else {
        invalidateForNextRoute(runsStore, ['requestId']);
        if (view === 'overview') tasks.push(refreshObservatoryFromChangeFeed());
      }

      const runtimeVisible = (view === 'memory' && ['retrieval', 'injection'].includes(sub))
        || (view === 'skills' && sub === 'routing');
      if (runtimeVisible) tasks.push(loadRuntimeTrace(true));
      else invalidateForNextRoute(runtimeTraceStore, ['listRequestId', 'detailRequestId']);

      if (view === 'ops' || (view === 'memory' && sub === 'lifecycle')) tasks.push(loadOps());
      else invalidateForNextRoute(opsStore, ['requestId']);
    }

    if (changed.has('memory')) {
      if (view === 'memory' && !['retrieval', 'injection'].includes(sub)) tasks.push(loadMemory(false));
      else invalidateForNextRoute(memoryStore, ['requestId']);
      if ((view === 'memory' && sub === 'approvals') || memoryApprovalStore.phase !== 'idle') {
        tasks.push(loadMemoryApprovals());
      } else {
        invalidateForNextRoute(memoryApprovalStore, ['requestId']);
      }
      if (projectMemoryDeletionStore.targetId
          && !['submitting', 'reconciling', 'completed'].includes(projectMemoryDeletionStore.phase)) {
        tasks.push(loadDeletionPreview(projectMemoryDeletionStore, 'sse'));
      }
    }

    if (changed.has('skills')) {
      if (view === 'skills' && sub !== 'routing') tasks.push(loadSkills(false));
      else invalidateForNextRoute(skillsStore, ['requestId']);
    }

    if (changed.has('connections')) {
      if (view === 'connections') tasks.push(loadConnections());
      else invalidateForNextRoute(connectionsStore, ['requestId']);
    }
    if (changed.has('permissions')) tasks.push(loadPendingPermissions());
    if (dataHealthInvalidated
        && (view === 'system' || dataHealthStore.phase !== 'idle')) {
      tasks.push(loadDataHealth());
    }
    await Promise.all(tasks);
  } finally {
    restoreLiveRefreshInteractionState(interaction);
  }
}

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;',
})[char]);
const money = (value) => `$${Number(value).toFixed(value < 0.01 ? 4 : 2)}`;
const tokens = (value) => Number(value).toLocaleString('zh-CN');
const statusText = { queued: '排队', running: '运行中', completed: '完成', interrupted: '已中断', cancelled: '已取消', cancel_requested: '取消待处理', complete: '完成', waiting: '等待', failed: '失败', starting: '启动中', ready: '就绪', active: '活跃', saved: '已保存', archived: '归档', connected: '已连接', configured: '已配置', degraded: '降级', disabled: '未启用', idle: '空闲', loaded: '已加载', injected: '已注入', rendered: '已渲染', selected: '已选择', suppressed: '已抑制', pending: '待审批', approved: '已批准', safe: '安全', live: '实时', stale: '配置态', unavailable: '未接入', error: '错误', partial: '部分可用', 'read-only': '只读' };
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
  return `<div class="tiles">${items.map(([value, label, tone = '']) => `<div class="tile"><b class="${tone}">${esc(value)}</b><span>${esc(label)}</span></div>`).join('')}</div>`;
}

function formatCount(value) {
  return Number.isInteger(value) && value >= 0 ? value.toLocaleString('zh-CN') : '—';
}

function formatDuration(value) {
  if (!Number.isInteger(value) || value < 0) return '—';
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(value < 10000 ? 2 : 1)} s`;
}

function formatNanoUsd(value) {
  return typeof MiniCodeCost === 'object' && typeof MiniCodeCost.formatNanoUsd === 'function'
    ? MiniCodeCost.formatNanoUsd(value)
    : '—';
}

function costCoverageText(metric) {
  const coverage = metric?.coverage || {};
  return `Priced ${formatCount(coverage.pricedCalls)} / ${formatCount(coverage.completedCalls)} completed · unavailable ${formatCount(coverage.unavailableCalls)} · missing ${formatCount(coverage.missingCalls)} · failed attempts ${formatCount(coverage.failedAttempts)}`;
}

function costMetricTile(metric) {
  const amount = formatNanoUsd(metric?.value?.amountNanoUsd);
  const status = metric?.status || 'unavailable';
  const label = status === 'complete'
    ? 'Catalog-calculated cost · retained complete'
    : status === 'partial'
      ? 'Observed cost · partial coverage'
      : 'Cost · unavailable';
  return [amount, label, status];
}

function costMetricDetail(metric) {
  const value = metric?.value;
  const coverage = costCoverageText(metric);
  if (!value) return `No priced Cost observation · ${coverage}`;
  const catalogs = Array.isArray(value.catalogIds) && value.catalogIds.length ? value.catalogIds.join(', ') : 'unavailable';
  return `Catalog-calculated Cost ${formatNanoUsd(value.amountNanoUsd)} observed · Provider-usage component ${formatNanoUsd(value.providerUsageNanoUsd)} · Estimated-usage component ${formatNanoUsd(value.estimatedUsageNanoUsd)} · Catalog ${catalogs} · ${coverage}`;
}

function costBreakdownTable(title, rows, key) {
  if (!Array.isArray(rows) || !rows.length) return '';
  return `<article class="cost-breakdown"><b>${esc(title)}</b>${rows.map((item) => `<div><code>${esc(item[key])}</code><span>${esc(formatNanoUsd(item.amountNanoUsd))}<small>${esc(formatCount(item.pricedCalls))} priced</small></span></div>`).join('')}</article>`;
}

function renderCostBreakdown(cost, breakdown) {
  const reasons = Array.isArray(breakdown?.unavailableReasons) && breakdown.unavailableReasons.length
    ? `<div class="cost-reasons">${breakdown.unavailableReasons.map((item) => `<span><code>${esc(item.reason)}</code> · ${esc(formatCount(item.calls))}</span>`).join('')}</div>`
    : '<div class="cost-reasons"><span>No unavailable Cost reasons in the retained scope.</span></div>';
  return `<h2>Canonical cost</h2><div class="card cost-summary"><div>${statusPill(cost?.status || 'unavailable')}<b>${esc(formatNanoUsd(cost?.value?.amountNanoUsd))} observed</b></div><p>${esc(costMetricDetail(cost))}</p><div class="unavailable-list"><span>Scope · ${esc(cost?.coverage?.scope || 'retained-run-journal')}</span><span>Historical · ${esc(cost?.coverage?.historical || 'partial')}</span><span>Limited · ${cost?.coverage?.limited === true ? 'yes' : 'no'}</span><span>Invalid events · ${esc(formatCount(cost?.coverage?.invalidEvents))}</span></div></div><div class="cost-breakdown-grid">${costBreakdownTable('Quality', breakdown?.quality, 'quality')}${costBreakdownTable('Catalog observations', breakdown?.catalogs, 'catalogId')}${costBreakdownTable('Canonical models', breakdown?.models, 'catalogModelKey')}${costBreakdownTable('Run sources', breakdown?.sources, 'source')}</div><h3>Unpriced coverage</h3>${reasons}`;
}

function toolMetricTile(metric) {
  const status = metric?.status || 'unavailable';
  return [formatCount(metric?.value?.observedCalls), status === 'unavailable' ? 'Tool calls · unavailable' : 'Observed Tool calls', status];
}

function failureMetricTile(metric) {
  const status = metric?.status || 'unavailable';
  return [formatCount(metric?.value?.affectedRuns), status === 'unavailable' ? 'Failures · unavailable' : 'Runs with observed failures', status];
}

function toolMetricDetail(metric) {
  const value = metric?.value;
  const coverage = metric?.coverage || {};
  if (!value) return `No canonical Tool observation · historical ${coverage.historical || 'partial'} · never shown as zero`;
  return `Observed ${formatCount(value.observedCalls)} · completed ${formatCount(value.completedCalls)} · paired ${formatCount(value.pairedCalls)} · success ${formatCount(value.successfulCalls)} · error ${formatCount(value.errorCalls)} · unique tools ${formatCount(value.uniqueTools)} · dangling ${formatCount(coverage.danglingStarts)} · unpaired ${formatCount(coverage.unpairedFinishes)} · invalid ${formatCount(coverage.invalidEvents)}`;
}

function failureMetricDetail(metric) {
  const value = metric?.value;
  const coverage = metric?.coverage || {};
  if (!value) return `No recorded lifecycle / Tool / Model failure observation · historical ${coverage.historical || 'partial'}`;
  return `Affected Runs ${formatCount(value.affectedRuns)} · Tool errors ${formatCount(value.toolErrors)} · Model failures ${formatCount(value.modelFailures)} · Run failures ${formatCount(value.runFailures)} · interruptions ${formatCount(value.interruptedRuns)} · cancellations ${formatCount(value.cancelledRuns)}`;
}


function observationBreakdownTable(title, rows, key, summary) {
  if (!Array.isArray(rows) || !rows.length) return '';
  return `<article class="observation-breakdown"><b>${esc(title)}</b>${rows.map((item) => `<div><code>${esc(item[key])}</code><span>${esc(summary(item))}</span></div>`).join('')}</article>`;
}

function renderToolBreakdown(tools, breakdown) {
  const coverage = tools?.coverage || {};
  const toolRows = observationBreakdownTable('Tool names', breakdown?.tools, 'toolName', (item) => `${formatCount(item.observedCalls)} observed · ${formatCount(item.completedCalls)} completed · ${formatCount(item.successfulCalls)} success · ${formatCount(item.errorCalls)} error · ${formatCount(item.incompleteCalls)} incomplete`);
  const outcomeRows = observationBreakdownTable('Outcomes', breakdown?.outcomes, 'outcome', (item) => `${formatCount(item.calls)} calls`);
  const sourceRows = observationBreakdownTable('Run sources', breakdown?.sources, 'source', (item) => `${formatCount(item.observedCalls)} observed · ${formatCount(item.errorCalls)} error`);
  return `<h2>Canonical Tool observations</h2><div class="card observation-summary"><div>${statusPill(tools?.status || 'unavailable')}<b>${esc(formatCount(tools?.value?.observedCalls))} observed calls</b></div><p>${esc(toolMetricDetail(tools))}</p><div class="unavailable-list"><span>Pairing · same Run only</span><span>No duration inference</span><span>Limited · ${coverage.limited === true ? 'yes' : 'no'}</span><span>Conflicts · ${esc(formatCount(coverage.conflictingOperations))}</span><span>Orphans · ${esc(formatCount(coverage.orphanFinishes))}</span></div></div><div class="observation-breakdown-grid">${toolRows}${outcomeRows}${sourceRows}</div>`;
}

function renderFailureBreakdown(failures, breakdown) {
  const coverage = failures?.coverage || {};
  const categoryLabels = { tool_errors: 'Tool errors', model_failures: 'Model attempt failures', run_failures: 'Run failures', interruptions: 'Interruptions', cancellations: 'Cancellations' };
  const categories = Array.isArray(breakdown?.categories) ? breakdown.categories.map((item) => ({ ...item, label: categoryLabels[item.category] || item.category })) : [];
  const categoryRows = observationBreakdownTable('Separate categories', categories, 'label', (item) => `${formatCount(item.count)} observations`);
  const modelRows = observationBreakdownTable('Model failure kinds', breakdown?.modelFailureKinds, 'failureKind', (item) => `${formatCount(item.attempts)} attempts`);
  const sourceRows = observationBreakdownTable('Run sources', breakdown?.sources, 'source', (item) => `${formatCount(item.affectedRuns)} affected · ${formatCount(item.interruptedRuns)} interrupted · ${formatCount(item.cancelledRuns)} cancelled`);
  return `<h2>Observed failures</h2><div class="card observation-summary"><div>${statusPill(failures?.status || 'unavailable')}<b>${esc(formatCount(failures?.value?.affectedRuns))} affected Runs</b></div><p>${esc(failureMetricDetail(failures))}</p><div class="unavailable-list"><span>Categories remain separate</span><span>Interruptions / cancellations are not failures</span><span>Observed Runs · ${esc(formatCount(coverage.observedRuns))}</span><span>Limited · ${coverage.limited === true ? 'yes' : 'no'}</span><span>Invalid events · ${esc(formatCount(coverage.invalidEvents))}</span></div></div><div class="observation-breakdown-grid">${categoryRows}${modelRows}${sourceRows}</div>`;
}

function formatUsageBuckets(usage) {
  if (!usage || usage.source === 'unavailable') return 'Token buckets unavailable';
  return `Input ${formatCount(usage.inputTokens)} · Output ${formatCount(usage.outputTokens)} · Cache read ${formatCount(usage.cacheReadTokens)} · Cache create ${formatCount(usage.cacheCreationTokens)}`;
}

function contextMetricTile(metric) {
  const status = metric?.status || 'unavailable';
  return [formatCount(metric?.value?.observedCompactions), status === 'unavailable' ? 'Context unavailable' : 'Context compactions', status];
}

function workingMemoryMetricTile(metric) {
  const status = metric?.status || 'unavailable';
  return [formatCount(metric?.value?.observedSnapshots), status === 'unavailable' ? 'WM unavailable' : 'WM observations', status];
}

function contextMetricDetail(context, recovery) {
  const cv = context?.value;
  const rv = recovery?.value;
  if (!cv && !rv) return 'Context observations unavailable; instrumentation partial and historical partial.';
  const parts = [];
  if (cv) parts.push(`${formatCount(cv.observedCompactions)} compactions`, `${formatCount(cv.messagesRemoved)} messages removed`, `${formatCount(cv.knownTokensFreed)} known estimated tokens freed`);
  if (rv) parts.push(`${formatCount(rv.recoveredAttempts)} / ${formatCount(rv.completedAttempts)} completed recoveries succeeded`);
  return `${parts.join(' · ')} · integrity ${context?.coverage?.integrity || recovery?.coverage?.integrity || 'unknown'} · instrumentation partial · historical partial`;
}

function workingMemoryMetricDetail(metric) {
  const value = metric?.value;
  if (!value) return 'WorkingMemory observations unavailable; no global or current process state is inferred.';
  const latest = value.latestObservation;
  if (!latest) return `${formatCount(value.observedSnapshots)} process-local snapshots retained; latest observation unavailable.`;
  return `${formatCount(value.observedSnapshots)} snapshots across ${formatCount(value.runsWithSnapshots)} Runs · latest retained process-local snapshot ${formatCount(latest.entries)} / ${formatCount(latest.maxEntries)} entries, ${formatCount(latest.protectedTokens)} / ${formatCount(latest.maxTokens)} estimated tokens · ${latest.runSource || 'unknown'} · not global/current`;
}

function renderContextBreakdown(metric, recovery, breakdown) {
  const cv = metric?.value;
  const rv = recovery?.value;
  const pathRows = (breakdown?.paths || []).map((item) => `<tr><td><code>${esc(item.path)}</code></td><td>${esc(formatCount(item.count))}</td><td>${esc(formatCount(item.messagesRemoved))}</td><td>${esc(formatCount(item.knownTokensFreed))}</td><td>${esc(formatCount(item.tokenUnknown))}</td></tr>`);
  const outcomeRows = (breakdown?.recoveryOutcomes || []).map((item) => `<tr><td><code>${esc(item.outcome)}</code></td><td>${esc(formatCount(item.count))}</td></tr>`);
  return `<h2>Context / Recovery observations</h2>${metricTiles([
    contextMetricTile(metric),
    [formatCount(cv?.knownTokensFreed), 'Known estimated tokens freed', metric?.status || 'unavailable'],
    [formatCount(cv?.messagesRemoved), 'Messages removed', metric?.status || 'unavailable'],
    [formatCount(rv?.recoveredAttempts), 'Recovered attempts', recovery?.status || 'unavailable'],
  ])}<div class="card usage-summary"><div><b>Context coverage</b>${statusPill(metric?.status || 'unavailable')}${statusPill(recovery?.status || 'unavailable')}</div><p>${esc(contextMetricDetail(metric, recovery))}</p><div class="unavailable-list"><span>integrity · ${esc(metric?.coverage?.integrity || 'unknown')}</span><span>instrumentation · partial</span><span>historical · partial</span><span>dangling · ${esc(metric?.coverage?.danglingRecoveries ?? 0)}</span><span>orphan · ${esc(metric?.coverage?.orphanEvents ?? 0)}</span><span>duplicate · ${esc(metric?.coverage?.duplicateEvents ?? 0)}</span><span>conflict · ${esc(metric?.coverage?.conflictingOperations ?? 0)}</span><span>invalid · ${esc(metric?.coverage?.invalidEvents ?? 0)}</span><span>limited · ${esc(metric?.coverage?.limited ?? false)}</span></div></div><h2>Context breakdown</h2>${table(['Path', 'Count', 'Messages removed', 'Known tokens freed', 'Unknown token count'], pathRows)}${outcomeRows.length ? `<h2>Recovery outcomes</h2>${table(['Outcome', 'Count'], outcomeRows)}` : ''}`;
}

function renderWorkingMemoryRuntime(metric) {
  const value = metric?.value;
  const latest = value?.latestObservation;
  return `<h2>WorkingMemory process-local observations</h2>${metricTiles([
    workingMemoryMetricTile(metric),
    [formatCount(value?.runsWithSnapshots), 'Runs with snapshots', metric?.status || 'unavailable'],
    [latest ? `${formatCount(latest.entries)} / ${formatCount(latest.maxEntries)}` : '—', 'Latest retained entries', metric?.status || 'unavailable'],
    [latest ? `${formatCount(latest.protectedTokens)} / ${formatCount(latest.maxTokens)}` : '—', 'Latest retained estimated tokens', metric?.status || 'unavailable'],
  ])}<div class="card working-card"><div><b>Latest retained process-local snapshot</b>${statusPill(metric?.status || 'unavailable')}</div><p>${esc(workingMemoryMetricDetail(metric))}</p>${latest ? `<small>${latest.runId ? `<code>${esc(latest.runId)}</code> · ` : ''}${esc(latest.runSource || 'unknown')} · observed ${esc(formatSnapshotTime(latest.observedAt))}</small>` : ''}<div class="unavailable-list"><span>not global</span><span>not current process state</span><span>not a compaction-protection guarantee</span><span>summedAcrossRuns · ${esc(metric?.coverage?.summedAcrossRuns ?? false)}</span></div></div>`;
}

function formatSnapshotTime(value) {
  if (!value) return '未知';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? '未知' : parsed.toLocaleString('zh-CN', { hour12: false });
}

function renderSnapshotSources(snapshot) {
  return `<div class="source-grid">${Object.entries(snapshot.sources).map(([name, source]) => `<div class="source-card">
    <div><code>${esc(name)}</code>${statusPill(source.status)}</div>
    <small>${source.updatedAt ? `updated ${esc(formatSnapshotTime(source.updatedAt))}` : 'no live timestamp'}</small>
    ${source.message ? `<p>${esc(source.message)}</p>` : ''}
  </div>`).join('')}</div>`;
}

function renderSnapshotDiagnostics(snapshot) {
  if (!snapshot.diagnostics.length) return '';
  return `<h2>局部诊断</h2><div class="stack">${snapshot.diagnostics.map((item) => `<div class="card snapshot-warning"><b>${esc(item.source)} · ${esc(item.code)}</b><p>${esc(item.message)}</p></div>`).join('')}</div>`;
}

function updateSnapshotNavigation(snapshot) {
  const overview = snapshot.overview;
  const values = {
    runs: overview.runs.count ?? '—',
    sessions: overview.sessions.count ?? '—',
    memory: overview.memory.totalCount ?? `${overview.memory.knownCount}+`,
    skills: overview.skills.count ?? '—',
    connections: overview.connections.mcp.configuredCount ?? '—',
    usage: (overview.usage.providerCalls || 0) + (overview.usage.estimatedCalls || 0) + (overview.usage.unavailableCalls || 0) || '—',
  };
  Object.entries(values).forEach(([name, value]) => {
    const target = document.querySelector(`[data-count="${name}"]`);
    if (target) target.textContent = String(value);
  });
  document.querySelector('#nav-source-state').textContent = `read-only · ${snapshot.status} · ${snapshot.workspace.name}`;
}

function renderOverviewOnly() {
  if (currentRoute()[0] !== 'overview') return;
  document.querySelector('#view').innerHTML = VIEWS.overview();
  tickMeta();
}

async function loadDashboardSnapshot() {
  const requestId = snapshotStore.requestId + 1;
  snapshotStore.requestId = requestId;
  snapshotStore.error = null;
  if (!snapshotStore.data) snapshotStore.phase = 'loading';
  renderOverviewOnly();
  try {
    const response = await fetch('/api/v1/snapshot', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('snapshot request failed');
    const snapshot = await response.json();
    if (snapshot.schemaVersion !== 1 || !snapshot.overview || !snapshot.sources) throw new Error('snapshot contract mismatch');
    if (requestId !== snapshotStore.requestId) return;
    snapshotStore.data = snapshot;
    snapshotStore.phase = snapshot.status === 'partial' ? 'partial' : 'loaded';
    snapshotStore.refreshedAt = Date.now();
    updateSnapshotNavigation(snapshot);
  } catch (_error) {
    if (requestId !== snapshotStore.requestId) return;
    snapshotStore.phase = 'error';
    snapshotStore.error = '无法读取 Dashboard Snapshot。静态页面仍可使用。';
    document.querySelector('#nav-source-state').textContent = 'read-only · snapshot error';
  }
  renderOverviewOnly();
}

function refreshDashboardSnapshot() {
  return loadDashboardSnapshot();
}

function renderRouteOnly(viewName) {
  const [view, sub] = currentRoute();
  if (view !== viewName) return;
  document.querySelector('#view').innerHTML = VIEWS[view](DATA, sub);
  tickMeta();
}

function assertPageContract(payload, requiredKey) {
  if (payload?.schemaVersion !== 1 || payload?.mode !== 'read-only' || !payload?.source || !payload?.page || !Array.isArray(payload?.[requiredKey])) {
    throw new Error('page contract mismatch');
  }
}

async function loadObservatoryRunDetail(runId, listRequestId) {
  const detailRequestId = observatoryStore.detailRequestId + 1;
  observatoryStore.detailRequestId = detailRequestId;
  observatoryStore.selectedRunId = runId;
  observatoryStore.detail = null;
  observatoryStore.detailPhase = 'loading';
  renderOverviewOnly();
  try {
    const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}?limit=50`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('observatory run detail request failed');
    const payload = await response.json();
    assertPageContract(payload, 'events');
    if (!payload.run || !payload.metrics
        || listRequestId !== observatoryStore.listRequestId
        || detailRequestId !== observatoryStore.detailRequestId
        || runId !== observatoryStore.selectedRunId) return;
    if (conversationDeletionTombstoned(payload.run.sessionId)) return;
    observatoryStore.detail = payload;
    observatoryStore.detailPhase = payload.source.status === 'error' ? 'partial' : 'loaded';
  } catch (_error) {
    if (listRequestId !== observatoryStore.listRequestId
        || detailRequestId !== observatoryStore.detailRequestId
        || runId !== observatoryStore.selectedRunId) return;
    observatoryStore.detailPhase = 'error';
    observatoryStore.error = '最新 Run 的事件详情暂时不可用。';
  }
  renderOverviewOnly();
}

async function loadObservatory() {
  const listRequestId = observatoryStore.listRequestId + 1;
  observatoryStore.listRequestId = listRequestId;
  observatoryStore.detailRequestId += 1;
  observatoryStore.error = null;
  observatoryStore.phase = observatoryStore.items.length ? 'partial' : 'loading';
  observatoryStore.detailPhase = observatoryStore.detail ? 'partial' : 'idle';
  renderOverviewOnly();
  try {
    const response = await fetch('/api/v1/runs?limit=6', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok) throw new Error('observatory runs request failed');
    const payload = await response.json();
    assertPageContract(payload, 'items');
    if (listRequestId !== observatoryStore.listRequestId) return;
    const items = payload.items.filter(
      (item) => !conversationDeletionTombstoned(item.sessionId),
    );
    const selected = items.find(
      (item) => ['running', 'cancel_requested', 'queued'].includes(item.status),
    ) || items[0] || null;
    observatoryStore.items = items;
    observatoryStore.source = payload.source;
    observatoryStore.diagnostics = Array.isArray(payload.diagnostics)
      ? payload.diagnostics
      : [];
    observatoryStore.selectedRunId = selected?.id || null;
    observatoryStore.detail = null;
    observatoryStore.phase = items.length
      ? (payload.source.status === 'error' ? 'partial' : 'loaded')
      : 'empty';
    observatoryStore.detailPhase = selected ? 'loading' : 'empty';
    renderOverviewOnly();
    if (selected) await loadObservatoryRunDetail(selected.id, listRequestId);
  } catch (_error) {
    if (listRequestId !== observatoryStore.listRequestId) return;
    observatoryStore.phase = observatoryStore.items.length ? 'partial' : 'error';
    observatoryStore.detailPhase = observatoryStore.detail ? 'partial' : 'error';
    observatoryStore.error = '无法读取最新 Run 的安全观测摘要。';
    renderOverviewOnly();
  }
}

function refreshObservatoryFromChangeFeed() {
  return loadObservatory();
}

function resetRunDetail() {
  runDetailStore.requestId += 1;
  runDetailStore.phase = 'idle';
  runDetailStore.runId = null;
  runDetailStore.data = null;
  runDetailStore.error = null;
  runDetailStore.loadingMore = false;
}

async function loadRuns(append = false) {
  const requestId = runsStore.requestId + 1;
  const filterKey = JSON.stringify(runsStore.filters);
  runsStore.requestId = requestId;
  runsStore.error = null;
  if (append) runsStore.loadingMore = true;
  else runsStore.phase = runsStore.items.length ? 'partial' : 'loading';
  renderRouteOnly('runs');
  const params = new URLSearchParams({ limit: '20' });
  Object.entries(runsStore.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  if (append && runsStore.page?.nextCursor) params.set('cursor', runsStore.page.nextCursor);
  try {
    const response = await fetch(`/api/v1/runs?${params.toString()}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('runs request failed');
    const payload = await response.json();
    assertPageContract(payload, 'items');
    if (!payload.coverage || !payload.summary) throw new Error('runs contract mismatch');
    if (requestId !== runsStore.requestId || filterKey !== JSON.stringify(runsStore.filters)) return;
    const deletionConvergence = conversationDeletionConvergenceStore();
    if (deletionConvergence) {
      deletionConvergence.convergence.runs = !payload.page.hasMore
        && !payload.items.some((item) => item.sessionId === deletionConvergence.targetId);
    }
    payload.items = payload.items.filter(
      (item) => !conversationDeletionTombstoned(item.sessionId),
    );
    runsStore.items = append ? runsStore.items.concat(payload.items) : payload.items;
    runsStore.page = payload.page;
    runsStore.source = payload.source;
    runsStore.coverage = payload.coverage;
    runsStore.summary = payload.summary;
    runsStore.diagnostics = Array.isArray(payload.diagnostics) ? payload.diagnostics : [];
    runsStore.phase = payload.source.status === 'error' ? 'partial' : 'loaded';
    runsStore.loadingMore = false;
    const countTarget = document.querySelector('[data-count="runs"]');
    if (countTarget) countTarget.textContent = String(payload.summary.knownTotal ?? '—');
  } catch (_error) {
    if (requestId !== runsStore.requestId || filterKey !== JSON.stringify(runsStore.filters)) return;
    runsStore.loadingMore = false;
    runsStore.phase = runsStore.items.length ? 'partial' : 'error';
    runsStore.error = '无法读取 RunJournal。其他只读页面和真实会话栏仍可使用。';
  }
  renderRouteOnly('runs');
}

function refreshRuns() {
  runsStore.items = [];
  runsStore.page = null;
  runsStore.source = null;
  runsStore.coverage = null;
  runsStore.summary = null;
  runsStore.diagnostics = [];
  resetRunDetail();
  return loadRuns(false);
}

function setRunStatusFilter(status) {
  runsStore.filters.status = status || null;
  runsStore.items = [];
  runsStore.page = null;
  resetRunDetail();
  loadRuns(false);
}

function setRunSourceFilter(source) {
  runsStore.filters.source = source || null;
  runsStore.items = [];
  runsStore.page = null;
  resetRunDetail();
  loadRuns(false);
}

function loadMoreRuns() {
  if (!runsStore.loadingMore && runsStore.page?.hasMore) loadRuns(true);
}

async function loadRunDetail(runId, append = false, preserve = false) {
  const requestId = runDetailStore.requestId + 1;
  runDetailStore.requestId = requestId;
  runDetailStore.runId = runId;
  runDetailStore.error = null;
  if (append) runDetailStore.loadingMore = true;
  else {
    runDetailStore.phase = preserve && runDetailStore.data ? 'partial' : 'loading';
    if (!preserve) runDetailStore.data = null;
  }
  renderRouteOnly('runs');
  const params = new URLSearchParams({ limit: '50' });
  if (append && runDetailStore.data?.page?.nextCursor) params.set('cursor', runDetailStore.data.page.nextCursor);
  try {
    const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}?${params.toString()}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('run detail request failed');
    const payload = await response.json();
    assertPageContract(payload, 'events');
    if (!payload.run || !payload.metrics || requestId !== runDetailStore.requestId || runId !== runDetailStore.runId) return;
    if (conversationDeletionTombstoned(payload.run.sessionId)) return;
    if (append && runDetailStore.data) payload.events = runDetailStore.data.events.concat(payload.events);
    runDetailStore.data = payload;
    runDetailStore.phase = payload.source.status === 'error' ? 'partial' : 'loaded';
    runDetailStore.loadingMore = false;
  } catch (_error) {
    if (requestId !== runDetailStore.requestId || runId !== runDetailStore.runId) return;
    runDetailStore.loadingMore = false;
    runDetailStore.phase = runDetailStore.data ? 'partial' : 'error';
    runDetailStore.error = '无法读取该 Run 的只读事件详情。';
  }
  renderRouteOnly('runs');
}

function selectRun(runId) {
  if (runId) loadRunDetail(runId, false);
}

function loadMoreRunEvents() {
  if (runDetailStore.runId && !runDetailStore.loadingMore && runDetailStore.data?.page?.hasMore) {
    loadRunDetail(runDetailStore.runId, true);
  }
}

function renderRuntimeRouteOnly() {
  const [view, sub] = currentRoute();
  if (!((view === 'skills' && sub === 'routing') || (view === 'memory' && ['retrieval', 'injection'].includes(sub)))) return;
  document.querySelector('#view').innerHTML = VIEWS[view](DATA, sub);
  tickMeta();
}

async function loadRuntimeRunDetail(runId, preserve = false) {
  const requestId = runtimeTraceStore.detailRequestId + 1;
  runtimeTraceStore.detailRequestId = requestId;
  runtimeTraceStore.selectedRunId = runId;
  if (!preserve) runtimeTraceStore.detail = null;
  runtimeTraceStore.error = null;
  runtimeTraceStore.phase = preserve && runtimeTraceStore.detail ? 'partial' : 'loading';
  renderRuntimeRouteOnly();
  try {
    const response = await fetch(`/api/v1/runs/${encodeURIComponent(runId)}?limit=100`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('runtime Run detail request failed');
    const payload = await response.json();
    assertPageContract(payload, 'events');
    if (requestId !== runtimeTraceStore.detailRequestId || runId !== runtimeTraceStore.selectedRunId) return;
    runtimeTraceStore.detail = payload;
    runtimeTraceStore.phase = payload.source.status === 'error' || payload.page.hasMore || runtimeTraceStore.source?.status === 'error' ? 'partial' : 'loaded';
  } catch (_error) {
    if (requestId !== runtimeTraceStore.detailRequestId || runId !== runtimeTraceStore.selectedRunId) return;
    runtimeTraceStore.phase = 'error';
    runtimeTraceStore.error = '无法读取所选 Run 的运行级观测事件。';
  }
  renderRuntimeRouteOnly();
}

async function loadRuntimeTrace(preserve = false) {
  const requestId = runtimeTraceStore.listRequestId + 1;
  runtimeTraceStore.listRequestId = requestId;
  runtimeTraceStore.detailRequestId += 1;
  runtimeTraceStore.error = null;
  runtimeTraceStore.phase = preserve && runtimeTraceStore.runs.length ? 'partial' : 'loading';
  renderRuntimeRouteOnly();
  try {
    const response = await fetch('/api/v1/runs?limit=20', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('runtime Runs request failed');
    const payload = await response.json();
    assertPageContract(payload, 'items');
    if (!payload.coverage || requestId !== runtimeTraceStore.listRequestId) return;
    runtimeTraceStore.runs = payload.items;
    runtimeTraceStore.source = payload.source;
    runtimeTraceStore.coverage = payload.coverage;
    if (!payload.items.length) {
      runtimeTraceStore.selectedRunId = null;
      runtimeTraceStore.detail = null;
      runtimeTraceStore.phase = payload.source.status === 'error' ? 'error' : 'empty';
      renderRuntimeRouteOnly();
      return;
    }
    const selectedStillExists = payload.items.some((run) => run.id === runtimeTraceStore.selectedRunId);
    const runId = selectedStillExists ? runtimeTraceStore.selectedRunId : payload.items[0].id;
    await loadRuntimeRunDetail(runId, preserve);
  } catch (_error) {
    if (requestId !== runtimeTraceStore.listRequestId) return;
    runtimeTraceStore.phase = 'error';
    runtimeTraceStore.error = '无法读取最近 Runs。';
    renderRuntimeRouteOnly();
  }
}

function refreshRuntimeTrace() {
  runtimeTraceStore.runs = [];
  runtimeTraceStore.source = null;
  runtimeTraceStore.coverage = null;
  runtimeTraceStore.selectedRunId = null;
  runtimeTraceStore.detail = null;
  loadRuntimeTrace();
}

function selectRuntimeRun(runId) {
  if (runId && runId !== runtimeTraceStore.selectedRunId) loadRuntimeRunDetail(runId);
}

function sessionWorkspaceId() {
  const workspaceId = snapshotStore.data?.workspace?.id;
  return typeof workspaceId === 'string' && workspaceId.length <= 128 ? workspaceId : null;
}

function storedSessionSelection() {
  const workspaceId = sessionWorkspaceId();
  if (!workspaceId) return null;
  try {
    const raw = sessionStorage.getItem(SESSION_SELECTION_STORAGE_KEY);
    if (!raw || raw.length > 512) return null;
    const value = JSON.parse(raw);
    return value?.workspaceId === workspaceId && SESSION_ID_PATTERN.test(value?.sessionId || '')
      ? value.sessionId
      : null;
  } catch (_error) {
    return null;
  }
}

function persistSessionSelection(sessionId) {
  const workspaceId = sessionWorkspaceId();
  if (!workspaceId || !SESSION_ID_PATTERN.test(sessionId || '')) return;
  try {
    sessionStorage.setItem(SESSION_SELECTION_STORAGE_KEY, JSON.stringify({ workspaceId, sessionId }));
  } catch (_error) {
    // Selection persistence is optional and never blocks the read model.
  }
}

function clearStoredSessionSelection(sessionId = null) {
  const workspaceId = sessionWorkspaceId();
  if (!workspaceId) return;
  try {
    const raw = sessionStorage.getItem(SESSION_SELECTION_STORAGE_KEY);
    const value = raw && raw.length <= 512 ? JSON.parse(raw) : null;
    if (value?.workspaceId === workspaceId
        && (sessionId === null || value?.sessionId === sessionId)) {
      sessionStorage.removeItem(SESSION_SELECTION_STORAGE_KEY);
    }
  } catch (_error) {
    // Invalid browser state is ignored and never projected.
  }
}

function createTurnId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return `turn_${Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('')}`;
}

function storedActiveTurn() {
  const workspaceId = sessionWorkspaceId();
  if (!workspaceId) return null;
  try {
    const raw = sessionStorage.getItem(ACTIVE_TURN_STORAGE_KEY);
    if (!raw || raw.length > 512) return null;
    const value = JSON.parse(raw);
    const targetValid = value?.targetSessionId === null || SESSION_ID_PATTERN.test(value?.targetSessionId || '');
    return value?.version === ACTIVE_TURN_RECOVERY_VERSION
      && value?.workspaceId === workspaceId
      && TURN_ID_PATTERN.test(value?.turnId || '')
      && targetValid
      ? { turnId: value.turnId, targetSessionId: value.targetSessionId }
      : null;
  } catch (_error) {
    return null;
  }
}

function persistActiveTurn(turnId, targetSessionId) {
  const workspaceId = sessionWorkspaceId();
  if (!workspaceId || !TURN_ID_PATTERN.test(turnId || '')) return false;
  if (targetSessionId !== null && !SESSION_ID_PATTERN.test(targetSessionId || '')) return false;
  try {
    sessionStorage.setItem(ACTIVE_TURN_STORAGE_KEY, JSON.stringify({
      version: ACTIVE_TURN_RECOVERY_VERSION,
      workspaceId,
      turnId,
      targetSessionId,
    }));
    return true;
  } catch (_error) {
    return false;
  }
}

function clearActiveTurn(turnId = null) {
  try {
    const raw = sessionStorage.getItem(ACTIVE_TURN_STORAGE_KEY);
    const value = raw && raw.length <= 512 ? JSON.parse(raw) : null;
    if (turnId === null || value?.turnId === turnId) sessionStorage.removeItem(ACTIVE_TURN_STORAGE_KEY);
  } catch (_error) {
    // Invalid recovery identity is local-only and safe to forget.
  }
  if (turnId === null || chatStore.activeTurnId === turnId) {
    chatStore.activeTurnId = null;
    chatStore.activeTargetSessionId = null;
  }
}

function renderSessionSurfaces() {
  renderRouteOnly('sessions');
  renderConversationDock();
}

async function reconcileSessionSelection(selectionVersion, preserveDetail = false) {
  if (selectionVersion !== sessionDetailStore.selectionVersion) return;
  if (!sessionsStore.items.length) {
    sessionDetailStore.requestId += 1;
    sessionDetailStore.sessionId = null;
    sessionDetailStore.data = null;
    sessionDetailStore.error = null;
    sessionDetailStore.phase = 'empty';
    chatStore.targetMode = 'new';
    clearStoredSessionSelection();
    renderSessionSurfaces();
    return;
  }
  const preferred = sessionDetailStore.sessionId || storedSessionSelection();
  if (preferred) {
    const outcome = await loadSessionDetail(preferred, false, false, preserveDetail);
    if (selectionVersion !== sessionDetailStore.selectionVersion || outcome !== 'missing') return;
  }
  if (selectionVersion !== sessionDetailStore.selectionVersion) return;
  const latest = sessionsStore.items[0]?.id;
  if (latest) await loadSessionDetail(latest, false, true, preserveDetail);
}

async function loadSessions(append = false, preserveDetail = false) {
  const requestId = sessionsStore.requestId + 1;
  const selectionVersion = sessionDetailStore.selectionVersion;
  sessionsStore.requestId = requestId;
  sessionsStore.error = null;
  if (append) sessionsStore.loadingMore = true;
  else sessionsStore.phase = sessionsStore.items.length ? 'partial' : 'loading';
  renderSessionSurfaces();
  const params = new URLSearchParams({ limit: '20' });
  if (append && sessionsStore.page?.nextCursor) params.set('cursor', sessionsStore.page.nextCursor);
  try {
    const response = await fetch('/api/v1/sessions?' + params.toString(), { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('sessions request failed');
    const payload = await response.json();
    assertPageContract(payload, 'items');
    if (requestId !== sessionsStore.requestId) return;
    const deletionConvergence = conversationDeletionConvergenceStore();
    if (deletionConvergence) {
      deletionConvergence.convergence.sessions = !payload.page.hasMore
        && !payload.items.some((item) => item.id === deletionConvergence.targetId);
    }
    payload.items = payload.items.filter(
      (item) => !conversationDeletionTombstoned(item.id),
    );
    if (append) {
      const knownIds = new Set(sessionsStore.items.map((item) => item.id));
      sessionsStore.items = sessionsStore.items.concat(payload.items.filter((item) => !knownIds.has(item.id)));
    } else {
      sessionsStore.items = payload.items;
    }
    sessionsStore.page = payload.page;
    sessionsStore.source = payload.source;
    sessionsStore.diagnostics = Array.isArray(payload.diagnostics) ? payload.diagnostics : [];
    sessionsStore.phase = payload.source.status === 'error' ? 'partial' : (payload.items.length || append ? 'loaded' : 'empty');
    sessionsStore.loadingMore = false;
    if (!append) await reconcileSessionSelection(selectionVersion, preserveDetail);
  } catch (_error) {
    if (requestId !== sessionsStore.requestId) return;
    sessionsStore.loadingMore = false;
    sessionsStore.phase = sessionsStore.items.length ? 'partial' : 'error';
    sessionsStore.error = '无法读取历史 Session。真实只读会话栏可手动重试。';
  }
  renderSessionSurfaces();
}

function refreshSessions() {
  sessionsStore.page = null;
  sessionsStore.source = null;
  sessionsStore.diagnostics = [];
  return loadSessions(false);
}

function loadMoreSessions() {
  if (!sessionsStore.loadingMore && sessionsStore.page?.hasMore) loadSessions(true);
}

async function loadSessionDetail(sessionId, append = false, persistSelection = true, preserve = false) {
  if (!SESSION_ID_PATTERN.test(sessionId || '')
      || conversationDeletionTombstoned(sessionId)) return 'missing';
  const requestId = sessionDetailStore.requestId + 1;
  sessionDetailStore.requestId = requestId;
  sessionDetailStore.sessionId = sessionId;
  sessionDetailStore.error = null;
  if (persistSelection) persistSessionSelection(sessionId);
  if (append) sessionDetailStore.loadingMore = true;
  else {
    sessionDetailStore.phase = preserve && sessionDetailStore.data ? 'partial' : 'loading';
    if (!preserve) sessionDetailStore.data = null;
  }
  renderSessionSurfaces();
  const params = new URLSearchParams({ limit: '50' });
  if (append && sessionDetailStore.data?.page?.nextCursor) params.set('cursor', sessionDetailStore.data.page.nextCursor);
  try {
    const response = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}?${params.toString()}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (response.status === 404) {
      if (requestId !== sessionDetailStore.requestId || sessionId !== sessionDetailStore.sessionId) return 'stale';
      sessionDetailStore.loadingMore = false;
      sessionDetailStore.phase = 'error';
      sessionDetailStore.error = '所选 Session 已不存在。';
      renderSessionSurfaces();
      return 'missing';
    }
    if (!response.ok) throw new Error('session detail request failed');
    const payload = await response.json();
    assertPageContract(payload, 'messages');
    if (requestId !== sessionDetailStore.requestId || sessionId !== sessionDetailStore.sessionId) return 'stale';
    if (conversationDeletionTombstoned(sessionId)) return 'stale';
    if (append && sessionDetailStore.data) {
      const knownIndexes = new Set(sessionDetailStore.data.messages.map((message) => message.index));
      payload.messages = sessionDetailStore.data.messages.concat(payload.messages.filter((message) => !knownIndexes.has(message.index)));
    }
    sessionDetailStore.data = payload;
    sessionDetailStore.phase = payload.source.status === 'error' || payload.page.hasMore ? 'partial' : 'loaded';
    sessionDetailStore.loadingMore = false;
    persistSessionSelection(sessionId);
  } catch (_error) {
    if (requestId !== sessionDetailStore.requestId || sessionId !== sessionDetailStore.sessionId) return 'stale';
    sessionDetailStore.loadingMore = false;
    sessionDetailStore.phase = sessionDetailStore.data ? 'partial' : 'error';
    sessionDetailStore.error = '无法读取该 Session 的只读详情。';
    renderSessionSurfaces();
    return 'error';
  }
  renderSessionSurfaces();
  return 'loaded';
}

function selectHistoricalSession(sessionId) {
  if (!SESSION_ID_PATTERN.test(sessionId || '')) return;
  chatStore.targetMode = 'existing';
  if (!chatStore.activeTurnId) {
    chatStore.phase = 'idle';
    chatStore.error = null;
  }
  sessionDetailStore.selectionVersion += 1;
  persistSessionSelection(sessionId);
  loadSessionDetail(sessionId, false, true);
}

function openRunSession(sessionId) {
  if (!SESSION_ID_PATTERN.test(sessionId || '')) return;
  selectHistoricalSession(sessionId);
  if (location.hash !== '#sessions') location.hash = '#sessions';
  else renderSessionSurfaces();
}

function loadMoreSessionMessages() {
  if (sessionDetailStore.sessionId && !sessionDetailStore.loadingMore && sessionDetailStore.data?.page?.hasMore) {
    loadSessionDetail(sessionDetailStore.sessionId, true, true);
  }
}

async function loadMemory(append = false) {
  const requestId = memoryStore.requestId + 1;
  memoryStore.requestId = requestId;
  memoryStore.error = null;
  if (append) memoryStore.loadingMore = true;
  else memoryStore.phase = memoryStore.data ? 'partial' : 'loading';
  renderRouteOnly('memory');
  const params = new URLSearchParams({ limit: '20' });
  Object.entries(memoryStore.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  if (append && memoryStore.data?.page?.nextCursor) params.set('cursor', memoryStore.data.page.nextCursor);
  try {
    const response = await fetch(`/api/v1/memory?${params.toString()}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('memory request failed');
    const payload = await response.json();
    assertPageContract(payload, 'items');
    if (!payload.summary || !payload.scopes) throw new Error('memory contract mismatch');
    if (requestId !== memoryStore.requestId) return;
    const deletionConvergence = projectMemoryDeletionConvergenceStore();
    if (deletionConvergence) {
      const canProveProjectAbsence = [null, 'project'].includes(memoryStore.filters.scope);
      deletionConvergence.convergence.memory = canProveProjectAbsence
        && !payload.page.hasMore
        && !payload.items.some((item) => item.id === deletionConvergence.targetId);
    }
    payload.items = payload.items.filter(
      (item) => !projectMemoryDeletionTombstoned(item.id),
    );
    if (append && memoryStore.data) payload.items = memoryStore.data.items.concat(payload.items);
    memoryStore.data = payload;
    memoryStore.phase = payload.source.status === 'error' ? 'partial' : 'loaded';
    memoryStore.loadingMore = false;
    const countTarget = document.querySelector('[data-count="memory"]');
    if (countTarget) countTarget.textContent = String(payload.summary.total ?? `${payload.summary.knownTotal}+`);
  } catch (_error) {
    if (requestId !== memoryStore.requestId) return;
    memoryStore.loadingMore = false;
    memoryStore.phase = memoryStore.data ? 'partial' : 'error';
    memoryStore.error = '无法读取持久 Memory。其他页面仍可使用。';
  }
  renderRouteOnly('memory');
}

function refreshMemory() {
  memoryStore.data = null;
  loadMemory(false);
}

function setMemoryScopeFilter(scope) {
  memoryStore.filters.scope = scope || null;
  memoryStore.data = null;
  loadMemory(false);
}

function loadMoreMemory() {
  if (!memoryStore.loadingMore && memoryStore.data?.page?.hasMore) loadMemory(true);
}

function assertReadContract(payload) {
  if (payload?.schemaVersion !== 1 || payload?.mode !== 'read-only' || !payload?.source || !Array.isArray(payload?.diagnostics)) {
    throw new Error('read contract mismatch');
  }
}

function assertSkillEvidenceContract(evidence) {
  if (!evidence || !['live', 'partial', 'unavailable'].includes(evidence.status)
      || evidence.scope !== 'retained-run-journal' || typeof evidence.message !== 'string') {
    throw new Error('skill evidence contract mismatch');
  }
  if (evidence.status === 'unavailable') {
    if (evidence.ledger !== null) throw new Error('skill evidence unavailable contract mismatch');
    return;
  }
  const ledger = evidence.ledger;
  const countFields = ['scannedRuns', 'eligibleTreatmentRuns', 'eligibleControlRuns', 'journalDiagnostics'];
  if (!ledger || ledger.ledgerVersion !== 1 || ledger.mode !== 'shadow'
      || ledger.promotionEligible !== false || !Array.isArray(ledger.evaluations)
      || typeof ledger.runsTruncated !== 'boolean' || typeof ledger.evaluationsTruncated !== 'boolean'
      || !countFields.every((field) => Number.isInteger(ledger[field]) && ledger[field] >= 0)
      || !ledger.excludedRuns || Object.values(ledger.excludedRuns).some((value) => !Number.isInteger(value) || value < 0)) {
    throw new Error('skill evidence ledger contract mismatch');
  }
  const validSignalMetric = (cohort) => {
    const runs = cohort?.runs;
    const verification = cohort?.verification;
    const user = cohort?.userSignal;
    const verificationCounts = [
      verification?.observedRuns,
      verification?.passedRuns,
      verification?.failedRuns,
    ];
    const userCounts = [
      user?.observedRuns,
      user?.acceptedRuns,
      user?.correctedRuns,
      user?.rejectedRuns,
    ];
    return Number.isInteger(runs) && runs >= 0
      && verificationCounts.every((value) => Number.isInteger(value) && value >= 0)
      && verification.observedRuns === verification.passedRuns + verification.failedRuns
      && verification.observedRuns <= runs
      && verification.coverageComplete === (runs > 0 && verification.observedRuns === runs)
      && userCounts.every((value) => Number.isInteger(value) && value >= 0)
      && user.observedRuns === user.acceptedRuns + user.correctedRuns + user.rejectedRuns
      && user.observedRuns <= runs
      && user.coverageComplete === (runs > 0 && user.observedRuns === runs);
  };
  if (!ledger.evaluations.every((item) => validSignalMetric(item?.treatment) && validSignalMetric(item?.control))) {
    throw new Error('skill evidence signal contract mismatch');
  }
}

function assertSkillVersionLedgerContract(wrapper) {
  if (!wrapper || !['live', 'partial', 'unavailable'].includes(wrapper.status)
      || wrapper.scope !== 'project-skill-version-ledger' || typeof wrapper.message !== 'string') {
    throw new Error('skill version ledger contract mismatch');
  }
  if (wrapper.status === 'unavailable') {
    if (wrapper.ledger !== null) throw new Error('skill version ledger unavailable contract mismatch');
    return;
  }
  const ledger = wrapper.ledger;
  if (!ledger || ledger.ledgerVersion !== 1 || ledger.mode !== 'shadow'
      || ledger.promotionLocked !== true || !Array.isArray(ledger.versions)
      || !ledger.evaluation || ledger.evaluation.gatePolicyVersion !== 2
      || !Number.isInteger(ledger.evaluation.versionCount)
      || ledger.evaluation.versionCount !== ledger.versions.length
      || !Number.isInteger(ledger.evaluation.promotionCandidateCount)
      || ledger.evaluation.promotionCandidateCount < 0
      || ledger.evaluation.promotionCandidateCount > ledger.versions.length) {
    throw new Error('skill version ledger payload mismatch');
  }
  const gateNames = ['outcome', 'verification', 'user', 'cost', 'latency'];
  const valid = ledger.versions.every((version) => {
    const skill = version?.skill;
    const evaluation = version?.evaluation;
    const gates = evaluation?.gates;
    return /^skillv_[0-9a-f]{32}$/.test(version?.versionId || '')
      && (version.parentVersionId === null || /^skillv_[0-9a-f]{32}$/.test(version.parentVersionId))
      && version.rollbackToVersionId === version.parentVersionId
      && version.status === 'observed' && typeof version.catalogCurrent === 'boolean'
      && skill && typeof skill.qualifiedName === 'string' && typeof skill.source === 'string'
      && typeof skill.directory === 'string' && /^[0-9a-f]{64}$/.test(skill.contentDigest || '')
      && evaluation?.gatePolicyVersion === 2 && Array.isArray(gates) && gates.length === gateNames.length
      && gates.every((gate, index) => gate?.name === gateNames[index]
        && ['pass', 'fail', 'unavailable'].includes(gate.status) && typeof gate.reason === 'string')
      && evaluation.allRequiredGatesPassed === gates.every((gate) => gate.status === 'pass')
      && evaluation.promotionCandidate === evaluation.allRequiredGatesPassed
      && evaluation.promotionLocked === true;
  });
  if (!valid) throw new Error('skill version lineage contract mismatch');
  if (ledger.evaluation.promotionCandidateCount !== ledger.versions.filter((version) => version.evaluation.promotionCandidate).length) {
    throw new Error('skill version candidate count mismatch');
  }
}

function assertConnectionsContract(payload) {
  assertReadContract(payload);
  const summary = payload?.summary;
  const runtime = payload?.mcpRuntime;
  const current = payload?.mcpCurrent;
  const coverage = payload?.coverage;
  const servers = payload?.mcpServers;
  const countFields = ['configuredMcpCount', 'observedConfiguredCount', 'unobservedConfiguredCount', 'unmatchedObservedServerCount'];
  const nullableCountFields = ['registeredConfiguredMcpCount', 'activeMcpInstanceCount', 'liveMcpCount'];
  const nullableCount = (value) => value === null || (Number.isInteger(value) && value >= 0);
  const timestamp = (value) => typeof value === 'string' && value.endsWith('Z') && !Number.isNaN(Date.parse(value));
  const currentStates = ['idle', 'starting', 'ready', 'failed'];
  const currentProtocols = ['content-length', 'newline-json'];
  const currentFailureKinds = ['timeout', 'command_not_found', 'process_exit', 'protocol_error', 'request_error', 'other'];
  if (!summary || !payload?.gateway || !current || !runtime || !coverage || !Array.isArray(servers)
      || !countFields.every((field) => Number.isInteger(summary[field]) && summary[field] >= 0)
      || !nullableCountFields.every((field) => nullableCount(summary[field]))
      || !['live', 'unavailable', 'error'].includes(current.status)
      || !['process-local', 'unavailable'].includes(current.current)
      || !current.coverage || current.coverage.scope !== 'gateway-process'
      || current.coverage.crossProcess !== 'unavailable' || current.coverage.heartbeat !== false
      || current.coverage.association !== 'configured-current-workspace-only'
      || !['complete', 'partial'].includes(current.coverage.configuredSet)
      || current.coverage.unmatched !== 'suppressed' || typeof current.coverage.limited !== 'boolean'
      || !Array.isArray(current.diagnostics)
      || !['stale', 'unavailable', 'error'].includes(runtime.status)
      || runtime.current !== 'unavailable' || runtime.historical !== 'partial'
      || runtime.liveCount !== null || !Number.isInteger(runtime.retainedObservationCount)
      || coverage.scope !== 'retained-run-scoped-mcp-observations'
      || coverage.current !== 'unavailable' || coverage.historical !== 'partial'
      || !Number.isInteger(coverage.runScanLimit) || !Number.isInteger(coverage.eventScanLimitPerRun)
      || !Number.isInteger(coverage.scannedRuns) || typeof coverage.limited !== 'boolean') {
    throw new Error('connections contract mismatch');
  }
  const exactCurrent = current.status === 'live' && current.coverage.configuredSet === 'complete' && !current.coverage.limited;
  const currentAggregatesExact = nullableCountFields.every((field) => Number.isInteger(summary[field]) && summary[field] >= 0);
  const currentAggregatesUnavailable = nullableCountFields.every((field) => summary[field] === null);
  const validByState = current.byState !== null
    && typeof current.byState === 'object'
    && currentStates.every((state) => Number.isInteger(current.byState[state]) && current.byState[state] >= 0)
    && Object.keys(current.byState).length === currentStates.length;
  const liveCurrentShape = current.current === 'process-local' && current.stateVersion === 1 && timestamp(current.checkedAt);
  const unavailableCurrentShape = current.current === 'unavailable' && current.stateVersion === null && current.checkedAt === null;
  const diagnosticsValid = current.diagnostics.every((item) => item && typeof item.code === 'string' && Number.isInteger(item.count) && item.count > 0);
  if (typeof current.message !== 'string' || !diagnosticsValid
      || (current.status === 'live' ? !liveCurrentShape : !unavailableCurrentShape)
      || (exactCurrent && (!validByState || !currentAggregatesExact))
      || (!exactCurrent && (!currentAggregatesUnavailable || current.byState !== null))
      || (exactCurrent && (summary.registeredConfiguredMcpCount !== currentStates.reduce((total, state) => total + current.byState[state], 0)
          || summary.liveMcpCount !== current.byState.ready))) {
    throw new Error('connections current aggregate contract mismatch');
  }
  servers.forEach((server) => {
    const historical = server?.runtime;
    const processState = server?.current;
    if (!server || !processState || !historical
        || !['live', 'unavailable', 'error'].includes(processState.status)
        || !['idle', 'starting', 'ready', 'failed', 'unavailable', 'error'].includes(server.liveStatus)
        || !['stale', 'unavailable', 'error'].includes(historical.status)
        || historical.current !== 'unavailable' || typeof historical.observed !== 'boolean'
        || !Number.isInteger(historical.retainedObservationCount)) {
      throw new Error('connections server contract mismatch');
    }
    const liveServer = processState.status === 'live';
    const unavailableServer = processState.status === 'unavailable';
    const fieldsUnavailable = processState.state === null && processState.activeInstanceCount === null
      && processState.protocol === null && processState.failureKind === null && processState.updatedAt === null;
    const liveFieldsValid = currentStates.includes(processState.state)
      && Number.isInteger(processState.activeInstanceCount) && processState.activeInstanceCount > 0
      && timestamp(processState.updatedAt) && processState.reason === null
      && processState.liveStatus === undefined;
    const stateRelationValid = processState.state === 'ready'
      ? currentProtocols.includes(processState.protocol) && processState.failureKind === null
      : processState.state === 'failed'
        ? (processState.protocol === null || currentProtocols.includes(processState.protocol)) && currentFailureKinds.includes(processState.failureKind)
        : processState.protocol === null && processState.failureKind === null;
    const reasonValid = unavailableServer
      ? ['not_registered', 'snapshot_limited', 'source_unavailable'].includes(processState.reason)
      : processState.status === 'error' && processState.reason === 'source_error';
    const sourceRelationValid = current.status === 'live'
      ? (liveServer || (unavailableServer && ['not_registered', 'snapshot_limited'].includes(processState.reason)))
      : current.status === 'unavailable'
        ? unavailableServer && processState.reason === 'source_unavailable'
        : processState.status === 'error' && processState.reason === 'source_error';
    if ((liveServer && (!liveFieldsValid || !stateRelationValid || server.liveStatus !== processState.state))
        || (!liveServer && (!fieldsUnavailable || !reasonValid || server.liveStatus !== (processState.status === 'error' ? 'error' : 'unavailable')))
        || !sourceRelationValid) {
      throw new Error('connections server current contract mismatch');
    }
  });
}

async function loadSkills(append = false) {
  const requestId = skillsStore.requestId + 1;
  const filterKey = JSON.stringify(skillsStore.filters);
  skillsStore.requestId = requestId;
  skillsStore.error = null;
  if (append) skillsStore.loadingMore = true;
  else skillsStore.phase = skillsStore.data ? 'partial' : 'loading';
  renderRouteOnly('skills');
  const params = new URLSearchParams({ limit: '20' });
  Object.entries(skillsStore.filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  if (append && skillsStore.data?.page?.nextCursor) params.set('cursor', skillsStore.data.page.nextCursor);
  try {
    const response = await fetch(`/api/v1/skills?${params.toString()}`, { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('skills request failed');
    const payload = await response.json();
    assertPageContract(payload, 'items');
    assertSkillEvidenceContract(payload.evidence);
    assertSkillVersionLedgerContract(payload.versionLedger);
    if (!payload.summary || requestId !== skillsStore.requestId || filterKey !== JSON.stringify(skillsStore.filters)) return;
    if (append && skillsStore.data) payload.items = skillsStore.data.items.concat(payload.items);
    skillsStore.data = payload;
    skillsStore.phase = payload.source.status === 'error' ? 'partial' : 'loaded';
    skillsStore.loadingMore = false;
    const countTarget = document.querySelector('[data-count="skills"]');
    if (countTarget) countTarget.textContent = String(payload.summary.total ?? '—');
  } catch (_error) {
    if (requestId !== skillsStore.requestId || filterKey !== JSON.stringify(skillsStore.filters)) return;
    skillsStore.loadingMore = false;
    skillsStore.phase = skillsStore.data ? 'partial' : 'error';
    skillsStore.error = '无法读取 Skill 安全摘要。其他页面仍可使用。';
  }
  renderRouteOnly('skills');
}

function refreshSkills() {
  skillsStore.data = null;
  loadSkills(false);
}

function setSkillSourceFilter(source) {
  skillsStore.filters.source = source || null;
  skillsStore.data = null;
  loadSkills(false);
}

function setSkillDirectoryFilter(directory) {
  skillsStore.filters.directory = directory || null;
  skillsStore.data = null;
  loadSkills(false);
}

function loadMoreSkills() {
  if (!skillsStore.loadingMore && skillsStore.data?.page?.hasMore) loadSkills(true);
}

async function loadConnections() {
  const requestId = connectionsStore.requestId + 1;
  connectionsStore.requestId = requestId;
  connectionsStore.error = null;
  connectionsStore.phase = connectionsStore.data ? 'partial' : 'loading';
  renderRouteOnly('connections');
  try {
    const response = await fetch('/api/v1/connections', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('connections request failed');
    const payload = await response.json();
    assertConnectionsContract(payload);
    if (requestId !== connectionsStore.requestId) return;
    connectionsStore.data = payload;
    if (payload.source.status === 'error' || payload.mcpCurrent.status !== 'live' || payload.mcpCurrent.coverage.limited || payload.mcpCurrent.diagnostics.length || payload.mcpRuntime.status === 'error' || payload.coverage.limited || payload.diagnostics.length) {
      connectionsStore.phase = 'partial';
    } else if (payload.summary.configuredMcpCount === 0 && payload.mcpRuntime.retainedObservationCount === 0) {
      connectionsStore.phase = 'empty';
    } else {
      connectionsStore.phase = 'loaded';
    }
    const countTarget = document.querySelector('[data-count="connections"]');
    if (countTarget) countTarget.textContent = String(payload.summary.configuredMcpCount ?? '—');
  } catch (_error) {
    if (requestId !== connectionsStore.requestId) return;
    connectionsStore.phase = connectionsStore.data ? 'partial' : 'error';
    connectionsStore.error = '无法读取 Gateway / MCP 配置摘要。';
  }
  renderRouteOnly('connections');
}

function refreshConnections() {
  connectionsStore.data = null;
  loadConnections();
}

async function loadSystem() {
  const requestId = systemStore.requestId + 1;
  systemStore.requestId = requestId;
  systemStore.error = null;
  systemStore.phase = systemStore.data ? 'partial' : 'loading';
  renderRouteOnly('system');
  try {
    const response = await fetch('/api/v1/system', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('system request failed');
    const payload = await response.json();
    assertReadContract(payload);
    if (!payload.application || !payload.runtime || !payload.workspace || !payload.features || !payload.storage) throw new Error('system contract mismatch');
    if (requestId !== systemStore.requestId) return;
    systemStore.data = payload;
    systemStore.phase = payload.source.status === 'error' ? 'partial' : 'loaded';
  } catch (_error) {
    if (requestId !== systemStore.requestId) return;
    systemStore.phase = systemStore.data ? 'partial' : 'error';
    systemStore.error = '无法读取安全的 System 摘要。';
  }
  renderRouteOnly('system');
}

function refreshSystem() {
  systemStore.data = null;
  loadSystem();
}

async function loadDataHealth() {
  const requestId = dataHealthStore.requestId + 1;
  dataHealthStore.requestId = requestId;
  dataHealthStore.error = null;
  dataHealthStore.phase = dataHealthStore.data ? 'partial' : 'loading';
  renderRouteOnly('system');
  try {
    const response = await fetch('/api/v1/data-health', {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    if (!response.ok
        || !String(response.headers.get('content-type') || '').toLowerCase().startsWith('application/json')) {
      throw new Error('data health request failed');
    }
    const raw = await response.text();
    if (utf8ByteLength(raw) > DATA_HEALTH_MAX_BYTES) throw new Error('data health response too large');
    const payload = validateDataHealthPayload(JSON.parse(raw));
    if (!payload) throw new Error('data health contract mismatch');
    if (requestId !== dataHealthStore.requestId) return;
    dataHealthStore.data = payload;
    const retainedRecords = payload.stores
      .filter((store) => store.durability !== 'process-local')
      .reduce((total, store) => total + (store.recordCount ?? 0), 0);
    if (payload.status !== 'live' || payload.summary.issueCount > 0) {
      dataHealthStore.phase = 'partial';
    } else if (retainedRecords === 0) {
      dataHealthStore.phase = 'empty';
    } else {
      dataHealthStore.phase = 'loaded';
    }
  } catch (_error) {
    if (requestId !== dataHealthStore.requestId) return;
    dataHealthStore.phase = dataHealthStore.data ? 'partial' : 'error';
    dataHealthStore.error = '无法读取安全的数据健康快照。';
  }
  renderRouteOnly('system');
}

function refreshDataHealth() {
  return loadDataHealth();
}

function renderOpsConsumers() {
  const [view, sub] = currentRoute();
  if (view === 'ops') renderRouteOnly('ops');
  if (view === 'memory' && sub === 'lifecycle') renderRouteOnly('memory');
}

async function loadOps() {
  const requestId = opsStore.requestId + 1;
  opsStore.requestId = requestId;
  opsStore.error = null;
  opsStore.phase = opsStore.data ? 'partial' : 'loading';
  renderOpsConsumers();
  try {
    const response = await fetch('/api/v1/ops', { headers: { Accept: 'application/json' }, cache: 'no-store' });
    if (!response.ok) throw new Error('ops request failed');
    const payload = await response.json();
    assertReadContract(payload);
    if (!payload.coverage || !payload.summary || !payload.usage || !payload.duration || !payload.cost || !payload.costBreakdown) throw new Error('ops contract mismatch');
    if (requestId !== opsStore.requestId) return;
    opsStore.data = payload;
    const modelCalls = (payload.summary.completedModelCalls || 0) + (payload.summary.failedModelCalls || 0);
    if (modelCalls === 0 && payload.source.status !== 'error') opsStore.phase = 'empty';
    else opsStore.phase = payload.source.status === 'live' ? 'loaded' : 'partial';
    const countTarget = document.querySelector('[data-count="usage"]');
    if (countTarget) countTarget.textContent = String(modelCalls || '—');
  } catch (_error) {
    if (requestId !== opsStore.requestId) return;
    opsStore.phase = opsStore.data ? 'partial' : 'error';
    opsStore.error = '无法读取保留 RunJournal 的模型用量、Cost 与耗时。';
  }
  renderOpsConsumers();
}

function refreshOps() {
  opsStore.data = null;
  return loadOps();
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

function runEventSummary(event) {
  const base = esc(event.summary || 'Run event');
  if (event.type === 'model.started') {
    return `<span class="run-event-detail model-event"><span>${base}</span></span>`;
  }
  if (event.type === 'model.completed') {
    const resultType = event.details?.resultType;
    const toolCallCount = event.details?.toolCallCount;
    const usage = event.details?.usage;
    const usageLabel = usage?.source === 'provider' ? 'Provider' : (usage?.source === 'estimated' ? 'Estimated' : 'Unavailable');
    const duration = event.details?.durationMs;
    const resultLabel = resultType === 'tool_calls' ? 'tool calls' : (resultType === 'assistant' ? 'assistant' : '');
    const callLabel = Number.isInteger(toolCallCount) && toolCallCount >= 0 ? `${esc(toolCallCount)} ${toolCallCount === 1 ? 'call' : 'calls'}` : '';
    return `<span class="run-event-detail model-event"><span>${base}</span>${resultLabel ? `<code>${esc(resultLabel)}</code>` : ''}${resultType === 'tool_calls' && callLabel ? `<small>${callLabel}</small>` : ''}<code>${esc(usageLabel)}</code><small>${esc(formatUsageBuckets(usage))}</small>${Number.isInteger(duration) ? `<small>${esc(formatDuration(duration))}</small>` : ''}</span>`;
  }
  if (event.type === 'model.failed') {
    const failureKind = event.details?.failureKind;
    const duration = event.details?.durationMs;
    return `<span class="run-event-detail model-event"><span>${base}</span>${statusPill('error')}${failureKind ? `<code>${esc(failureKind)}</code>` : ''}${Number.isInteger(duration) ? `<small>${esc(formatDuration(duration))}</small>` : ''}</span>`;
  }
  if (event.type === 'model.costed') {
    const status = event.details?.status || 'unavailable';
    const quality = event.details?.quality;
    const catalog = event.details?.catalogId;
    const model = event.details?.catalogModelKey;
    const reason = event.details?.reason;
    return `<span class="run-event-detail model-event"><span>${base}</span>${statusPill(status)}${quality ? `<code>${esc(quality)}</code>` : ''}${catalog ? `<small>${esc(catalog)}</small>` : ''}${model ? `<small>${esc(model)}</small>` : ''}${reason ? `<small>${esc(reason)}</small>` : ''}</span>`;
  }
  if (event.type === 'tool.started') {
    const toolName = event.details?.toolName;
    return `<span class="run-event-detail"><span>${base}</span>${toolName ? `<code>${esc(toolName)}</code>` : ''}</span>`;
  }
  if (event.type === 'tool.finished') {
    const toolName = event.details?.toolName;
    const outcome = event.details?.outcome;
    return `<span class="run-event-detail"><span>${base}</span>${toolName ? `<code>${esc(toolName)}</code>` : ''}${outcome === 'success' || outcome === 'error' ? statusPill(outcome) : ''}</span>`;
  }
  if (event.type === 'mcp.runtime.observed') {
    const details = event.details || {};
    const outcome = details.outcome;
    const serverKey = details.serverKey;
    const transport = details.transport === 'stdio' ? 'stdio' : '';
    const connectionLabel = details.connectionAttempted === true ? 'connection attempted' : 'existing connection observed';
    const title = outcome === 'request_succeeded' ? 'MCP request succeeded' : (outcome === 'connection_failed' ? 'MCP connection failed' : (outcome === 'request_failed' ? 'MCP request failed' : 'MCP runtime observed'));
    const failure = outcome === 'connection_failed' || outcome === 'request_failed' ? details.failureKind : '';
    const serverLabel = serverKey ? `server ${serverKey.slice(0, 12)}...` : '';
    const meta = outcome === 'request_succeeded' ? [transport, connectionLabel].filter(Boolean).join(' · ') : failure;
    return `<span class="run-event-detail runtime-event"><span>${esc(title)}</span>${serverLabel ? `<code>${esc(serverLabel)}</code>` : ''}${meta ? `<small>${esc(meta)}</small>` : ''}</span>`;
  }
  if (event.type === 'assistant.completed') {
    const contentLength = event.details?.contentLength;
    const lengthLabel = Number.isInteger(contentLength) && contentLength >= 0 ? `${esc(contentLength)} chars` : '';
    return `<span class="run-event-detail"><span>${base}</span>${lengthLabel ? `<small>${lengthLabel}</small>` : ''}</span>`;
  }
  if (event.type === 'execution.stopped') {
    const reason = event.details?.reasonCode;
    const failures = event.details?.consecutiveFailedSteps;
    const action = event.details?.userActionRequired === true ? 'user action required' : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${reason ? `<code>${esc(reason)}</code>` : ''}${Number.isInteger(failures) ? `<small>${esc(failures)} consecutive failed steps</small>` : ''}${action ? `<small>${esc(action)}</small>` : ''}</span>`;
  }
  if (event.type === 'task.outcome') {
    const outcomeStatus = event.details?.outcomeStatus;
    const toolErrorCount = event.details?.toolErrorCount;
    const recovery = event.details?.errorsRecovered === true ? 'recovered tool errors' : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${outcomeStatus ? statusPill(outcomeStatus) : ''}${Number.isInteger(toolErrorCount) ? `<code>${esc(toolErrorCount)} tool errors</code>` : ''}${recovery ? `<small>${esc(recovery)}</small>` : ''}</span>`;
  }
  if (event.type === 'skill.routed') {
    const count = Number.isInteger(event.details?.selectedCount) ? event.details.selectedCount : null;
    const fallback = event.details?.usedFallback === true ? 'fallback' : 'matched';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${count == null ? '' : `<code>${esc(count)} selected</code>`}<small>${esc(fallback)}</small></span>`;
  }
  if (event.type === 'skill.loaded') {
    const qualifiedName = event.details?.qualifiedName;
    const contentDigest = event.details?.contentDigest;
    const digestLabel = typeof contentDigest === 'string' ? `sha256:${contentDigest.slice(0, 12)}` : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${qualifiedName ? `<code>${esc(qualifiedName)}</code>` : ''}${digestLabel ? `<small>${esc(digestLabel)}</small>` : ''}</span>`;
  }
  if (event.type === 'skill.attributed') {
    const loadedSkillCount = Number.isInteger(event.details?.loadedSkillCount) ? event.details.loadedSkillCount : null;
    const outcomeStatus = typeof event.details?.outcomeStatus === 'string' ? event.details.outcomeStatus : '';
    const recoveryLabel = event.details?.errorsRecovered === true ? ' · recovered tool errors' : '';
    const outcomeLabel = outcomeStatus ? `${outcomeStatus}${recoveryLabel}` : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${loadedSkillCount == null ? '' : `<code>${esc(loadedSkillCount)} loaded</code>`}${outcomeLabel ? `<small>${esc(outcomeLabel)}</small>` : ''}</span>`;
  }
  if (event.type === 'memory.retrieved') {
    const candidateCount = Number.isInteger(event.details?.candidateCount) ? event.details.candidateCount : null;
    const selectedCount = Number.isInteger(event.details?.selectedCount) ? event.details.selectedCount : null;
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${candidateCount == null ? '' : `<code>${esc(candidateCount)} candidates</code>`}${selectedCount == null ? '' : `<small>${esc(selectedCount)} selected</small>`}</span>`;
  }
  if (event.type === 'memory.rendered') {
    const renderedCount = Number.isInteger(event.details?.renderedCount) ? event.details.renderedCount : null;
    const injected = event.details?.injected === true ? 'injected' : 'not injected';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${renderedCount == null ? '' : `<code>${esc(renderedCount)} rendered</code>`}<small>${esc(injected)}</small></span>`;
  }
  if (event.type === 'context.compacted') {
    const strategy = event.details?.strategy;
    const trigger = event.details?.trigger;
    const tokensFreed = event.details?.tokensFreed;
    const messagesBefore = event.details?.messagesBefore;
    const messagesAfter = event.details?.messagesAfter;
    const compactLabel = strategy && trigger ? `${strategy} · ${trigger}` : '';
    const tokenLabel = Number.isInteger(tokensFreed) && tokensFreed >= 0 ? `${formatCount(tokensFreed)} tokens freed` : '';
    const messageLabel = Number.isInteger(messagesBefore) && Number.isInteger(messagesAfter) ? `${formatCount(messagesBefore)} → ${formatCount(messagesAfter)} messages` : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${compactLabel ? `<code>${esc(compactLabel)}</code>` : ''}${tokenLabel ? `<small>${esc(tokenLabel)}</small>` : ''}${messageLabel ? `<small>${esc(messageLabel)}</small>` : ''}</span>`;
  }
  if (event.type === 'recovery.started') {
    const kind = event.details?.kind;
    const reason = event.details?.reason === 'context_overflow' ? 'context overflow' : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${kind ? `<code>${esc(kind)}</code>` : ''}${reason ? `<small>${esc(reason)}</small>` : ''}</span>`;
  }
  if (event.type === 'recovery.completed') {
    const outcome = event.details?.outcome;
    const tokensFreed = event.details?.tokensFreed;
    const tokenLabel = Number.isInteger(tokensFreed) && tokensFreed >= 0 ? `${formatCount(tokensFreed)} tokens freed` : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${outcome ? `<code>${esc(outcome)}</code>` : ''}${tokenLabel ? `<small>${esc(tokenLabel)}</small>` : ''}</span>`;
  }
  if (event.type === 'working_memory.observed') {
    const entries = event.details?.entries;
    const maxEntries = event.details?.maxEntries;
    const protectedTokens = event.details?.protectedTokens;
    const maxTokens = event.details?.maxTokens;
    const entryLabel = Number.isInteger(entries) && Number.isInteger(maxEntries) ? `${formatCount(entries)} / ${formatCount(maxEntries)} entries` : '';
    const tokenLabel = Number.isInteger(protectedTokens) && Number.isInteger(maxTokens) ? `${formatCount(protectedTokens)} / ${formatCount(maxTokens)} estimated tokens` : '';
    return `<span class="run-event-detail runtime-event"><span>${base}</span>${entryLabel ? `<code>${esc(entryLabel)}</code>` : ''}${tokenLabel ? `<small>${esc(tokenLabel)}</small>` : ''}<small>process-local</small></span>`;
  }
  return base;
}

function runtimeRunSelector() {
  if (!runtimeTraceStore.runs.length) return '';
  return `<div class="runtime-run-selector"><small>最近 Runs</small><div>${runtimeTraceStore.runs.map((run) => `<button class="${run.id === runtimeTraceStore.selectedRunId ? 'on' : ''}" onclick="selectRuntimeRun('${esc(run.id)}')"><b>${esc(run.title)}</b><span>${esc(run.source)} · ${esc(formatSnapshotTime(run.updatedAt))}</span></button>`).join('')}</div></div>`;
}

function runtimeTraceState(kind) {
  const labels = { skill: 'Skill Routing', retrieval: 'Memory Retrieval', injection: 'Memory Injection' };
  const label = labels[kind];
  const controls = `<div class="page-actions"><div class="intro"><b>真实 ${esc(label)}</b> · 来自 RunJournal；SSE 实时失效，Change Feed 仅作轮询后备，并保留手动刷新。</div><button class="snapshot-button" onclick="refreshRuntimeTrace()">刷新</button></div>`;
  if (runtimeTraceStore.phase === 'idle' || runtimeTraceStore.phase === 'loading') {
    return `${controls}<div class="card snapshot-state"><b>正在读取 ${esc(label)}…</b><p>先读取最近 Runs，再读取所选 Run 的严格白名单事件。</p></div>`;
  }
  if (runtimeTraceStore.phase === 'empty') {
    return `${controls}<div class="card empty runtime-empty"><b>暂无可选择的 Run</b><p>RunJournal 尚未记录当前 Workspace 的任务；历史不会回填。</p></div>`;
  }
  if (runtimeTraceStore.phase === 'error' || !runtimeTraceStore.detail) {
    return `${controls}<div class="card snapshot-state snapshot-error"><b>${esc(label)} 暂时不可用</b><p>${esc(runtimeTraceStore.error || '运行级事件尚未加载。')}</p><button class="snapshot-button" onclick="refreshRuntimeTrace()">Retry</button></div>`;
  }
  const detail = runtimeTraceStore.detail;
  const run = detail.run;
  const eventType = kind === 'skill' ? 'skill.routed' : (kind === 'retrieval' ? 'memory.retrieved' : 'memory.rendered');
  const event = detail.events.find((item) => item.type === eventType);
  const retrieval = detail.events.find((item) => item.type === 'memory.retrieved');
  const rendered = detail.events.find((item) => item.type === 'memory.rendered');
  const partial = runtimeTraceStore.phase === 'partial';
  const header = `${controls}${readSourceLine(detail.source)}${runtimeRunSelector()}<div class="runtime-selected-run"><span><b>${esc(run.title)}</b><small><code>${esc(run.id)}</code> · ${esc(run.source)} · ${esc(run.status)}</small></span>${statusPill(partial ? 'partial' : 'live')}</div>${partial ? '<div class="card snapshot-warning"><p>该来源或事件页为部分可用；仅展示已验证的安全事件。</p></div>' : ''}`;
  if (!event) {
    const message = kind === 'skill' ? 'This Run has no observed Skill Routing event.' : (kind === 'retrieval' ? 'This Run has no observed Memory Retrieval event.' : 'This Run has no observed Memory Injection event.');
    return `${header}<div class="card empty runtime-historical"><b>${esc(message)}</b><p>该 Run 可能早于当前 instrumentation，或本次生产路径没有执行该阶段；不会从目录或持久化 Memory 伪造结果。</p></div>`;
  }
  if (kind === 'skill') {
    const details = event.details || {};
    const selected = Array.isArray(details.selected) ? details.selected : [];
    const cards = selected.map((skill) => `<article class="tool-card runtime-skill"><div><code>${esc(skill.qualifiedName)}</code>${sourceTag(skill.source)}</div><p>${skill.directory ? `directory ${esc(skill.directory)} · ` : ''}score ${esc(skill.score)}</p></article>`).join('') || '<div class="card empty">该路由没有可展示的安全 Skill 名称。</div>';
    const truncation = details.selectedTruncated ? `<div class="card snapshot-warning"><p>安全展示 ${esc(selected.length)} / ${esc(details.selectedCount ?? '—')} 项；其余项目因数量上限或字段验证未返回。</p></div>` : '';
    return `${header}${metricTiles([[details.selectedCount ?? '—', '真实选择数'], [details.totalSkills ?? '—', '路由 Skill 总数'], [details.intentType ?? '—', 'Intent'], [details.actionType ?? '—', 'Action'], [details.usedFallback === true ? 'yes' : 'no', 'Fallback']])}${truncation}<div class="runtime-skill-grid">${cards}</div>`;
  }
  if (kind === 'retrieval') {
    const details = event.details || {};
    const renderedCount = rendered?.details?.renderedCount;
    return `${header}<div class="runtime-stages"><div><small>Candidates</small><b>${esc(details.candidateCount ?? '—')}</b></div><i>→</i><div><small>Selected</small><b>${esc(details.selectedCount ?? '—')}</b></div><i>→</i><div><small>Rendered</small><b>${esc(renderedCount ?? '—')}</b></div><span>/</span><div><small>Suppressed</small><b>${esc(details.suppressedCount ?? '—')}</b></div></div><div class="card runtime-facts"><b>No match ${statusPill(details.noMatch === true ? 'yes' : 'no')}</b><p>${details.noMatch === true ? `safe reason · ${esc(details.noMatchReason || 'other')}` : '真实检索产生了可用阶段结果；不展示 Memory 内容、ID、query 或 diagnostics。'}</p></div>`;
  }
  const details = event.details || {};
  const noMatch = retrieval?.details?.noMatch === true;
  return `${header}${metricTiles([[details.renderedCount ?? '—', 'Rendered'], [details.totalTokens ?? '—', 'Memory token estimate'], [details.controllerMode ?? '—', 'Controller mode'], [details.injected === true ? 'yes' : 'no', 'Injected']])}<div class="card runtime-facts"><b>最终注入状态 · ${details.injected === true ? 'injected' : 'not injected'}</b><p>${noMatch ? `Retrieval no-match · ${esc(retrieval.details.noMatchReason || 'other')}` : 'Token 仅为 Memory rendered prompt 的规范化估算，不属于 usage、cost 或模型 token 指标。'}</p></div>`;
}

function renderRunMetric(key, metric) {
  const labels = { cost: 'Cost', tokens: 'Tokens', duration: 'Model duration', toolCalls: 'Tool calls', errors: 'Observed failures', context: 'Context observations', recovery: 'Recovery observations', workingMemory: 'WorkingMemory process-local snapshot' };
  const status = metric?.status || 'unavailable';
  const value = metric?.value;
  let detail = 'no canonical data';
  if (key === 'tokens' && value) {
    detail = `Total ${formatCount(value.totalTokens)} · Input ${formatCount(value.inputTokens)} · Output ${formatCount(value.outputTokens)} · Cache ${formatCount(value.cacheReadTokens)} / ${formatCount(value.cacheCreationTokens)} · ${value.provenance || 'unavailable'}`;
  } else if (key === 'duration' && value) {
    detail = `${formatCount(value.observedCalls)} / ${formatCount(value.modelCalls)} observed · total ${formatDuration(value.totalMs)} · average ${formatDuration(value.averageMs)}`;
  } else if (key === 'cost') {
    detail = costMetricDetail(metric);
  } else if (key === 'toolCalls') {
    detail = toolMetricDetail(metric);
  } else if (key === 'errors') {
    detail = failureMetricDetail(metric);
  } else if (key === 'context') {
    detail = contextMetricDetail(metric, null);
  } else if (key === 'recovery') {
    detail = contextMetricDetail(null, metric);
  } else if (key === 'workingMemory') {
    detail = workingMemoryMetricDetail(metric);
  } else if (value != null && typeof value !== 'object') {
    detail = String(value);
  }
  return `<span><b>${esc(labels[key])}</b>${statusPill(status)}<small>${esc(detail)}</small></span>`;
}

function runToolSummary(tools) {
  if (!tools || tools.status === 'unavailable') return 'Tools unavailable';
  const suffix = tools.status === 'partial' ? ' · partial' : '';
  return `${formatCount(tools.observedCalls)} tools · ${formatCount(tools.errorCalls)} tool errors${suffix}`;
}

function runFailureSummary(failures) {
  if (!failures || failures.status === 'unavailable') return 'Failure coverage unavailable';
  const categories = [];
  if (failures.toolErrors) categories.push(`${formatCount(failures.toolErrors)} tool`);
  if (failures.modelFailures) categories.push(`${formatCount(failures.modelFailures)} model`);
  if (failures.runFailed) categories.push('run failed');
  if (failures.interrupted) categories.push('interrupted');
  if (failures.cancelled) categories.push('cancelled');
  return categories.length ? categories.join(' · ') : 'No observed failures';
}

function runContextSummary(context, workingMemory) {
  const parts = [];
  if (context?.status === 'partial') {
    parts.push(`${formatCount(context.compactions)} compact`, `${context.recoveries == null ? 'recovery unavailable' : `${formatCount(context.recoveries)} recovery observed`}`);
  } else {
    parts.push('context unavailable');
  }
  if (workingMemory?.observed) parts.push(`WM ${formatCount(workingMemory.entries)} / ${formatCount(workingMemory.maxEntries)} process-local`);
  else parts.push('WM unavailable');
  return parts.join(' · ');
}

function runDetailPanel(metricKeys) {
  if (!runDetailStore.runId) return '<div class="card empty run-detail-empty">选择一条 Run，在主区域查看真实且经过裁剪的生命周期事件。</div>';
  if (runDetailStore.phase === 'loading') return '<div class="card snapshot-state"><b>正在读取 Run 事件…</b><p>仅加载固定安全摘要，不返回 Prompt、tool input/output 或原始 payload。</p></div>';
  if (runDetailStore.phase === 'error' || !runDetailStore.data) {
    return `<div class="card snapshot-state snapshot-error"><b>Run 详情不可用</b><p>${esc(runDetailStore.error || '详情尚未加载。')}</p><button class="snapshot-button" onclick="selectRun('${esc(runDetailStore.runId)}')">重试</button></div>`;
  }
  const detail = runDetailStore.data;
  const run = detail.run;
  const metrics = metricKeys.map((key) => renderRunMetric(key, detail.metrics[key])).join('');
  const events = detail.events.map((event) => `<tr><td class="mono">${esc(event.sequence)}</td><td><code>${esc(event.type)}</code></td><td>${runEventSummary(event)}</td><td class="meta">${event.step == null ? '' : `step ${esc(event.step)} · `}${esc(formatSnapshotTime(event.timestamp))}</td></tr>`);
  const sessionAction = run.sessionId
    ? `<button class="snapshot-button run-session-link" onclick="openRunSession('${esc(run.sessionId)}')">查看 Session</button>`
    : '<span class="run-session-unlinked">未关联 Session</span>';
  const sessionNote = run.sessionId ? ` · session ${esc(String(run.sessionId).slice(0, 12))}` : ' · session unavailable';
  return `<section class="run-detail"><div class="run-detail-head"><div><b>${esc(run.title)}</b><small><code>${esc(run.id)}</code> · ${esc(run.source)}${sessionNote} · ${esc(run.eventCount)} events</small></div><div class="run-detail-actions">${sessionAction}${statusPill(run.status)}</div></div><div class="run-unavailable-metrics">${metrics}</div>${table(['序号', '事件', '摘要', '时间'], events)}${detail.events.length === 0 ? '<div class="card empty">该 Run 暂无可读取事件。</div>' : ''}${pageDiagnostics(detail.diagnostics)}${detail.page.hasMore ? `<button class="load-more" onclick="loadMoreRunEvents()" ${runDetailStore.loadingMore ? 'disabled' : ''}>${runDetailStore.loadingMore ? '加载中…' : '加载更多事件'}</button>` : ''}</section>`;
}

function runsPageBody(metricKeys) {
  if (runsStore.phase === 'idle' || runsStore.phase === 'loading') return '<div class="card snapshot-state"><b>正在扫描 RunJournal…</b><p>只读取当前 Workspace 的 canonical Run 记录。</p></div>';
  if (runsStore.phase === 'error' && !runsStore.items.length) {
    return `<div class="card snapshot-state snapshot-error"><b>Runs 暂时不可用</b><p>${esc(runsStore.error || 'RunJournal 尚未加载。')}</p><button class="snapshot-button" onclick="refreshRuns()">重试</button></div>`;
  }
  const rows = runsStore.items.map((run) => `<button class="run-row ${run.id === runDetailStore.runId ? 'selected' : ''}" onclick="selectRun('${esc(run.id)}')"><span><b>${esc(run.title)}</b><small><code>${esc(run.id)}</code> · ${esc(run.source)} · updated ${esc(formatSnapshotTime(run.updatedAt))}</small></span><span><b class="run-cost">${esc(formatNanoUsd(run.cost?.amountNanoUsd))}</b><small>${esc(statusText[run.cost?.status] || run.cost?.status || 'unavailable')} cost · ${esc(run.eventCount)} events · ${esc(run.cost?.pricedCalls ?? 0)} priced</small><small>${esc(runToolSummary(run.tools))} · ${esc(runFailureSummary(run.failures))}</small><small>${esc(runContextSummary(run.context, run.workingMemory))}</small></span>${statusPill(run.status)}</button>`).join('') || `<div class="card empty run-journal-empty"><b>暂无已记录的 Run</b><p>生命周期、Model Cost、Tool 与 Assistant 完成标记已接入 TUI、Headless 与 Gateway；仅显示启用本功能后的任务，历史运行未回填。</p></div>`;
  return `${readSourceLine(runsStore.source)}${runsStore.error ? `<div class="card snapshot-warning"><p>${esc(runsStore.error)}</p></div>` : ''}<div class="runs-master-detail"><div class="runs-master"><div class="stack">${rows}</div>${runsStore.page?.hasMore ? `<button class="load-more" onclick="loadMoreRuns()" ${runsStore.loadingMore ? 'disabled' : ''}>${runsStore.loadingMore ? '加载中…' : '加载更多 Runs'}</button>` : ''}${pageDiagnostics(runsStore.diagnostics)}</div>${runDetailPanel(metricKeys)}</div>`;
}

function observatorySelectedRun() {
  return observatoryStore.items.find(
    (item) => item.id === observatoryStore.selectedRunId,
  ) || observatoryStore.items[0] || null;
}

function observatoryRunFocusContent() {
  if (observatoryStore.phase === 'idle' || observatoryStore.phase === 'loading') {
    return '<div class="observatory-run-main snapshot-state"><b>正在定位最新 Run…</b><p>从当前 Workspace 的真实 RunJournal 读取有界摘要。</p></div>';
  }
  if (observatoryStore.phase === 'error' && !observatoryStore.items.length) {
    return `<div class="observatory-run-main snapshot-state snapshot-error"><b>Run 观测暂时不可用</b><p>${esc(observatoryStore.error || '最新 Run 尚未加载。')}</p><button class="snapshot-button" onclick="loadObservatory()">重试</button></div>`;
  }
  const run = observatorySelectedRun();
  if (!run) {
    return '<div class="observatory-run-main observatory-ledger-empty"><b>暂无保留 Run</b><p>新任务产生 canonical RunJournal 记录后会在这里出现；历史运行不会回填。</p></div>';
  }
  const detail = observatoryStore.detail?.run?.id === run.id
    ? observatoryStore.detail
    : null;
  const events = detail?.events || [];
  const latestEvent = events[events.length - 1] || null;
  const focusLabel = ['running', 'cancel_requested', 'queued'].includes(run.status)
    ? 'Current run'
    : 'Latest retained run';
  const eventDetail = latestEvent ? runEventSummary(latestEvent) : '';
  const detailCopy = observatoryStore.detailPhase === 'loading'
    ? '正在读取经过裁剪的事件详情…'
    : observatoryStore.detailPhase === 'error'
      ? esc(observatoryStore.error || '事件详情暂时不可用。')
      : latestEvent
        ? (eventDetail === esc(latestEvent.summary || 'Run event')
          ? `Latest persisted observation · ${esc(formatSnapshotTime(latestEvent.timestamp))}`
          : eventDetail)
        : '该 Run 暂无可读取事件。';
  const stageNumber = latestEvent?.sequence ?? run.eventCount ?? '—';
  return `<header class="observatory-section-heading"><div><span class="eyebrow">${esc(focusLabel)} · ${esc(String(run.id).slice(0, 18))}</span><h2>${esc(run.title)}</h2></div>${statusPill(run.status)}</header>
    <div class="observatory-run-main">
      <div class="observatory-run-identity"><code>${esc(run.id)}</code><p>Prompt、messages 与 Tool input/output 均不展示；这里只呈现持久化安全摘要。</p></div>
      <div class="observatory-run-stage"><span class="observatory-stage-count">${esc(stageNumber)}</span><div><small>${latestEvent ? esc(latestEvent.type) : 'run state'}</small><strong>${latestEvent ? esc(latestEvent.summary || 'Run event') : esc(statusText[run.status] || run.status)}</strong><p>${detailCopy}</p></div></div>
    </div>
    <dl class="observatory-run-facts">
      <div><dt>Events</dt><dd>${esc(run.eventCount ?? '—')}</dd></div>
      <div><dt>Cost</dt><dd>${esc(formatNanoUsd(run.cost?.amountNanoUsd))}</dd></div>
      <div><dt>Tools</dt><dd>${esc(runToolSummary(run.tools))}</dd></div>
      <div><dt>Source</dt><dd>${esc(run.source)}</dd></div>
    </dl>`;
}

function observatoryActivityItems() {
  const run = observatorySelectedRun();
  if (!run) return '<div class="observatory-ledger-empty">没有可展示的 Run 活动。</div>';
  if (observatoryStore.detailPhase === 'loading') {
    return '<div class="observatory-ledger-empty">正在读取 Activity trace…</div>';
  }
  if (!observatoryStore.detail?.events?.length) {
    return `<div class="observatory-ledger-empty">${esc(observatoryStore.error || '该 Run 暂无可读取事件。')}</div>`;
  }
  const events = observatoryStore.detail.events.slice(-6);
  return `<ol>${events.map((event, index) => {
    const failed = event.type.includes('failed') || event.type.includes('denied');
    const active = index === events.length - 1 && ['running', 'cancel_requested'].includes(run.status);
    const className = failed ? 'failed' : (active ? 'active' : 'complete');
    return `<li class="${className}"><span class="observatory-activity-node" aria-hidden="true"></span><div class="observatory-activity-copy"><small>${esc(formatSnapshotTime(event.timestamp))} · ${esc(event.type)}</small><strong>${esc(event.summary || 'Run event')}</strong>${runEventSummary(event)}</div></li>`;
  }).join('')}</ol>`;
}

function observatoryLedgerRows() {
  if (observatoryStore.phase === 'loading' && !observatoryStore.items.length) {
    return '<div class="observatory-ledger-empty">正在读取 Recent work…</div>';
  }
  if (!observatoryStore.items.length) {
    return `<div class="observatory-ledger-empty">${esc(observatoryStore.error || '暂无保留 Run。')}</div>`;
  }
  return `<div class="observatory-ledger-list">${observatoryStore.items.slice(0, 5).map((run) => `<button class="observatory-ledger-row" type="button" onclick="openObservatoryRun('${esc(run.id)}')"><span><strong>${esc(run.title)}</strong><small>${esc(run.id)} · ${esc(run.source)} · ${esc(run.eventCount)} events</small></span>${statusPill(run.status)}</button>`).join('')}</div>`;
}

function openObservatoryRun(runId) {
  if (!runId) return;
  location.hash = '#runs';
  loadRunDetail(runId, false);
}

function memoryCorroborationSpan(memory) {
  const success = Number(memory.corroboratedSuccessCount) || 0;
  const failure = Number(memory.corroboratedFailureCount) || 0;
  if (success + failure === 0) return '';
  const score = Number(memory.corroboratedUsefulnessScore) || 0;
  return ` · verified ${esc(success)}✓ ${esc(failure)}✗ (${esc(score.toFixed(2))})`;
}

function memoryRows(items) {
  return items.map((memory) => `<div class="memory-row">
    <div class="memory-score"><b>${esc(memory.scope)}</b><span>${esc(memory.tier)}</span></div>
    <div class="memory-copy"><small>${esc(memory.category)} · updated ${esc(formatSnapshotTime(memory.updatedAt))}</small><b><code>${esc(memory.id)}</code></b><p>${esc(memory.content)}</p>
      <div class="memory-entry-meta">${statusPill(memory.lifecycleStatus)} ${statusPill(memory.safetyStatus)} ${statusPill(memory.approvalStatus)}<span>${esc(memory.retrievalCount)} retrievals · ${esc(memory.injectionCount)} injections · usefulness ${esc(memory.usefulnessScore)}${memoryCorroborationSpan(memory)}</span>${memory.truncated ? '<span>content truncated</span>' : ''}</div>
    </div>
    <div class="memory-row-actions">${memory.contentHidden ? statusPill('held') : statusPill('live')}${memory.scope === 'project' ? `<button type="button" class="memory-delete-button" onclick="openProjectMemoryDeletion('${esc(memory.id)}', this)">删除</button>` : ''}</div>
  </div>`).join('') || '<div class="card empty">暂无 Memory 条目</div>';
}

function memoryScopeCards(summary, scopes) {
  return `<div class="pillar-grid">${Object.entries(MEMORY_SCOPES).map(([scope, meta]) => {
    const count = summary.byScope[scope];
    const source = scopes[scope] || { status: 'unavailable', location: meta.path };
    return `<a href="#memory/scopes" class="flow-box scope-card"><b>${esc(meta.label)} · ${count ?? '—'} ${statusPill(source.status)}</b><span>${esc(meta.description)}</span><code>${esc(source.location || meta.path)}</code><small>${esc(meta.sharing)} · read-only</small></a>`;
  }).join('')}</div>`;
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

function memoryTierCards(summary) {
  return `<div class="tier-grid">${Object.entries(MEMORY_TIERS).map(([tier, meta]) => {
    const count = summary.byTier[tier] ?? 0;
    return `<div class="tier-card"><small>${esc(tier)}</small><b>${count}</b><strong>${esc(meta.label)}</strong><p>${esc(meta.description)}</p></div>`;
  }).join('')}</div>`;
}

function pageDiagnostics(diagnostics) {
  if (!diagnostics?.length) return '';
  return `<div class="stack page-diagnostics">${diagnostics.map((item) => `<div class="card snapshot-warning"><b>${esc(item.source)} · ${esc(item.code)}</b><p>${esc(item.message)}</p></div>`).join('')}</div>`;
}

function readSourceLine(source) {
  if (!source) return '';
  return `<div class="read-source-line">${statusPill(source.status)}<span>${source.updatedAt ? `updated ${esc(formatSnapshotTime(source.updatedAt))}` : 'no source timestamp'}</span>${source.message ? `<span>${esc(source.message)}</span>` : ''}</div>`;
}

function historicalMcpOutcome(outcome) {
  return {
    request_succeeded: 'Request succeeded · historical fact',
    connection_failed: 'Connection failed · historical fact',
    request_failed: 'Request failed · historical fact',
  }[outcome] || 'Historical outcome unavailable';
}

function renderMcpCurrentRuntime(current) {
  if (!current || current.status === 'error' || current.reason === 'source_error') {
    return `<div class="connection-fact current error"><small>Current Gateway process</small><b>${statusPill('error')} Current process snapshot unavailable</b><span>Configuration and retained history remain independently visible.</span><button class="snapshot-button" onclick="refreshConnections()">Retry</button></div>`;
  }
  if (current.status !== 'live') {
    if (current.reason === 'not_registered') {
      return `<div class="connection-fact current"><small>Current Gateway process</small><b>${statusPill('unavailable')} No active registered client in this Gateway process</b><span>This is not an offline claim.</span></div>`;
    }
    if (current.reason === 'snapshot_limited') {
      return `<div class="connection-fact current"><small>Current Gateway process</small><b>${statusPill('unavailable')} Not present in the bounded snapshot; current state unknown</b><span>The current snapshot is limited; no state is inferred.</span></div>`;
    }
    return `<div class="connection-fact current"><small>Current Gateway process</small><b>${statusPill('unavailable')} Current process snapshot unavailable</b><span>No process-local source was injected into this Dashboard read model.</span></div>`;
  }
  const instances = `${formatCount(current.activeInstanceCount)} active instance${current.activeInstanceCount === 1 ? '' : 's'}`;
  const updated = `updated ${formatSnapshotTime(current.updatedAt)}`;
  if (current.state === 'ready') {
    const protocol = current.protocol ? `protocol ${current.protocol}` : 'protocol unavailable';
    return `<div class="connection-fact current ready"><small>Current Gateway process</small><b>${statusPill('ready')} Ready in this Gateway process</b><span>${esc(instances)} · ${esc(protocol)} · ${esc(updated)}</span></div>`;
  }
  if (current.state === 'starting') {
    return `<div class="connection-fact current starting"><small>Current Gateway process</small><b>${statusPill('starting')} Starting in this Gateway process</b><span>${esc(instances)} · ${esc(updated)}</span></div>`;
  }
  if (current.state === 'idle') {
    return `<div class="connection-fact current idle"><small>Current Gateway process</small><b>${statusPill('idle')} Registered, not started</b><span>${esc(instances)} · ${esc(updated)}</span></div>`;
  }
  const category = current.failureKind || 'other';
  return `<div class="connection-fact current error"><small>Current Gateway process</small><b>${statusPill('failed')} Failed in this Gateway process</b><span>Failure category ${esc(category)} · ${esc(instances)} · ${esc(updated)}</span></div>`;
}

function renderMcpHistoricalRuntime(runtime) {
  if (!runtime || runtime.status === 'error') {
    return `<div class="connection-fact historical error"><small>Retained Run history</small><b>Historical observations unavailable</b><span>Current connection status unavailable</span></div>`;
  }
  if (!runtime.observed) {
    return `<div class="connection-fact historical"><small>Retained Run history</small><b>No retained observation in the scanned window</b><span>Current connection status unavailable</span></div>`;
  }
  const clientPath = runtime.connectionAttempted === true
    ? 'A connection attempt was observed for that request'
    : 'An already-started client path was observed for that request';
  const protocol = runtime.observedProtocol ? ` · observed protocol ${runtime.observedProtocol}` : ' · observed protocol unavailable';
  return `<div class="connection-fact historical"><small>Last observed in retained Runs</small><b>${esc(historicalMcpOutcome(runtime.lastOutcome))}</b><span>${esc(formatSnapshotTime(runtime.lastObservedAt))} · ${esc(formatCount(runtime.retainedObservationCount))} retained observations</span><span>${esc(clientPath + protocol)}</span></div>`;
}

function renderMcpCoverage(data) {
  const coverage = data.coverage;
  const summary = data.summary;
  return `<div class="card mcp-coverage"><div>${statusPill(data.mcpRuntime.status)}<b>Historical MCP observation coverage</b></div><p>Retained Run observations are historical and independent from the current Gateway process snapshot.</p><div class="unavailable-list"><span>Scanned Runs · ${esc(formatCount(coverage.scannedRuns))} / ${esc(formatCount(coverage.retainedRuns))} retained</span><span>Observed configured · ${esc(formatCount(summary.observedConfiguredCount))} / ${esc(formatCount(summary.configuredMcpCount))}</span><span>Unobserved configured · ${esc(formatCount(summary.unobservedConfiguredCount))}</span><span>Unmatched historical servers · ${esc(formatCount(summary.unmatchedObservedServerCount))}</span><span>Run limit · ${esc(formatCount(coverage.runScanLimit))}</span><span>Per-Run event limit · ${esc(formatCount(coverage.eventScanLimitPerRun))}</span><span>Limited · ${coverage.limited === true ? 'yes' : 'no'}</span><span>Historical · partial</span></div></div>`;
}

function renderMcpCurrentCoverage(data) {
  const current = data.mcpCurrent;
  const coverage = current.coverage;
  const states = current.byState
    ? `idle ${formatCount(current.byState.idle)} · starting ${formatCount(current.byState.starting)} · ready ${formatCount(current.byState.ready)} · failed ${formatCount(current.byState.failed)}`
    : 'state totals unavailable';
  return `<div class="card mcp-coverage current-coverage"><div>${statusPill(current.status)}<b>Current Gateway process coverage</b></div><p>${esc(current.message)}</p><div class="unavailable-list"><span>Gateway process snapshot · ${current.checkedAt ? esc(formatSnapshotTime(current.checkedAt)) : 'unavailable'}</span><span>Scope · ${esc(coverage.scope)}</span><span>Cross-process · ${esc(coverage.crossProcess)}</span><span>Heartbeat · ${coverage.heartbeat === false ? 'false' : 'unavailable'}</span><span>Registered configured · ${esc(formatCount(data.summary.registeredConfiguredMcpCount))}</span><span>Active instances · ${esc(formatCount(data.summary.activeMcpInstanceCount))}</span><span>Ready configured · ${esc(formatCount(data.summary.liveMcpCount))}</span><span>${esc(states)}</span><span>Configured set · ${esc(coverage.configuredSet)}</span><span>Association · configured current workspace only</span><span>Unmatched keys · suppressed</span><span>Limited · ${coverage.limited === true ? 'yes' : 'no'}</span><span>No global state</span><span>No process control</span></div></div>`;
}

function sessionDetailPanel() {
  if (!sessionDetailStore.sessionId) return '<div class="card empty session-detail-empty">选择一条历史 Session，在主区域查看经过角色过滤和脱敏的只读消息。</div>';
  if (sessionDetailStore.phase === 'loading') return '<div class="card snapshot-state"><b>正在读取 Session 详情…</b><p>仅加载 user / assistant 消息；不会读取 system、tool 或 thinking 正文。</p></div>';
  if (sessionDetailStore.phase === 'error' || !sessionDetailStore.data) {
    return `<div class="card snapshot-state snapshot-error"><b>Session 详情不可用</b><p>${esc(sessionDetailStore.error || '详情尚未加载。')}</p><button class="snapshot-button" onclick="selectHistoricalSession('${esc(sessionDetailStore.sessionId)}')">重试</button></div>`;
  }
  const detail = sessionDetailStore.data;
  const session = detail.session;
  const messages = detail.messages.map((message) => `<article class="historical-message ${esc(message.role)}"><small>${esc(message.role)} · #${esc(message.index)}${message.truncated ? ' · truncated' : ''}</small><p>${esc(message.content)}</p></article>`).join('') || '<div class="card empty">该 Session 没有可展示的 user / assistant 消息。</div>';
  return `<section class="session-detail"><div class="session-detail-head"><div><b><code>${esc(session?.id || sessionDetailStore.sessionId)}</code></b><small>${esc(session?.visibleMessageCount ?? 0)} visible · ${esc(session?.messageCount ?? 0)} saved messages</small></div><div class="session-detail-actions">${readSourceLine(detail.source)}<button type="button" class="session-delete-button" onclick="openConversationDeletion('${esc(sessionDetailStore.sessionId)}', this)" ${chatStore.activeTargetSessionId === sessionDetailStore.sessionId && chatStore.activeTurnId ? 'aria-describedby="session-delete-busy-note"' : ''}>删除会话</button></div></div>${chatStore.activeTargetSessionId === sessionDetailStore.sessionId && chatStore.activeTurnId ? '<p id="session-delete-busy-note" class="session-delete-busy-note">此 Session 有活动 Turn；仍可查看权威预览，但不能确认删除。</p>' : ''}${messages}${pageDiagnostics(detail.diagnostics)}${detail.page.hasMore ? `<button class="load-more" onclick="loadMoreSessionMessages()" ${sessionDetailStore.loadingMore ? 'disabled' : ''}>${sessionDetailStore.loadingMore ? '加载中…' : '加载更多消息'}</button>` : ''}</section>`;
}

function sessionsPageBody() {
  if (sessionsStore.phase === 'idle' || sessionsStore.phase === 'loading') return '<div class="card snapshot-state"><b>正在读取历史 Session…</b><p>列表限制在当前 resolved workspace。</p></div>';
  if (sessionsStore.phase === 'error' && !sessionsStore.items.length) {
    return `<div class="card snapshot-state snapshot-error"><b>Sessions 暂时不可用</b><p>${esc(sessionsStore.error || 'Session 列表尚未加载。')}</p><button class="snapshot-button" onclick="refreshSessions()">重试</button></div>`;
  }
  const rows = sessionsStore.items.map((session) => `<button class="session-row ${session.id === sessionDetailStore.sessionId ? 'selected' : ''}" onclick="selectHistoricalSession('${esc(session.id)}')"><span><b>${esc(session.title)}</b><small><code>${esc(session.id)}</code> · ${esc(session.lastMessagePreview || 'no preview')}</small></span><span><b>${esc(session.messageCount)} msg</b><small>${esc(formatSnapshotTime(session.updatedAt))} · ${esc(session.status)}</small></span></button>`).join('') || '<div class="card empty">当前 Workspace 暂无历史 Session。</div>';
  return `${readSourceLine(sessionsStore.source)}${sessionsStore.error ? `<div class="card snapshot-warning"><p>${esc(sessionsStore.error)}</p></div>` : ''}<div class="sessions-master-detail"><div class="sessions-master"><div class="stack">${rows}</div>${sessionsStore.page?.hasMore ? `<button class="load-more" onclick="loadMoreSessions()" ${sessionsStore.loadingMore ? 'disabled' : ''}>${sessionsStore.loadingMore ? '加载中…' : '加载下一页'}</button>` : ''}${pageDiagnostics(sessionsStore.diagnostics)}</div>${sessionDetailPanel()}</div>`;
}

function renderMemoryRuntimeLifecycle() {
  if (opsStore.phase === 'idle') loadOps();
  if (opsStore.phase === 'idle' || opsStore.phase === 'loading') {
    return '<div class="card snapshot-state"><b>正在读取 Context / WorkingMemory runtime observations…</b><p>来自 /api/v1/ops 的只读 RunJournal 聚合；不会读取当前进程 WorkingMemory。</p></div>';
  }
  if (opsStore.phase === 'error' || !opsStore.data) {
    return `<div class="card snapshot-state snapshot-error"><b>Context runtime 暂时不可用</b><p>${esc(opsStore.error || 'Runtime observations 尚未加载。')}</p><button class="snapshot-button" onclick="refreshOps()">重试</button></div>`;
  }
  return `${renderContextBreakdown(opsStore.data.context, opsStore.data.recovery, opsStore.data.contextBreakdown)}${renderWorkingMemoryRuntime(opsStore.data.workingMemory)}`;
}

function renderMemoryApprovals() {
  const store = memoryApprovalStore;
  if (store.phase === 'idle' || (store.phase === 'loading' && !store.items.length)) {
    return '<div class="card snapshot-state"><b>正在读取持久 Memory 待审批项…</b><p>内容只在内存中短暂展示；审批权威仍是当前 Workspace 的 Memory store。</p></div>';
  }
  if (store.phase === 'error' && !store.items.length) {
    return `<div class="card snapshot-state snapshot-error" role="alert"><b>Memory 审批暂时不可用</b><p>${esc(store.error || fixedMemoryApprovalError())}</p><button class="snapshot-button" type="button" onclick="loadMemoryApprovals()">手动刷新</button></div>`;
  }
  if (store.phase === 'empty' && !store.items.length) {
    return '<div class="card memory-approval-empty"><b>当前没有待审批的持久记忆</b><p>此页仅处理持久记忆的批准或拒绝；不会编辑正文，也不会自动作出决定。</p><button class="snapshot-button" type="button" onclick="loadMemoryApprovals()">手动刷新</button></div>';
  }

  let selected = store.items.find((item) => item.memoryId === store.selectedMemoryId) || store.items[0] || null;
  if (selected && store.selectedMemoryId !== selected.memoryId) store.selectedMemoryId = selected.memoryId;
  if (!selected) {
    return `<div class="card snapshot-state snapshot-error" role="alert"><b>Memory 审批状态不可用</b><p>${esc(store.error || fixedMemoryApprovalError('invalid_response'))}</p><button class="snapshot-button" type="button" onclick="loadMemoryApprovals()">手动刷新</button></div>`;
  }
  const acting = store.actingMemoryId === selected.memoryId;
  const approveEnabled = memoryApprovalActionAvailable(selected, 'approve');
  const rejectEnabled = memoryApprovalActionAvailable(selected, 'reject');
  const denyOnly = !canApproveMemory(selected);
  const notice = store.error
    ? `<div class="memory-approval-notice error" role="alert"><p>${esc(store.error)}</p><button class="snapshot-button" type="button" onclick="loadMemoryApprovals()">手动刷新</button></div>`
    : store.diagnostics.length
      ? '<div class="memory-approval-notice" role="status"><p>待审批列表已按安全上限裁剪；当前只显示有界结果，请处理当前项目后刷新。</p></div>'
      : '';
  const rows = store.items.map((item) => {
    const active = item.memoryId === selected.memoryId;
    return `<button type="button" class="memory-approval-row ${active ? 'on' : ''}" data-memory-approval-select="${esc(item.memoryId)}" onclick="selectMemoryApproval('${esc(item.memoryId)}')" aria-pressed="${active ? 'true' : 'false'}"><span><b>${esc(item.category)}</b>${statusPill(item.safetyStatus)}</span><small>${esc(item.scope)} · ${esc(item.tier)} · ${esc(item.source)} · ${esc(formatSnapshotTime(item.createdAt))}</small><code>${esc(item.memoryId)}</code></button>`;
  }).join('');
  const safetyNote = denyOnly
    ? '<p class="memory-approval-deny-only">该审查不完整、被截断、已隐藏或风险过高，只能拒绝。</p>'
    : '<p class="memory-approval-safe">内容已通过当前安全投影，可批准或拒绝。</p>';
  return `${notice}<div class="page-actions"><div class="intro"><b>持久记忆审批 · read-write</b> · 批准后才会进入 Retrieval / Injection；每次决定绑定当前 review revision，结果由权威 GET 重新确认且不会自动重发。</div><button class="snapshot-button" type="button" onclick="loadMemoryApprovals()" ${acting ? 'disabled' : ''}>手动刷新</button></div><section class="memory-approval-workspace" aria-label="待审批 Memory" aria-live="polite" aria-busy="${acting ? 'true' : 'false'}"><div class="memory-approval-master"><div class="memory-approval-master-head"><b>${esc(store.items.length)} 项待审批</b><small>workspace-local authority</small></div>${rows}</div><article class="memory-approval-detail"><header><div><b>${esc(selected.category)}</b><code>${esc(selected.memoryId)}</code></div>${statusPill(selected.risk)}</header><dl><dt>Scope</dt><dd>${esc(selected.scope)} · ${esc(selected.scopeKind)}</dd><dt>Tier</dt><dd>${esc(selected.tier)}</dd><dt>Source</dt><dd>${esc(selected.source)}</dd><dt>Created</dt><dd>${esc(formatSnapshotTime(selected.createdAt))}</dd></dl><div class="memory-approval-preview"><span>安全内容预览</span><pre><code>${esc(selected.review.contentPreview)}</code></pre></div>${safetyNote}<small class="memory-approval-revision">review revision · ${esc(selected.reviewRevision)}</small><div class="memory-approval-actions">${denyOnly ? '' : `<button type="button" class="memory-approval-approve" data-memory-approval-decision="approve" onclick="decideMemoryApproval('${esc(selected.memoryId)}', '${esc(selected.reviewRevision)}', 'approve')" ${approveEnabled ? '' : 'disabled aria-disabled="true"'}>${acting ? '处理中…' : '批准并启用'}</button>`}<button type="button" class="memory-approval-reject" data-memory-approval-decision="reject" onclick="decideMemoryApproval('${esc(selected.memoryId)}', '${esc(selected.reviewRevision)}', 'reject')" ${rejectEnabled ? '' : 'disabled aria-disabled="true"'}>${acting ? '处理中…' : '拒绝'}</button></div></article></section>`;
}

function renderSystemSummary() {
  if (systemStore.phase === 'idle' || systemStore.phase === 'loading') {
    return '<div class="card snapshot-state"><b>正在读取 System 摘要…</b><p>仅返回版本、运行平台、Workspace 身份和语义化模块状态。</p></div>';
  }
  if (systemStore.phase === 'error' || !systemStore.data) {
    return `<div class="card snapshot-state snapshot-error"><b>System 暂时不可用</b><p>${esc(systemStore.error || 'System 摘要尚未加载。')}</p><button class="snapshot-button" onclick="refreshSystem()">重试</button></div>`;
  }
  const data = systemStore.data;
  return `<div class="page-actions">${readSourceLine(data.source)}<button class="snapshot-button" onclick="refreshSystem()">刷新 System</button></div>${systemStore.error ? `<div class="card snapshot-warning"><p>${esc(systemStore.error)}</p></div>` : ''}<div class="card accent-card"><b>${esc(data.application.name)} · ${esc(data.application.version)}</b><p>Dashboard schema ${esc(data.application.dashboardSchemaVersion)} · ${statusPill('read-only')}</p></div>${metricTiles([[data.runtime.pythonVersion, 'Python'], [data.runtime.platform, '平台'], [data.runtime.architecture, '架构'], [data.runtime.processMode, '进程模式']])}<h2>Workspace</h2><div class="card"><b>${esc(data.workspace.name)} ${statusPill(data.workspace.status)}</b><p><code>${esc(data.workspace.id)}</code> · 不展示本机绝对路径或原始配置。</p></div><h2>功能状态</h2>${table(['模块', '状态'], Object.entries(data.features).map(([name, status]) => `<tr><td><code>${esc(name)}</code></td><td>${statusPill(status)}</td></tr>`))}<h2>只读存储来源</h2>${table(['来源', '状态', '写能力'], Object.entries(data.storage).map(([name, storage]) => `<tr><td><code>${esc(name)}</code></td><td>${statusPill(storage.status)}</td><td class="meta">${storage.writable == null ? 'not inspected / unavailable' : esc(storage.writable)}</td></tr>`))}<div class="card unavailable-card"><b>Run 执行轨迹、模型用量、canonical Cost、Tool 与 Failure 聚合已接入</b><p>TUI、Headless 与 Gateway 记录 lifecycle-model-usage-cost-tool-assistant-skill-memory-context；Context/WorkingMemory 当前仅为 Run-level partial 观测，Dashboard 不做跨 Run 聚合。SSE live refresh 已接入；MCP runtime 与 Dashboard 业务写操作仍保持既有边界。</p></div>${pageDiagnostics(data.diagnostics)}`;
}

function formatDataHealthBytes(value) {
  if (!Number.isSafeInteger(value) || value < 0) return '—';
  if (value < 1024) return `${value.toLocaleString('zh-CN')} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MiB`;
}

function renderDataHealthPanel() {
  const heading = '<div class="data-health-heading"><div><h2>Data Health / 数据健康</h2><p>持久化总账 · bounded read-only inspection</p></div><button class="snapshot-button" type="button" onclick="refreshDataHealth()">手动刷新</button></div>';
  const boundary = '<div class="card data-health-boundary"><b>只读边界</b><p>本阶段没有删除、清理、修复或重置功能；planned 不代表现在已经能删除；process-local 状态不属于磁盘持久化事实。</p></div>';
  if (dataHealthStore.phase === 'idle' || (dataHealthStore.phase === 'loading' && !dataHealthStore.data)) {
    return `${heading}${boundary}<div class="card snapshot-state"><b>正在读取数据健康快照…</b><p>仅检查固定根目录中的元数据、计数和字节数，不读取或展示正文。</p></div>`;
  }
  if (dataHealthStore.phase === 'error' || !dataHealthStore.data) {
    return `${heading}${boundary}<div class="card snapshot-state snapshot-error" role="alert"><b>Data Health 暂时不可用</b><p>${esc(dataHealthStore.error || '数据健康快照尚未加载。')}</p><button class="snapshot-button" type="button" onclick="refreshDataHealth()">重试</button></div>`;
  }
  const data = dataHealthStore.data;
  const scopeLabels = {
    workspace: 'Workspace', user: 'User', local: 'Local',
    configuration: 'Configuration', process: 'Process',
  };
  const dispositionLabels = {
    planned: '9A-2 planned', excluded: '明确排除', 'not-applicable': '不适用',
  };
  const cards = data.stores.map((store) => `<article class="data-health-store status-${esc(store.status)}"><header><code>${esc(store.id)}</code>${statusPill(store.status)}</header><div class="data-health-store-facts"><span>${esc(scopeLabels[store.scope])}</span><span>${esc(store.durability)}</span><span>${esc(dispositionLabels[store.resetDisposition])}</span></div><dl><dt>记录</dt><dd>${esc(formatCount(store.recordCount))}</dd><dt>占用</dt><dd>${esc(formatDataHealthBytes(store.byteCount))}</dd><dt>更新</dt><dd>${store.updatedAt ? esc(formatSnapshotTime(store.updatedAt)) : '—'}</dd></dl><p>${esc(store.message)}</p></article>`).join('');
  const planned = data.maintenancePlan.eligibleStoreIds.map((id) => `<code>${esc(id)}</code>`).join('');
  const excluded = data.maintenancePlan.excludedStoreIds.map((id) => `<code>${esc(id)}</code>`).join('');
  const blockers = data.maintenancePlan.blockers.length
    ? `<div class="data-health-blockers"><b>规划阻塞项</b>${data.maintenancePlan.blockers.map((item) => `<span>${esc(item.storeId)} · ${esc(item.code)}</span>`).join('')}</div>`
    : '<p class="data-health-plan-note">当前快照没有发现规划阻塞项；这不提供任何维护执行能力。</p>';
  const diagnostics = data.diagnostics.length
    ? `<div class="card snapshot-warning data-health-diagnostics"><b>有界检查发现 ${esc(data.diagnostics.length)} 项问题</b>${data.diagnostics.map((item) => `<p><code>${esc(item.storeId)}</code> · ${esc(item.message)}</p>`).join('')}</div>`
    : '';
  const stateNote = dataHealthStore.phase === 'empty'
    ? '<div class="card empty"><b>当前固定数据根目录没有已知记录</b><p>Store 仍按 scope 和 durability 展示；空状态不代表进程内状态或配置不存在。</p></div>'
    : '';
  return `${heading}${boundary}${dataHealthStore.error ? `<div class="card snapshot-warning" role="alert"><p>保留上一次安全快照；${esc(dataHealthStore.error)}</p><button class="snapshot-button" type="button" onclick="refreshDataHealth()">重试</button></div>` : ''}${metricTiles([[data.summary.storeCount, 'Store'], [formatCount(data.summary.knownRecordCount), '已知记录'], [formatDataHealthBytes(data.summary.knownByteCount), '已知占用'], [data.summary.issueCount, '问题 Store', data.status]])}${stateNote}${diagnostics}<div class="data-health-grid">${cards}</div><div class="card data-health-plan"><header><div><b>Batch 9A-2 维护规划</b><small>${esc(data.maintenancePlan.status)} · destructive actions unavailable</small></div>${statusPill(data.status)}</header><section><b>Workspace 候选</b><div>${planned}</div><p>只表示未来边界候选；本页面不生成删除路径，也不执行维护。</p></section><section><b>User / configuration / source 明确排除</b><div>${excluded}</div></section>${blockers}</div>`;
}

function skillEvidencePanel(evidence) {
  if (!evidence || evidence.status === 'unavailable' || !evidence.ledger) {
    return `<div class="card unavailable-card"><b>Skill shadow evidence unavailable</b><p>${esc(evidence?.message || 'Retained RunJournal evidence could not be read.')}</p></div>`;
  }
  const ledger = evidence.ledger;
  const evaluations = ledger.evaluations || [];
  const excluded = Object.values(ledger.excludedRuns || {}).reduce((total, value) => total + value, 0);
  const cards = evaluations.map((evaluation) => {
    const skill = evaluation.skill || {};
    const profile = evaluation.profile || {};
    const treatment = evaluation.treatment || {};
    const control = evaluation.control || {};
    const delta = typeof evaluation.goalAchievementDelta === 'number' ? `${Math.round(evaluation.goalAchievementDelta * 1000) / 10} pp` : 'not comparable';
    const digest = typeof skill.contentDigest === 'string' ? `sha256:${skill.contentDigest.slice(0, 12)}` : 'digest unavailable';
    const verification = treatment.verification || {};
    const user = treatment.userSignal || {};
    return `<article class="tool-card skill-evidence-card"><div><code>${esc(skill.qualifiedName || 'unknown Skill')}</code>${statusPill(evaluation.shadowStatus || 'insufficient_evidence')}</div><p>${esc(profile.intentType || 'unknown')} / ${esc(profile.actionType || 'unknown')} · ${esc(digest)}</p><div class="skill-meta"><span>loaded ${esc(treatment.runs ?? 0)} · goal ${esc(treatment.goalAchievements ?? 0)}</span><span>not loaded ${esc(control.runs ?? 0)} · goal ${esc(control.goalAchievements ?? 0)}</span><span>verified ${esc(verification.passedRuns ?? 0)} / ${esc(verification.observedRuns ?? 0)}</span><span>user accept ${esc(user.acceptedRuns ?? 0)} · correct ${esc(user.correctedRuns ?? 0)} · reject ${esc(user.rejectedRuns ?? 0)}</span><span>delta ${esc(delta)}</span><span>${evaluation.sampleGatePassed === true ? 'sample gate passed' : 'sample gate pending'}</span><span>promotion locked</span></div></article>`;
  }).join('') || '<div class="card empty"><b>No comparable Skill cohorts yet</b><p>New canonical task outcomes and versioned routing observations will populate this shadow ledger over time.</p></div>';
  return `<section class="skill-evidence"><div class="page-actions"><div class="intro"><b>Cross-Run Skill evidence · shadow only</b> · ${esc(evidence.message)}</div>${statusPill(evidence.status)}</div>${metricTiles([[ledger.scannedRuns, 'Runs scanned'], [ledger.eligibleTreatmentRuns, 'single-Skill treatment'], [ledger.eligibleControlRuns, 'no-Skill controls'], [excluded, 'excluded Runs']])}<div class="runtime-skill-grid">${cards}</div>${ledger.runsTruncated || ledger.evaluationsTruncated ? '<div class="card snapshot-warning"><p>Evidence is bounded; this snapshot is partial.</p></div>' : ''}</section>`;
}

function skillVersionPanel(wrapper) {
  if (!wrapper || wrapper.status === 'unavailable' || !wrapper.ledger) {
    return `<div class="card unavailable-card"><b>Skill version history unavailable</b><p>${esc(wrapper?.message || 'Project version history could not be read.')}</p></div>`;
  }
  const ledger = wrapper.ledger;
  const cards = ledger.versions.map((version) => {
    const skill = version.skill || {};
    const gates = version.evaluation?.gates || [];
    const gateRows = gates.map((gate) => `<span>${esc(gate.name)} ${statusPill(gate.status)} · ${esc(gate.reason)}</span>`).join('');
    const parent = version.parentVersionId ? `parent ${version.parentVersionId.slice(0, 15)}` : 'root observation';
    const candidate = version.evaluation?.promotionCandidate === true ? 'shadow candidate · execution locked' : 'promotion evidence incomplete or failed';
    return `<article class="tool-card skill-version-card"><div><code>${esc(skill.qualifiedName || 'unknown Skill')}</code>${version.catalogCurrent ? statusPill('current') : statusPill('historical')}</div><p>${esc(version.versionId)} · sha256:${esc((skill.contentDigest || '').slice(0, 12))}</p><div class="skill-meta"><span>${esc(parent)}</span><span>status ${esc(version.status)}</span><span>profiles ${esc(version.evaluation?.evidenceProfiles ?? 0)}</span><span>${esc(candidate)}</span><span>promotion locked</span><span>rollback execution locked</span>${gateRows}</div></article>`;
  }).join('') || '<div class="card empty"><b>No observed Skill versions yet</b><p>A runtime catalog observation will create immutable digest lineage; Dashboard reads do not write it.</p></div>';
  return `<section class="skill-version-ledger"><div class="page-actions"><div class="intro"><b>Skill version lineage · read-only</b> · ${esc(wrapper.message)}</div>${statusPill(wrapper.status)}</div>${metricTiles([[ledger.evaluation.versionCount, 'observed versions'], [ledger.evaluation.promotionCandidateCount, 'promotion candidates'], [ledger.versions.filter((item) => item.catalogCurrent).length, 'catalog current'], [0, 'executable actions']])}<div class="runtime-skill-grid">${cards}</div></section>`;
}

const VIEWS = {
  overview() {
    if (snapshotStore.phase === 'loading') {
      return `<div class="core-page observatory-overview"><div class="card snapshot-state"><b>正在读取本地 Snapshot…</b><p>只读加载 Workspace、Session、Memory、Skill 与 Gateway 状态。</p></div></div>`;
    }
    if (snapshotStore.phase === 'error' || !snapshotStore.data) {
      return `<div class="core-page observatory-overview"><div class="card snapshot-state snapshot-error"><b>Overview 暂时不可用</b><p>${esc(snapshotStore.error || 'Snapshot 尚未加载。')}</p><button class="snapshot-button" onclick="refreshDashboardSnapshot()">重试</button></div></div>`;
    }

    const snapshot = snapshotStore.data;
    const overview = snapshot.overview;
    const memoryCount = overview.memory.totalCount ?? `${overview.memory.knownCount}+`;
    const mcpCount = overview.connections.mcp.configuredCount ?? '—';
    const usage = overview.usage;
    const modelCalls = (usage.providerCalls || 0) + (usage.estimatedCalls || 0) + (usage.unavailableCalls || 0);
    return `<div class="core-page observatory-overview">
      <section class="observatory-band" aria-label="Workspace status">
        <div class="observatory-band-lead"><span class="status-marker" aria-hidden="true"></span><span><strong>${esc(snapshot.workspace.name)}</strong><small>${esc(overview.connections.gateway.status)} · ${esc(snapshot.status)} · read-only</small></span></div>
        <dl class="observatory-band-inventory">
          <div><dt>Sessions</dt><dd>${esc(overview.sessions.count ?? '—')}</dd></div>
          <div><dt>Memories</dt><dd>${esc(memoryCount)}</dd></div>
          <div><dt>Skills</dt><dd>${esc(overview.skills.count ?? '—')}</dd></div>
          <div><dt>Runs</dt><dd>${esc(overview.runs.count ?? '—')}</dd></div>
        </dl>
        <div class="observatory-band-caption"><code>${esc(snapshot.workspace.id)}</code><span>MCP ${esc(mcpCount)} configured · absolute path hidden</span><button class="snapshot-button" onclick="refreshDashboardSnapshot(); loadObservatory()">刷新</button></div>
      </section>
      <div class="observatory-grid">
        <div class="observatory-primary">
          <article class="observatory-run-focus" aria-label="Current Run observation">${observatoryRunFocusContent()}</article>
          <section class="observatory-signals" aria-labelledby="observatory-signals-title">
            <header class="observatory-section-heading"><div><span class="eyebrow">Retained RunJournal · historical partial</span><h2 id="observatory-signals-title">Signals</h2></div>${statusPill(usage.status)}</header>
            ${metricTiles([
              [formatCount(usage.inputTokens), 'Input tokens'],
              [formatCount(usage.outputTokens), 'Output tokens'],
              [formatCount(usage.cacheReadTokens), 'Cache read tokens'],
              [formatCount(usage.cacheCreationTokens), 'Cache creation tokens'],
              [formatDuration(usage.durationMs), 'Model duration'],
              costMetricTile(usage.cost),
              toolMetricTile(usage.tools),
              failureMetricTile(usage.failures),
              contextMetricTile(overview.context),
              workingMemoryMetricTile(overview.workingMemory),
            ])}
            <div class="observatory-signal-copy"><p>${esc(modelCalls)} completed model observations: Provider ${esc(usage.providerCalls)} · Estimated ${esc(usage.estimatedCalls)} · Unavailable ${esc(usage.unavailableCalls)}。Token provenance ${esc(usage.provenance)}。${esc(costMetricDetail(usage.cost))}</p><p>${esc(toolMetricDetail(usage.tools))}。${esc(failureMetricDetail(usage.failures))}。${esc(contextMetricDetail(overview.context, overview.recovery))}。${esc(workingMemoryMetricDetail(overview.workingMemory))}。</p></div>
          </section>
        </div>
        <div class="observatory-secondary">
          <section class="observatory-activity" aria-labelledby="observatory-activity-title">
            <header class="observatory-section-heading"><div><span class="eyebrow">Execution trace</span><h2 id="observatory-activity-title">Activity</h2></div>${observatoryStore.detail?.run ? statusPill(observatoryStore.detail.run.status) : ''}</header>
            ${observatoryActivityItems()}
          </section>
          <section class="observatory-ledger" aria-labelledby="observatory-ledger-title">
            <header class="observatory-section-heading"><div><span class="eyebrow">Workspace ledger</span><h2 id="observatory-ledger-title">Recent work</h2></div><a class="meta" href="#runs">全部 Runs</a></header>
            ${observatoryLedgerRows()}
          </section>
        </div>
      </div>
      <details class="observatory-disclosure">
        <summary>数据来源与观测边界</summary>
        <div><div class="usage-summary"><div><b>Retained RunJournal · historical partial</b>${statusPill(usage.cost?.status || 'unavailable')}${statusPill(usage.tools?.status || 'unavailable')}${statusPill(usage.failures?.status || 'unavailable')}${statusPill(overview.context?.status || 'unavailable')}${statusPill(overview.workingMemory?.status || 'unavailable')}</div><div class="unavailable-list"><span>Coverage · retained-run-journal</span><span>Historical coverage · partial</span><span>Scope · lifecycle-model-usage-duration-cost-tool-failure-context-working-memory</span><span>Context / WorkingMemory · Run-level partial observations only</span><span>Cost / Tool / Failure · persisted observations only</span></div></div>${renderSnapshotSources(snapshot)}${renderSnapshotDiagnostics(snapshot)}${pageDiagnostics(observatoryStore.diagnostics)}</div>
      </details>
    </div>`;
  },

  runs() {
    const metricKeys = ['tokens', 'duration', 'cost', 'toolCalls', 'errors', 'context', 'recovery', 'workingMemory'];
    const statusFilters = [['', '全部'], ['queued', '排队'], ['running', '运行中'], ['completed', '完成'], ['failed', '失败'], ['interrupted', '中断']];
    const sourceFilters = [['', '全部'], ['tui', 'TUI'], ['headless', 'Headless'], ['gateway', 'Gateway'], ['unknown', 'Unknown']];
    const controls = `<div class="catalog-filters"><div class="catalog-filter"><small>status</small><div class="scope-filters">${statusFilters.map(([value, label]) => `<button class="${runsStore.filters.status === (value || null) ? 'on' : ''}" onclick="setRunStatusFilter('${esc(value)}')">${esc(label)}${value ? ` · ${esc(runsStore.summary?.byStatus?.[value] ?? 0)}` : ''}</button>`).join('')}</div></div><div class="catalog-filter"><small>source</small><div class="scope-filters">${sourceFilters.map(([value, label]) => `<button class="${runsStore.filters.source === (value || null) ? 'on' : ''}" onclick="setRunSourceFilter('${esc(value)}')">${esc(label)}</button>`).join('')}</div></div></div>`;
    return `<div class="core-page runs-observatory"><div class="page-actions"><div class="intro"><b>真实 RunJournal · read-only</b> · SSE 是主要失效通道，Change Feed 轮询仅在连接不可用时后备；保留手动刷新。</div><button class="snapshot-button" onclick="refreshRuns()">刷新</button></div>${controls}<details class="observatory-disclosure run-coverage"><summary>Run instrumentation 与安全展示边界</summary><div class="card unavailable-card"><b>Lifecycle + Model Usage + Cost + Tool + Assistant + Skill + Memory instrumentation live · Context + WorkingMemory observation partial · historical partial</b><p>Model 表示每次真实 <code>_model_next()</code> 请求边界；Cost、Tool 与 Failure 只展示同一 Run 内持久化 canonical 观测，不在 Dashboard 重算或推断。Context/WorkingMemory 仅显示已接入路径的 Run-level、process-local 观测。这里不表示 Provider 当前在线，也不保存 Prompt、messages、output 或 Tool operation ID。Tool input/output is never displayed.</p><div class="unavailable-list"><span>Scope · lifecycle-model-usage-cost-tool-assistant-skill-memory-context</span><span>Context · partial</span><span>WorkingMemory · partial / process-local</span><span>Usage / duration / Cost / Tool / Failure observation · live</span><span>Missing Cost · never shown as zero</span><span>Missing Tool · never shown as zero</span><span>Failure categories · never collapsed into one total</span><span>Historical Runs · not backfilled</span></div></div></details>${runsPageBody(metricKeys)}</div>`;
  },

  sessions() {
    return `<div class="core-page sessions-observatory"><div class="page-actions"><div class="intro"><b>真实 Session 历史 · read-only content / deletion managed</b> · 仅显示当前 Workspace；删除必须先读取完整范围并再次明确确认。</div><button class="snapshot-button" onclick="refreshSessions()">刷新</button></div>${sessionsPageBody()}</div>`;
  },

  memory(_, sub = 'overview') {
    const shell = (content) => `<div class="core-page memory-observatory">${content}</div>`;
    sub = sub || 'overview';
    const data = memoryStore.data;
    const knownCount = data?.summary?.knownTotal ?? null;
    const approvalCount = ['live', 'empty', 'partial', 'error'].includes(memoryApprovalStore.phase)
      ? memoryApprovalStore.items.length
      : null;
    const tabs = [
      ['overview', '概览'],
      ['scopes', '作用域', knownCount],
      ['approvals', '待审批', approvalCount],
      ['retrieval', '检索'],
      ['injection', '注入'],
      ['lifecycle', '生命周期'],
    ];
    if (sub === 'approvals') {
      return shell(subtabBar('memory', tabs, sub) + renderMemoryApprovals());
    }
    if (sub === 'retrieval') {
      return shell(subtabBar('memory', tabs, sub) + runtimeTraceState('retrieval'));
    }
    if (sub === 'injection') {
      return shell(subtabBar('memory', tabs, sub) + runtimeTraceState('injection'));
    }
    if (memoryStore.phase === 'idle' || memoryStore.phase === 'loading') {
      return shell(subtabBar('memory', tabs, sub) + '<div class="card snapshot-state"><b>正在读取持久 Memory…</b><p>User / Project / Local scope 独立加载且保持只读。</p></div>');
    }
    if (memoryStore.phase === 'error' || !data) {
      return shell(subtabBar('memory', tabs, sub) + `<div class="card snapshot-state snapshot-error"><b>Memory 暂时不可用</b><p>${esc(memoryStore.error || 'Memory 尚未加载。')}</p><button class="snapshot-button" onclick="refreshMemory()">重试</button></div>`);
    }
    const controls = `<div class="page-actions">${readSourceLine(data.source)}<button class="snapshot-button" onclick="refreshMemory()">刷新</button></div>${memoryStore.error ? `<div class="card snapshot-warning"><p>${esc(memoryStore.error)}</p></div>` : ''}`;
    let body = '';
    if (sub === 'scopes') {
      const filterButtons = `<div class="scope-filters"><button class="${memoryStore.filters.scope == null ? 'on' : ''}" onclick="setMemoryScopeFilter(null)">全部</button>${Object.entries(MEMORY_SCOPES).map(([scope, meta]) => `<button class="${memoryStore.filters.scope === scope ? 'on' : ''}" onclick="setMemoryScopeFilter('${esc(scope)}')">${esc(meta.label)}</button>`).join('')}</div>`;
      body = `<div class="intro">Scope 决定真实持久化语义；页面不暴露开发者绝对路径。只有 Project 条目提供严格确认删除；User / Local 无删除入口，持久记忆审批在“待审批”子页完成。</div>${filterButtons}${Object.entries(MEMORY_SCOPES).map(([scope, meta]) => {
        const scopeState = data.scopes[scope] || { status: 'unavailable', count: null, location: meta.path };
        const items = data.items.filter((memory) => memory.scope === scope);
        if (memoryStore.filters.scope && memoryStore.filters.scope !== scope) return '';
        return `<section class="scope-section"><h2>${esc(meta.label)} scope · ${scopeState.count ?? '—'} ${statusPill(scopeState.status)}</h2><div class="scope-path"><code>${esc(scopeState.location || meta.path)}</code><span>${esc(meta.description)}</span><small>${esc(meta.sharing)} · read-only</small></div>${scopeState.status === 'error' ? '<div class="card snapshot-warning"><p>此 scope 存在局部读取问题；可用条目仍保留展示。</p></div>' : ''}<div class="memory-list">${memoryRows(items)}</div></section>`;
      }).join('')}`;
    } else if (sub === 'retrieval') {
      body = '';
    } else if (sub === 'injection') {
      body = '';
    } else if (sub === 'lifecycle') {
      body = `<div class="intro">Tier 是每条持久 Memory 在任意 scope 内部的真实生命周期状态，不是另一套存储目录。</div>
        ${memoryTierCards(data.summary)}
        <h2>Working memory protection — 独立模块</h2>
        <h2>WorkingMemoryTracker — process-local runtime observations</h2>
        <div class="intro">latest retained process-local snapshot · not global · not current · no compaction-protection guarantee</div>
        ${renderMemoryRuntimeLifecycle()}
        <h2>MemoryPipeline — 静态架构</h2>
        ${memoryPipelineCards()}
        <h2>真实条目状态</h2><div class="memory-list">${memoryRows(data.items)}</div>`;
    } else {
      body = `<div class="card accent-card"><b>CodeLoop 按作用域组织持久 Memory。</b><p>User / Project / Local 决定存储和共享语义；category 负责分类，tier 负责生命周期。当前接口只读。</p></div>
        <h2>三个持久化作用域</h2>
        ${memoryScopeCards(data.summary, data.scopes)}
        <h2>Tier 分布</h2>${memoryTierCards(data.summary)}
        <h2>本轮检索位置</h2><div class="card"><b>Run-level observation · live</b><p>真实 candidates、selected、rendered 与 suppressed 数量位于 Retrieval / Injection 子页；不会以持久化条目数量替代运行级事实。</p></div>`;
    }
    return shell(subtabBar('memory', tabs, sub) + controls + body + `${data.page.hasMore ? `<button class="load-more" onclick="loadMoreMemory()" ${memoryStore.loadingMore ? 'disabled' : ''}>${memoryStore.loadingMore ? '加载中…' : '加载更多 Memory'}</button>` : ''}${pageDiagnostics(data.diagnostics)}`);
  },

  skills(_, sub = 'available') {
    const data = skillsStore.data;
    const tabs = [['available', '可用', data?.summary?.total], ['routing', '本轮路由']];
    if (sub === 'routing') {
      return subtabBar('skills', tabs, sub) + runtimeTraceState('skill');
    }
    if (skillsStore.phase === 'idle' || skillsStore.phase === 'loading') {
      return subtabBar('skills', tabs, sub) + '<div class="card snapshot-state"><b>正在发现本地 Skills…</b><p>只读取有界摘要，不加载完整正文或绝对路径。</p></div>';
    }
    if (skillsStore.phase === 'error' || !data) {
      return subtabBar('skills', tabs, sub) + `<div class="card snapshot-state snapshot-error"><b>Skills 暂时不可用</b><p>${esc(skillsStore.error || 'Skill 摘要尚未加载。')}</p><button class="snapshot-button" onclick="refreshSkills()">重试</button></div>`;
    }
    const sources = [['project', 'Project'], ['user', 'User'], ['compat_project', 'Compat project'], ['compat_user', 'Compat user']];
    const sourceFilters = `<div class="catalog-filter"><small>source</small><div class="scope-filters"><button class="${skillsStore.filters.source == null ? 'on' : ''}" onclick="setSkillSourceFilter(null)">全部</button>${sources.map(([source, label]) => `<button class="${skillsStore.filters.source === source ? 'on' : ''}" onclick="setSkillSourceFilter('${esc(source)}')">${esc(label)} · ${esc(data.summary.bySource[source] ?? 0)}</button>`).join('')}</div></div>`;
    const directoryFilters = `<div class="catalog-filter"><small>directory</small><div class="scope-filters"><button class="${skillsStore.filters.directory == null ? 'on' : ''}" onclick="setSkillDirectoryFilter(null)">全部</button>${(data.summary.directories || []).map((directory) => `<button class="${skillsStore.filters.directory === directory ? 'on' : ''}" onclick="setSkillDirectoryFilter('${esc(directory)}')">${esc(directory)}</button>`).join('')}</div></div>`;
    const items = data.items.map((skill) => `<article class="tool-card skill-card"><div><code>${esc(skill.qualifiedName)}</code>${sourceTag(skill.source)}${skill.directory ? `<span class="meta right">${esc(skill.directory)}</span>` : ''}</div><p>${esc(skill.description)}</p><div class="skill-meta"><span>${esc(skill.domains.join(' · ') || 'no domains')}</span><span>${esc(skill.scopes.join(' · ') || 'no scopes')}</span><span>${esc(skill.tools.join(' · ') || 'no declared tools')}</span><span>${esc(skill.keywords.join(' · ') || 'no keywords')}</span><span>${esc(skill.exampleCount)} examples${skill.descriptionTruncated ? ' · description truncated' : ''}</span></div></article>`).join('') || '<div class="card empty">当前筛选下没有 Skill。</div>';
    return subtabBar('skills', tabs, sub) + `<div class="page-actions">${readSourceLine(data.source)}<button class="snapshot-button" onclick="refreshSkills()">刷新</button></div>${skillsStore.error ? `<div class="card snapshot-warning"><p>${esc(skillsStore.error)}</p></div>` : ''}<div class="intro">真实 Skill 安全摘要 · read-only；不提供安装、编辑、删除、加载全文或执行操作。Evidence is task correlation, not causal proof; verification and user signals require complete explicit coverage; promotion locked; rollback execution locked.</div>${skillEvidencePanel(data.evidence)}${skillVersionPanel(data.versionLedger)}<div class="catalog-filters">${sourceFilters}${directoryFilters}</div>${items}${data.page.hasMore ? `<button class="load-more" onclick="loadMoreSkills()" ${skillsStore.loadingMore ? 'disabled' : ''}>${skillsStore.loadingMore ? '加载中…' : '加载更多 Skills'}</button>` : ''}${pageDiagnostics(data.diagnostics)}`;
  },

  connections(_, sub = 'gateways') {
    const data = connectionsStore.data;
    const tabs = [['gateways', 'Gateway', data ? 1 : null], ['mcp', 'MCP', data?.summary?.configuredMcpCount]];
    if (connectionsStore.phase === 'idle' || connectionsStore.phase === 'loading') {
      return subtabBar('connections', tabs, sub) + '<div class="card snapshot-state"><b>正在读取 Connections…</b><p>Gateway 状态与 MCP 配置摘要独立加载。</p></div>';
    }
    if (connectionsStore.phase === 'error' || !data) {
      return subtabBar('connections', tabs, sub) + `<div class="card snapshot-state snapshot-error"><b>Connections 暂时不可用</b><p>${esc(connectionsStore.error || 'Connection 摘要尚未加载。')}</p><button class="snapshot-button" onclick="refreshConnections()">重试</button></div>`;
    }
    const controls = `<div class="page-actions">${readSourceLine(data.source)}<button class="snapshot-button" onclick="refreshConnections()">刷新</button></div>${connectionsStore.error ? `<div class="card snapshot-warning"><p>${esc(connectionsStore.error)}</p></div>` : ''}`;
    if (sub === 'mcp') {
      const servers = data.mcpServers.map((server) => `<article class="tool-card connection-card"><div><code>${esc(server.name)}</code>${statusPill(server.status)}<span class="meta right">${esc(server.scope)}</span></div><div class="connection-facts"><div class="connection-fact"><small>Current configuration</small><b>${esc(statusText[server.status] || server.status)}</b><span>${server.protocol ? `configured protocol ${esc(server.protocol)}` : 'configured protocol unavailable'} · read-only</span></div>${renderMcpCurrentRuntime(server.current)}${renderMcpHistoricalRuntime(server.runtime)}</div></article>`).join('') || '<div class="card empty"><b>没有 MCP 配置</b><p>用户级和项目级配置源均可读，当前 effective 配置集合为空；当前快照中的 unmatched keys 被完全抑制，历史 unmatched facts 只以汇总计数显示。</p></div>';
      return subtabBar('connections', tabs, sub) + controls + `<div class="intro">configured 不等于 connected；current 仅表示这一个 Gateway process snapshot，history 仅来自保留 Runs。没有 heartbeat、全局状态或 Dashboard 进程控制。</div>${renderMcpCurrentCoverage(data)}${renderMcpCoverage(data)}${servers}${pageDiagnostics(data.diagnostics)}`;
    }
    return subtabBar('connections', tabs, sub) + controls + `<div class="pillar-grid"><div class="flow-box"><b>CodeLoop Gateway ${statusPill(data.gateway.status)}</b><span>${esc(data.gateway.transport)} · ${esc(data.gateway.scope)}</span><small>当前 Dashboard 请求的本地只读入口</small></div><div class="flow-box"><b>MCP configuration ${statusPill(data.source.status)}</b><span>${esc(formatCount(data.summary.configuredMcpCount))} configured</span><small>${esc(formatCount(data.summary.registeredConfiguredMcpCount))} registered in snapshot</small></div><div class="flow-box"><b>Current instances ${statusPill(data.mcpCurrent.status)}</b><span>${esc(formatCount(data.summary.activeMcpInstanceCount))} active instances</span><small>${esc(formatCount(data.summary.liveMcpCount))} Ready in this Gateway process</small></div></div><h2>配置来源</h2>${table(['来源', '状态', '条目', '更新时间'], Object.entries(data.configSources).map(([scope, source]) => `<tr><td>${esc(scope)}</td><td>${statusPill(source.status)}</td><td>${esc(source.count ?? '—')}</td><td class="meta">${source.updatedAt ? esc(formatSnapshotTime(source.updatedAt)) : 'no file'}</td></tr>`))}${pageDiagnostics(data.diagnostics)}`;
  },

  ops() {
    if (opsStore.phase === 'idle' || opsStore.phase === 'loading') {
      return '<div class="card snapshot-state"><b>正在读取模型用量、Cost、Tool 与 Failure…</b><p>只扫描当前 Workspace 保留的 RunJournal；SSE 仅提供失效提示，Change Feed 轮询仅作后备。</p></div>';
    }
    if (opsStore.phase === 'error' || !opsStore.data) {
      return `<div class="card snapshot-state snapshot-error"><b>Ops 暂时不可用</b><p>${esc(opsStore.error || 'Ops 摘要尚未加载。')}</p><button class="snapshot-button" onclick="refreshOps()">重试</button></div>`;
    }
    const data = opsStore.data;
    const provider = data.usage.provider;
    const estimated = data.usage.estimated;
    const combined = data.usage.combined;
    const bucketCard = (label, bucket, calls) => `<article class="usage-bucket"><div><b>${esc(label)}</b><code>${esc(formatCount(calls))} calls</code></div><dl><dt>Input</dt><dd>${esc(formatCount(bucket.inputTokens))}</dd><dt>Output</dt><dd>${esc(formatCount(bucket.outputTokens))}</dd><dt>Cache read</dt><dd>${esc(formatCount(bucket.cacheReadTokens))}</dd><dt>Cache create</dt><dd>${esc(formatCount(bucket.cacheCreationTokens))}</dd></dl></article>`;
    const controls = `<div class="page-actions"><div><div class="intro"><b>Retained RunJournal · read-only</b> · SSE 实时失效，Change Feed 轮询后备，并保留手动刷新。</div>${readSourceLine(data.source)}</div><button class="snapshot-button" onclick="refreshOps()">刷新</button></div>`;
    const empty = opsStore.phase === 'empty' ? '<div class="card empty"><b>暂无模型观测</b><p>保留 RunJournal 中还没有可配对的 Model terminal events；Tool 与 Failure 仍按各自持久化观测状态显示。历史运行不会回填，也不会把未观测值显示为零。</p></div>' : '';
    return `${controls}${opsStore.error ? `<div class="card snapshot-warning"><p>${esc(opsStore.error)}</p></div>` : ''}${empty}${metricTiles([
      costMetricTile(data.cost),
      [formatCount(data.summary.pricedCalls), 'Priced calls'],
      [formatCount((data.summary.unavailableCostCalls || 0) + (data.summary.missingCostCalls || 0)), 'Unavailable / missing Cost'],
      [formatCount(data.summary.completedModelCalls), 'Completed model calls'],
      [formatCount(data.summary.failedModelCalls), 'Failed attempts · not priced'],
      [formatCount(combined.totalTokens), 'Known total tokens'],
      [formatDuration(data.duration.totalMs), 'Observed model duration'],
      [formatCount(data.summary.observedToolCalls), 'Observed Tool calls', data.tools?.status || 'unavailable'],
      [formatCount(data.summary.toolErrorCalls), 'Tool errors'],
      [formatCount(data.summary.affectedRuns), 'Runs with observed failures', data.failures?.status || 'unavailable'],
      contextMetricTile(data.context),
      workingMemoryMetricTile(data.workingMemory),
    ])}${renderContextBreakdown(data.context, data.recovery, data.contextBreakdown)}${renderWorkingMemoryRuntime(data.workingMemory)}<h2>Canonical usage</h2><div class="usage-grid">${bucketCard('Provider', provider, data.summary.providerCalls)}${bucketCard('Estimated', estimated, data.summary.estimatedCalls)}<article class="usage-bucket combined"><div><b>Combined</b>${statusPill(combined.status)}</div><dl><dt>Input</dt><dd>${esc(formatCount(combined.inputTokens))}</dd><dt>Output</dt><dd>${esc(formatCount(combined.outputTokens))}</dd><dt>Total</dt><dd>${esc(formatCount(combined.totalTokens))}</dd><dt>Provenance</dt><dd>${esc(combined.provenance)}</dd></dl></article></div>${renderCostBreakdown(data.cost, data.costBreakdown)}${renderToolBreakdown(data.tools, data.toolBreakdown)}${renderFailureBreakdown(data.failures, data.failureBreakdown)}<h2>Observation coverage</h2><div class="card ops-coverage"><div>${statusPill(data.source.status)}<b>${esc(data.summary.scannedRuns)} / ${esc(data.summary.retainedRuns ?? '—')} retained Runs scanned</b></div><p>Completed calls: Provider ${esc(data.summary.providerCalls)} · Estimated ${esc(data.summary.estimatedCalls)} · Unavailable ${esc(data.summary.unavailableCalls)}。Cost: Priced ${esc(data.summary.pricedCalls)} · Unavailable ${esc(data.summary.unavailableCostCalls)} · Missing ${esc(data.summary.missingCostCalls)}；Duration observed ${esc(data.duration.observedCalls)} / ${esc(data.duration.modelCalls)} calls。Tool: Observed ${esc(data.summary.observedToolCalls)} · completed ${esc(data.summary.completedToolCalls)} · error ${esc(data.summary.toolErrorCalls)}。Failures: affected Runs ${esc(data.summary.affectedRuns)} · Model ${esc(data.summary.modelFailureAttempts)} · Run ${esc(data.summary.runFailures)} · interrupted ${esc(data.summary.interruptedRuns)} · cancelled ${esc(data.summary.cancelledRuns)}；historical partial。</p><div class="unavailable-list"><span>Run scan limit · ${esc(data.coverage.runScanLimit)}</span><span>Per-run event limit · ${esc(data.coverage.eventScanLimitPerRun)}</span><span>Scope · ${esc(data.coverage.scope)}</span><span>Tool coverage · ${esc(data.coverage.tools)}</span><span>Failure coverage · ${esc(data.coverage.failures)}</span><span>Context coverage · ${esc(data.coverage.context)}</span><span>WorkingMemory coverage · ${esc(data.coverage.workingMemory)}</span><span>Historical · ${esc(data.coverage.historical)}</span></div></div>${pageDiagnostics(data.diagnostics)}`;
  },

  system() {
    return `${renderSystemSummary()}${renderDataHealthPanel()}`;
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
  document.querySelector('#page-kicker').textContent = PAGE_KICKERS[view];
  document.querySelector('#page-deck').textContent = PAGE_DECKS[view];
  document.querySelector('#view').innerHTML = VIEWS[view](DATA, sub);
  tickMeta();
}

function tickMeta() {
  const [view, sub] = currentRoute();
  if (view === 'overview') {
    if (snapshotStore.phase === 'loading') {
      document.querySelector('#page-meta').innerHTML = '<span class="source-state stale"><i></i>read-only · loading</span>';
      return;
    }
    if (snapshotStore.phase === 'error' || !snapshotStore.data) {
      document.querySelector('#page-meta').innerHTML = '<span class="source-state error"><i></i>read-only · snapshot error</span>';
      return;
    }
    const snapshot = snapshotStore.data;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(snapshot.status)}"><i></i>read-only · ${esc(snapshot.status)}</span> · generated ${esc(formatSnapshotTime(snapshot.generatedAt))} · ${esc(snapshot.workspace.name)}`;
    return;
  }
  if (view === 'runs') {
    const status = runsStore.phase === 'loading' || runsStore.phase === 'idle' ? 'stale' : (runsStore.source?.status || runsStore.phase);
    const label = status === 'stale' ? 'loading' : status;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>journal read-only · ${esc(label)}</span>${runsStore.source?.updatedAt ? ` · updated ${esc(formatSnapshotTime(runsStore.source.updatedAt))}` : ''} · lifecycle-model-usage-cost-tool-assistant-skill-memory-context · historical partial`;
    return;
  }
  if (view === 'sessions') {
    const status = sessionsStore.phase === 'loading' || sessionsStore.phase === 'idle' ? 'stale' : (sessionsStore.source?.status || sessionsStore.phase);
    const label = status === 'stale' ? 'loading' : status;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>historical read-only · ${esc(label)}</span>${sessionsStore.source?.updatedAt ? ` · updated ${esc(formatSnapshotTime(sessionsStore.source.updatedAt))}` : ''}`;
    return;
  }
  if (view === 'memory') {
    if (sub === 'approvals') {
      const status = ['idle', 'loading'].includes(memoryApprovalStore.phase) ? 'stale' : memoryApprovalStore.phase;
      const label = status === 'stale' ? 'loading' : status;
      document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>read-write · persistent approval · ${esc(label)}</span>${memoryApprovalStore.lastUpdatedAt ? ` · updated ${esc(formatSnapshotTime(memoryApprovalStore.lastUpdatedAt))}` : ''}`;
      return;
    }
    if (sub === 'retrieval' || sub === 'injection') {
      const status = ['idle', 'loading'].includes(runtimeTraceStore.phase) ? 'stale' : runtimeTraceStore.phase;
      document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>runtime read-only · ${esc(status === 'stale' ? 'loading' : status)}</span> · RunJournal skill-memory coverage`;
      return;
    }
    const status = memoryStore.phase === 'loading' || memoryStore.phase === 'idle' ? 'stale' : (memoryStore.data?.source?.status || memoryStore.phase);
    const label = status === 'stale' ? 'loading' : status;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>read-only · ${esc(label)}</span>${memoryStore.data?.source?.updatedAt ? ` · updated ${esc(formatSnapshotTime(memoryStore.data.source.updatedAt))}` : ''}`;
    return;
  }
  if (view === 'skills') {
    if (sub === 'routing') {
      const status = ['idle', 'loading'].includes(runtimeTraceStore.phase) ? 'stale' : runtimeTraceStore.phase;
      document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>runtime read-only · ${esc(status === 'stale' ? 'loading' : status)}</span> · RunJournal skill coverage`;
      return;
    }
    const status = skillsStore.phase === 'loading' || skillsStore.phase === 'idle' ? 'stale' : (skillsStore.data?.source?.status || skillsStore.phase);
    const label = status === 'stale' ? 'loading / configuration' : status;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>read-only · ${esc(label)}</span>${skillsStore.data?.source?.updatedAt ? ` · updated ${esc(formatSnapshotTime(skillsStore.data.source.updatedAt))}` : ''}`;
    return;
  }
  if (view === 'connections') {
    const status = connectionsStore.phase === 'loading' || connectionsStore.phase === 'idle' ? 'stale' : (connectionsStore.data?.source?.status || connectionsStore.phase);
    const label = status === 'stale' ? 'current snapshot unavailable' : status;
    const currentCheckedAt = connectionsStore.data?.mcpCurrent?.checkedAt;
    const sourceUpdatedAt = connectionsStore.data?.source?.updatedAt;
    const timestampMeta = currentCheckedAt
      ? ` · checked ${esc(formatSnapshotTime(currentCheckedAt))}`
      : sourceUpdatedAt ? ` · source updated ${esc(formatSnapshotTime(sourceUpdatedAt))}` : '';
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>read-only · ${esc(label)}</span>${timestampMeta} · Gateway process snapshot · historical partial · No global state · No process control`;
    return;
  }
  if (view === 'system') {
    const systemStatus = ['loading', 'idle'].includes(systemStore.phase)
      ? 'stale' : (systemStore.data?.source?.status || systemStore.phase);
    const healthStatus = ['loading', 'idle'].includes(dataHealthStore.phase)
      ? 'stale' : (dataHealthStore.data?.status || dataHealthStore.phase);
    const status = [systemStatus, healthStatus].includes('error')
      ? 'error'
      : [systemStatus, healthStatus].includes('partial')
        ? 'partial'
        : [systemStatus, healthStatus].includes('stale') ? 'stale' : 'live';
    const generatedAt = dataHealthStore.data?.generatedAt || systemStore.data?.generatedAt;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>read-only · system ${esc(systemStatus)} · data ${esc(healthStatus)}</span>${generatedAt ? ` · generated ${esc(formatSnapshotTime(generatedAt))}` : ''}`;
    return;
  }
  if (view === 'ops') {
    const status = opsStore.phase === 'loading' || opsStore.phase === 'idle' ? 'stale' : (opsStore.data?.source?.status || opsStore.phase);
    const label = status === 'stale' ? 'loading' : status;
    document.querySelector('#page-meta').innerHTML = `<span class="source-state ${esc(status)}"><i></i>journal read-only · ${esc(label)}</span>${opsStore.data?.generatedAt ? ` · generated ${esc(formatSnapshotTime(opsStore.data.generatedAt))}` : ''} · retained usage + Cost · historical partial`;
    return;
  }
  const seconds = Math.round((Date.now() - state.lastRefresh) / 1000);
  document.querySelector('#page-meta').innerHTML = `<span class="source-state mock"><i></i>mock / read-only</span> · updated ${seconds}s ago · ${esc(DATA.workspace)}`;
}

function fixedMemoryApprovalError(code = 'memory_approval_unavailable') {
  const messages = {
    invalid_response: '审批数据未通过安全验证；不会显示未经验证的内容，请手动刷新。',
    memory_approval_not_found: '该待审批 Memory 已不存在，已从权威状态重新读取。',
    memory_review_stale: 'Memory 内容已变化并重新加载；请基于最新审查重新决定。',
    memory_already_decided: '该 Memory 已由另一决定完成，已从权威状态重新读取。',
    memory_not_reviewable: '该 Memory 不能安全批准，已重新加载为只能拒绝的审查。',
    memory_write_conflict: 'Memory 在决定期间发生变化；未自动重试，已重新加载。',
    memory_store_busy: 'Memory 存储暂时繁忙；保留当前安全审查，请稍后手动刷新。',
    memory_approval_failed: 'Memory 审批暂时失败；保留当前安全审查，请手动刷新。',
    memory_approval_unavailable: '当前 Gateway 无法提供 Memory 审批，请手动刷新。',
    invalid_request: 'Memory 审批请求无效；已重新读取权威状态。',
    invalid_memory_id: 'Memory 标识无效；已重新读取权威状态。',
    invalid_decision: 'Memory 审批决定无效；已重新读取权威状态。',
    invalid_review_revision: 'Memory 审查版本无效；已重新读取权威状态。',
    connection_lost: '连接中断，审批结果尚未确认；不会自动重发决定，请手动刷新。',
  };
  return messages[code] || messages.memory_approval_failed;
}

async function memoryApprovalResponseJson(response, maxBytes = MEMORY_APPROVAL_MAX_BYTES) {
  const text = await response.text();
  if (utf8ByteLength(text) > maxBytes) throw new Error('invalid_response');
  return JSON.parse(text);
}

function memoryApprovalErrorCode(payload) {
  const allowed = new Set([
    'memory_approval_not_found', 'memory_review_stale', 'memory_already_decided',
    'memory_not_reviewable', 'memory_write_conflict', 'memory_store_busy',
    'memory_approval_failed', 'memory_approval_unavailable',
    'invalid_request', 'invalid_memory_id', 'invalid_decision',
    'invalid_review_revision',
  ]);
  const code = payload?.error?.code;
  return typeof code === 'string' && allowed.has(code) ? code : 'invalid_response';
}

function selectMemoryApproval(memoryId) {
  if (!MEMORY_APPROVAL_ID_PATTERN.test(memoryId || '')
      || !memoryApprovalStore.items.some((item) => item.memoryId === memoryId)) return false;
  memoryApprovalStore.selectedMemoryId = memoryId;
  renderRouteOnly('memory');
  return true;
}

function memoryApprovalActionAvailable(item, decision) {
  if (!item
      || !['live', 'partial'].includes(memoryApprovalStore.phase)
      || memoryApprovalStore.actingMemoryId !== null
      || memoryApprovalReadPromise !== null
      || !memoryApprovalStore.items.some((candidate) => candidate.memoryId === item.memoryId
        && candidate.reviewRevision === item.reviewRevision)
      || !['approve', 'reject'].includes(decision)
      || !item.choices.includes(decision)) return false;
  return decision === 'reject' || canApproveMemory(item);
}

async function loadMemoryApprovals(forceDuringAction = false) {
  if (memoryApprovalStore.actingMemoryId !== null && !forceDuringAction) {
    memoryApprovalRefreshQueued = true;
    return false;
  }
  if (memoryApprovalReadPromise !== null) {
    memoryApprovalRefreshQueued = true;
    return memoryApprovalReadPromise;
  }
  const requestId = memoryApprovalStore.requestId + 1;
  memoryApprovalStore.requestId = requestId;
  memoryApprovalStore.phase = memoryApprovalStore.items.length ? 'partial' : 'loading';
  memoryApprovalStore.error = null;
  renderRouteOnly('memory');

  const operation = (async () => {
    try {
      const response = await fetch('/api/v1/memory/approvals/pending', {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      const payload = await memoryApprovalResponseJson(response);
      if (requestId !== memoryApprovalStore.requestId) return false;
      const validated = response.ok ? validateMemoryApprovalPendingPayload(payload) : null;
      if (!validated) throw new Error(response.ok ? 'invalid_response' : memoryApprovalErrorCode(payload));
      const deletionConvergence = projectMemoryDeletionConvergenceStore();
      if (deletionConvergence) {
        deletionConvergence.convergence.approvals = !validated.items.some(
          (item) => item.memoryId === deletionConvergence.targetId,
        );
      }
      const currentItems = validated.items.filter(
        (item) => !projectMemoryDeletionTombstoned(item.memoryId),
      );
      memoryApprovalStore.items = currentItems;
      memoryApprovalStore.revision = validated.revision;
      memoryApprovalStore.diagnostics = validated.diagnostics;
      memoryApprovalStore.error = null;
      memoryApprovalStore.lastUpdatedAt = validated.generatedAt;
      if (!currentItems.some((item) => item.memoryId === memoryApprovalStore.selectedMemoryId)) {
        memoryApprovalStore.selectedMemoryId = currentItems[0]?.memoryId || null;
      }
      memoryApprovalStore.phase = validated.diagnostics.length
        ? 'partial'
        : currentItems.length ? 'live' : 'empty';
      return true;
    } catch (error) {
      if (requestId !== memoryApprovalStore.requestId) return false;
      const code = typeof error?.message === 'string' ? error.message : 'invalid_response';
      memoryApprovalStore.phase = 'error';
      memoryApprovalStore.error = fixedMemoryApprovalError(code);
      return false;
    }
  })();
  memoryApprovalReadPromise = operation;
  try {
    return await operation;
  } finally {
    if (memoryApprovalReadPromise === operation) memoryApprovalReadPromise = null;
    if (requestId === memoryApprovalStore.requestId) renderRouteOnly('memory');
    if (memoryApprovalRefreshQueued && memoryApprovalStore.actingMemoryId === null) {
      memoryApprovalRefreshQueued = false;
      Promise.resolve().then(() => loadMemoryApprovals());
    }
  }
}

async function decideMemoryApproval(memoryId, reviewRevision, decision) {
  const item = memoryApprovalStore.items.find((candidate) => candidate.memoryId === memoryId
    && candidate.reviewRevision === reviewRevision);
  if (!item || !memoryApprovalActionAvailable(item, decision)) return false;
  const actionGeneration = memoryApprovalStore.actionGeneration + 1;
  memoryApprovalStore.actionGeneration = actionGeneration;
  memoryApprovalStore.actingMemoryId = memoryId;
  memoryApprovalStore.error = null;
  renderRouteOnly('memory');

  let response;
  try {
    response = await fetch(`/api/v1/memory/approvals/${encodeURIComponent(memoryId)}/decision`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ decision, reviewRevision }),
    });
  } catch (_error) {
    if (actionGeneration !== memoryApprovalStore.actionGeneration) return false;
    memoryApprovalStore.phase = 'error';
    memoryApprovalStore.error = fixedMemoryApprovalError('connection_lost');
    memoryApprovalStore.actingMemoryId = null;
    memoryApprovalRefreshQueued = false;
    renderRouteOnly('memory');
    return false;
  }

  let payload = null;
  try {
    payload = await memoryApprovalResponseJson(response, 16 * 1024);
  } catch (_error) {
    // A received but invalid response is never trusted; GET remains authority.
  }
  if (actionGeneration !== memoryApprovalStore.actionGeneration
      || !memoryApprovalStore.items.some((candidate) => candidate.memoryId === memoryId
        && candidate.reviewRevision === reviewRevision)) return false;

  const expected = { memoryId, reviewRevision, decision };
  if (response.ok && validMemoryApprovalDecisionPayload(payload, expected)) {
    const reconciled = await loadMemoryApprovals(true);
    if (actionGeneration !== memoryApprovalStore.actionGeneration) return false;
    memoryApprovalStore.actingMemoryId = null;
    memoryApprovalRefreshQueued = false;
    renderRouteOnly('memory');
    if (!reconciled) return false;
    if (decision === 'approve') {
      await Promise.all([loadMemory(false), loadDashboardSnapshot()]);
    }
    return true;
  }

  const code = response.ok ? 'invalid_response' : memoryApprovalErrorCode(payload);
  if (code === 'memory_store_busy') {
    memoryApprovalStore.phase = 'error';
    memoryApprovalStore.error = fixedMemoryApprovalError(code);
    memoryApprovalStore.actingMemoryId = null;
    memoryApprovalRefreshQueued = false;
    renderRouteOnly('memory');
    return false;
  }
  const notice = fixedMemoryApprovalError(code);
  const reconciled = await loadMemoryApprovals(true);
  if (actionGeneration !== memoryApprovalStore.actionGeneration) return false;
  memoryApprovalStore.actingMemoryId = null;
  memoryApprovalRefreshQueued = false;
  if (reconciled) memoryApprovalStore.error = notice;
  renderRouteOnly('memory');
  return false;
}

function deletionStoreFor(kind) {
  if (kind === 'conversation') return conversationDeletionStore;
  if (kind === 'project-memory') return projectMemoryDeletionStore;
  return null;
}

function activeDeletionStore() {
  return [conversationDeletionStore, projectMemoryDeletionStore].find(
    (store) => store.phase !== 'idle' && store.targetId !== null,
  ) || null;
}

function deletionPath(store) {
  if (!store?.targetId) return null;
  return store.kind === 'conversation'
    ? `/api/v1/sessions/${encodeURIComponent(store.targetId)}/deletion`
    : `/api/v1/memory/project/${encodeURIComponent(store.targetId)}/deletion`;
}

function fixedDeletionError(code = 'deletion_failed') {
  const messages = {
    invalid_response: '删除服务返回了无法验证的数据；未执行本地清理。',
    invalid_request: '删除请求无效；请关闭后从当前条目重新开始。',
    invalid_id: '删除目标标识无效；未发送删除请求。',
    invalid_revision: '删除版本无效；请重新读取预览。',
    deletion_target_not_found: '未找到删除目标；仅凭此结果不能确认删除，正在等待集合对账。',
    deletion_revision_stale: '删除范围已经变化，请基于最新预览重新确认。',
    deletion_target_busy: '当前会话仍有任务正在运行或提交；不会自动取消任务。',
    deletion_write_conflict: '目标在确认后发生变化；不会自动重提，请重新检查范围。',
    deletion_store_busy: '删除存储暂时繁忙；写入结果未确认，请重新读取预览。',
    deletion_unavailable: '删除服务暂时不可用；保留当前安全预览，请稍后重新检查。',
    deletion_failed: '删除未能完成；不会自动重试。',
    connection_lost: '连接中断，删除结果尚未确认；不会自动重发删除请求。',
    deletion_retry_required: '清理只完成了一部分；重新读取范围并再次明确确认后才能继续。',
  };
  return messages[code] || messages.deletion_failed;
}

function deletionErrorCode(payload) {
  const allowed = new Set([
    'invalid_request', 'invalid_id', 'invalid_revision',
    'deletion_target_not_found', 'deletion_revision_stale',
    'deletion_target_busy', 'deletion_write_conflict',
    'deletion_store_busy', 'deletion_unavailable', 'deletion_failed',
  ]);
  if (!hasExactKeys(payload, ['ok', 'error'])
      || payload.ok !== false
      || !hasExactKeys(payload.error, ['code', 'message'])
      || typeof payload.error.message !== 'string'
      || utf8ByteLength(payload.error.message) > 512
      || !allowed.has(payload.error.code)) return 'invalid_response';
  return payload.error.code;
}

async function deletionResponseJson(response) {
  const text = await response.text();
  if (!text || utf8ByteLength(text) > DELETION_RESPONSE_MAX_BYTES) {
    throw new Error('invalid_response');
  }
  return JSON.parse(text);
}

function ensureDeletionDialogHost() {
  let host = document.querySelector('#deletion-dialog-host');
  if (!host) {
    host = document.createElement('div');
    host.id = 'deletion-dialog-host';
    document.body.appendChild(host);
  }
  return host;
}

function resetDeletionStore(store) {
  store.phase = 'idle';
  store.targetId = null;
  store.preview = null;
  store.result = null;
  store.errorCode = null;
  store.errorMessage = null;
  store.opener = null;
  store.outcomeUnconfirmed = false;
  store.staleNotice = false;
  store.localBusy = false;
  store.convergence = null;
}

function closeDeletionDialog(kind = null) {
  const store = kind ? deletionStoreFor(kind) : activeDeletionStore();
  if (!store || store.phase === 'submitting') return false;
  const opener = store.opener;
  store.requestGeneration += 1;
  store.actionGeneration += 1;
  resetDeletionStore(store);
  ensureDeletionDialogHost().replaceChildren();
  const focusTarget = opener?.isConnected ? opener : document.querySelector('#view');
  focusTarget?.focus({ preventScroll: true });
  return true;
}

function replaceOpenDeletion(store, targetId, opener, localBusy = false) {
  const other = store.kind === 'conversation'
    ? projectMemoryDeletionStore
    : conversationDeletionStore;
  if (other.phase === 'submitting' || store.phase === 'submitting') return false;
  if (other.phase !== 'idle') closeDeletionDialog(other.kind);
  if (store.phase !== 'idle') closeDeletionDialog(store.kind);
  store.targetId = targetId;
  store.opener = opener || document.activeElement;
  store.localBusy = localBusy;
  store.phase = 'loading-preview';
  store.errorCode = null;
  store.errorMessage = null;
  store.outcomeUnconfirmed = false;
  store.staleNotice = false;
  renderDeletionDialog(store, true);
  return true;
}

function openConversationDeletion(sessionId, opener = document.activeElement) {
  if (!SESSION_ID_PATTERN.test(sessionId || '')
      || sessionDetailStore.sessionId !== sessionId
      || !sessionsStore.items.some((item) => item.id === sessionId)) return false;
  const localBusy = chatStore.activeTargetSessionId === sessionId
    && chatStore.activeTurnId !== null;
  if (!replaceOpenDeletion(
    conversationDeletionStore,
    sessionId,
    opener,
    localBusy,
  )) return false;
  loadDeletionPreview(conversationDeletionStore, 'open');
  return true;
}

function openProjectMemoryDeletion(memoryId, opener = document.activeElement) {
  if (!MEMORY_APPROVAL_ID_PATTERN.test(memoryId || '')
      || !memoryStore.data?.items?.some(
        (item) => item.id === memoryId && item.scope === 'project',
      )) return false;
  if (!replaceOpenDeletion(
    projectMemoryDeletionStore,
    memoryId,
    opener,
  )) return false;
  loadDeletionPreview(projectMemoryDeletionStore, 'open');
  return true;
}

function deletionCanSubmit(store) {
  const preview = store?.preview;
  if (!preview
      || !['review', 'stale', 'partial'].includes(store.phase)
      || store.outcomeUnconfirmed
      || store.localBusy
      || preview.target[store.kind === 'conversation' ? 'sessionId' : 'memoryId'] !== store.targetId
      || !DELETION_REVISION_PATTERN.test(preview.deletionRevision)
      || preview.blockers.length
      || preview.diagnostics.length) return false;
  if (preview.status === 'partial') {
    return Object.values(preview.affected).some((count) => count > 0);
  }
  if (preview.status !== 'ready') return false;
  if (store.kind === 'conversation') return preview.affected.sessions === 1;
  return preview.affected.entries === 1
    || (preview.affected.entries === 0
      && preview.affected.approvalAuditRecords + preview.affected.backlinks > 0);
}

function deletionFixedCodeText(code) {
  const labels = {
    active_turn: '存在正在处理或提交的 Turn',
    active_run: '存在尚未终止的 Run',
    session_record_invalid: 'Session 记录无法安全验证',
    session_index_invalid: 'Session 索引无法安全验证',
    session_delta_invalid: 'Session 增量记录无法安全验证',
    session_delta_scan_limited: 'Session 增量扫描达到安全上限',
    session_ownership_unavailable: 'Session Workspace 归属无法确认',
    session_scan_unavailable: 'Session 扫描暂时不可用',
    turn_record_invalid: 'Turn 记录无法安全验证',
    turn_scan_unavailable: 'Turn 扫描暂时不可用',
    turn_scan_limited: 'Turn 扫描达到安全上限',
    run_record_invalid: 'Run 记录无法安全验证',
    run_writer_invalid: 'Run writer 状态无法安全验证',
    run_scan_unavailable: 'Run 扫描暂时不可用',
    run_scan_limited: 'Run 扫描达到安全上限',
    memory_metadata_invalid: 'Memory 元数据无法安全验证',
    memory_audit_invalid: 'Memory 审批审计无法安全验证',
    deletion_retry_required: '仍有清理残留，需要重新确认',
  };
  return labels[code] || '删除状态无法安全确认';
}

function deletionCountsHtml(store, counts, heading) {
  const labels = store.kind === 'conversation'
    ? [['sessions', 'Session'], ['turns', '终态 Turns'], ['runs', '终态 Runs']]
    : [['entries', 'Project 条目'], ['approvalAuditRecords', '审批审计记录'], ['backlinks', 'Backlinks']];
  return `<section class="deletion-counts" aria-label="${esc(heading)}"><small>${esc(heading)}</small><div>${labels.map(([key, label]) => `<span><b>${esc(counts[key])}</b><em>${esc(label)}</em></span>`).join('')}</div></section>`;
}

function deletionPreviewDetails(store) {
  const preview = store.preview;
  if (!preview) return '';
  const safeCodes = [...preview.blockers, ...preview.diagnostics];
  const notices = safeCodes.length
    ? `<ul class="deletion-diagnostics">${safeCodes.map((item) => `<li>${esc(deletionFixedCodeText(item.code))}</li>`).join('')}</ul>`
    : '';
  const status = `<div class="deletion-status-row">${statusPill(preview.status)}<span>刚刚重新检查 · ${esc(formatSnapshotTime(preview.generatedAt))}</span></div>`;
  if (store.kind === 'conversation') {
    return `${status}<dl class="deletion-target"><dt>Session ID</dt><dd><code>${esc(preview.target.sessionId)}</code></dd></dl>${deletionCountsHtml(store, preview.affected, preview.status === 'partial' ? '剩余范围' : '将影响')}<div class="deletion-scope-note"><p>会话正文、关联终态 Turn 与 Runs 页面中的关联执行记录会删除，且不可撤销。</p><p>其他 Session、无关联 Run 与 Memory 不受影响。</p></div>${notices}`;
  }
  return `${status}<dl class="deletion-target"><dt>Memory ID</dt><dd><code>${esc(preview.target.memoryId)}</code></dd><dt>Scope</dt><dd><code>project</code></dd><dt>Category / Tier</dt><dd>${esc(preview.target.category)} · ${esc(preview.target.tier)}</dd><dt>Lifecycle / Approval</dt><dd>${esc(preview.target.lifecycleStatus)} · ${esc(preview.target.approvalStatus)}</dd></dl>${deletionCountsHtml(store, preview.affected, preview.affected.entries === 0 ? '清理残留表示' : (preview.status === 'partial' ? '剩余范围' : '将影响'))}<div class="deletion-scope-note"><p>这条 Project Memory、对应审批审计与其他 Project Memory 中的 backlinks 会删除，且不可撤销。</p><p>User、Local 与其他 Project Memory 不受影响；确认页不复制 Memory 正文。</p></div>${notices}`;
}

function deletionDialogActions(store) {
  if (store.phase === 'submitting') {
    return '<button type="button" class="deletion-secondary" disabled>正在提交删除…</button>';
  }
  if (store.phase === 'completed') {
    return '<button type="button" class="deletion-secondary" data-deletion-close>完成</button>';
  }
  const recheckNeeded = [
    'busy', 'partial', 'stale', 'unconfirmed', 'error', 'reconciling',
  ].includes(store.phase) || store.outcomeUnconfirmed;
  const recheckLabel = store.outcomeUnconfirmed ? '检查删除结果' : '重新检查状态';
  const destructive = deletionCanSubmit(store)
    ? `<button type="button" class="deletion-destructive" data-deletion-submit>${store.preview.status === 'partial' ? '重新确认并继续清理' : store.kind === 'conversation' ? '删除会话及关联记录' : '删除这条 Project Memory'}</button>`
    : '';
  return `<button type="button" class="deletion-secondary" data-deletion-close>取消</button>${recheckNeeded ? `<button type="button" class="deletion-secondary" data-deletion-recheck>${esc(recheckLabel)}</button>` : ''}${destructive}`;
}

function renderDeletionDialog(store = activeDeletionStore(), initialFocus = false) {
  const host = ensureDeletionDialogHost();
  if (!store || store.phase === 'idle') {
    host.replaceChildren();
    return;
  }
  const previousAction = host.contains(document.activeElement)
    ? document.activeElement?.dataset?.deletionAction
    : null;
  const title = store.kind === 'conversation' ? '删除完整会话' : '删除 Project Memory';
  const description = store.kind === 'conversation'
    ? '先由 Gateway 重新计算完整会话删除范围，再由你明确确认。'
    : '只管理当前 Workspace 的单条 Project Memory；User 与 Local 不在此操作范围。';
  const phaseMessages = {
    'loading-preview': '正在从删除权威读取最新预览…',
    submitting: '删除请求已发送；此时不能关闭，最终结果仍需权威对账。',
    reconciling: '删除已提交，正在重新读取权威集合。',
    completed: '权威删除结果和集合状态已经收敛。',
    busy: '当前目标仍在使用中，不提供删除确认。',
    partial: '之前的清理尚未完成；继续前必须重新读取并明确确认。',
    stale: '删除范围已经变化；请审查最新范围并重新确认。',
    unconfirmed: '写入结果尚未确认；只允许 GET 检查，不会自动重发。',
    error: '删除服务暂时不可用；不会自动重试写操作。',
    review: '预览已验证；请核对低敏感范围后再确认。',
  };
  const result = store.result?.status === 'partial'
    ? `<div class="deletion-partial-result">${deletionCountsHtml(store, store.result.deleted, '本次已清理')}${deletionCountsHtml(store, store.result.remaining, '仍然存在')}</div>`
    : '';
  const localBusy = store.localBusy
    ? '<div class="deletion-notice warning">右侧 Dock 仍有属于此 Session 的活动 Turn；前端预先禁用删除，Gateway 仍是最终权威。</div>'
    : '';
  const error = store.errorMessage
    ? `<div class="deletion-notice ${store.outcomeUnconfirmed ? 'warning' : 'error'}" role="alert">${esc(store.errorMessage)}</div>`
    : '';
  host.innerHTML = `<div class="deletion-backdrop"><section class="deletion-dialog" role="dialog" aria-modal="true" aria-labelledby="deletion-dialog-title" aria-describedby="deletion-dialog-description" aria-busy="${store.phase === 'submitting' || store.phase === 'reconciling' ? 'true' : 'false'}"><header><div><small>Workspace-local management</small><h2 id="deletion-dialog-title">${esc(title)}</h2></div><button type="button" class="deletion-close" data-deletion-close aria-label="关闭删除确认" ${store.phase === 'submitting' ? 'disabled' : ''}>关闭</button></header><p id="deletion-dialog-description">${esc(description)}</p><div class="deletion-live" role="status" aria-live="polite">${esc(phaseMessages[store.phase] || '删除状态已更新。')}</div>${localBusy}${error}${store.preview ? deletionPreviewDetails(store) : ''}${result}<footer>${deletionDialogActions(store)}</footer></section></div>`;
  host.querySelectorAll('[data-deletion-close]').forEach((button) => {
    button.dataset.deletionAction = 'close';
    button.addEventListener('click', () => closeDeletionDialog(store.kind));
  });
  const recheck = host.querySelector('[data-deletion-recheck]');
  if (recheck) {
    recheck.dataset.deletionAction = 'recheck';
    recheck.addEventListener('click', () => loadDeletionPreview(store, 'manual'));
  }
  const submit = host.querySelector('[data-deletion-submit]');
  if (submit) {
    submit.dataset.deletionAction = 'submit';
    submit.addEventListener('click', () => submitDeletion(store.kind));
  }
  const focusTarget = previousAction
    ? host.querySelector(`[data-deletion-action="${previousAction}"]`)
    : null;
  if (focusTarget) focusTarget.focus({ preventScroll: true });
  else if (initialFocus) {
    host.querySelector('[data-deletion-close], [data-deletion-recheck], [data-deletion-submit]')?.focus({ preventScroll: true });
  }
}

async function loadDeletionPreview(storeOrKind, reason = 'manual') {
  const store = typeof storeOrKind === 'string'
    ? deletionStoreFor(storeOrKind)
    : storeOrKind;
  if (!store?.targetId || store.phase === 'submitting') return false;
  const targetId = store.targetId;
  const generation = store.requestGeneration + 1;
  store.requestGeneration = generation;
  store.phase = 'loading-preview';
  store.errorCode = null;
  store.errorMessage = null;
  store.result = null;
  renderDeletionDialog(store);
  let response;
  let payload;
  try {
    response = await fetch(deletionPath(store), {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    payload = await deletionResponseJson(response);
  } catch (_error) {
    if (generation !== store.requestGeneration || targetId !== store.targetId) return false;
    store.phase = 'error';
    store.errorCode = 'invalid_response';
    store.errorMessage = fixedDeletionError('invalid_response');
    renderDeletionDialog(store);
    return false;
  }
  if (generation !== store.requestGeneration || targetId !== store.targetId) return false;
  if (!response.ok) {
    const code = deletionErrorCode(payload);
    store.errorCode = code;
    store.errorMessage = fixedDeletionError(code);
    if (code === 'deletion_target_busy') store.phase = 'busy';
    else if (code === 'deletion_revision_stale' || code === 'deletion_write_conflict') store.phase = 'stale';
    else if (code === 'deletion_target_not_found') {
      store.phase = 'unconfirmed';
      store.outcomeUnconfirmed = true;
      store.convergence = store.kind === 'conversation'
        ? { sessions: false, runs: false }
        : { memory: false, approvals: false };
      await reconcileDeletionCollections(store, false);
      return false;
    } else store.phase = 'error';
    renderDeletionDialog(store);
    return false;
  }
  const preview = validateDeletionPreview(payload, store.kind, targetId);
  if (!preview) {
    store.phase = 'error';
    store.errorCode = 'invalid_response';
    store.errorMessage = fixedDeletionError('invalid_response');
    renderDeletionDialog(store);
    return false;
  }
  const previousRevision = store.preview?.deletionRevision || null;
  store.preview = preview;
  store.errorCode = null;
  store.errorMessage = null;
  store.outcomeUnconfirmed = false;
  store.staleNotice = ['stale', 'sse'].includes(reason)
    || (previousRevision !== null && previousRevision !== preview.deletionRevision);
  if (preview.status === 'completed') {
    await beginDeletionReconciliation(store);
    return true;
  }
  if (preview.status === 'busy' || store.localBusy) store.phase = 'busy';
  else if (preview.status === 'partial') store.phase = 'partial';
  else if (preview.status === 'unavailable') {
    store.phase = 'error';
    store.errorCode = 'deletion_unavailable';
    store.errorMessage = fixedDeletionError('deletion_unavailable');
  } else store.phase = store.staleNotice ? 'stale' : 'review';
  renderDeletionDialog(store);
  return true;
}

function prepareDeletionConvergence(store) {
  const targetId = store.targetId;
  if (store.kind === 'conversation') {
    conversationDeletionTombstones.add(targetId);
    store.convergence = { sessions: false, runs: false };
    sessionsStore.requestId += 1;
    sessionDetailStore.requestId += 1;
    sessionDetailStore.selectionVersion += 1;
    runsStore.requestId += 1;
    runDetailStore.requestId += 1;
    runtimeTraceStore.listRequestId += 1;
    runtimeTraceStore.detailRequestId += 1;
    sessionsStore.items = sessionsStore.items.filter((item) => item.id !== targetId);
    runsStore.items = runsStore.items.filter((item) => item.sessionId !== targetId);
    const selectedRunSession = runDetailStore.data?.run?.sessionId
      || runsStore.items.find((item) => item.id === runDetailStore.runId)?.sessionId;
    if (selectedRunSession === targetId) resetRunDetail();
    if (sessionDetailStore.sessionId === targetId) {
      sessionDetailStore.sessionId = null;
      sessionDetailStore.data = null;
      sessionDetailStore.phase = 'idle';
    }
    clearStoredSessionSelection(targetId);
    if (chatStore.targetMode === 'existing'
        && (chatStore.activeTargetSessionId === targetId
          || sessionDetailStore.sessionId === null)) {
      chatStore.targetMode = 'new';
    }
    renderSessionSurfaces();
    return;
  }
  projectMemoryDeletionTombstones.add(targetId);
  store.convergence = { memory: false, approvals: false };
  memoryStore.requestId += 1;
  memoryApprovalStore.requestId += 1;
  memoryApprovalStore.actionGeneration += 1;
  if (memoryStore.data) {
    memoryStore.data.items = memoryStore.data.items.filter((item) => item.id !== targetId);
  }
  memoryApprovalStore.items = memoryApprovalStore.items.filter(
    (item) => item.memoryId !== targetId,
  );
  if (memoryApprovalStore.selectedMemoryId === targetId) {
    memoryApprovalStore.selectedMemoryId = null;
  }
  if (memoryApprovalStore.actingMemoryId === targetId) {
    memoryApprovalStore.actingMemoryId = null;
    memoryApprovalRefreshQueued = false;
  }
  renderRouteOnly('memory');
}

async function reconcileDeletionCollections(store, authoritativeCompletion = true) {
  const generation = store.actionGeneration;
  const targetId = store.targetId;
  store.phase = 'reconciling';
  renderDeletionDialog(store);
  if (store.kind === 'conversation') {
    await Promise.all([
      loadSessions(false, true),
      loadRuns(false),
      loadDashboardSnapshot(),
      chatStore.activeTurnId ? checkActiveTurnStatus(true) : Promise.resolve(),
    ]);
  } else {
    await Promise.all([
      loadMemory(false),
      loadMemoryApprovals(true),
      loadDashboardSnapshot(),
    ]);
  }
  if (generation !== store.actionGeneration || targetId !== store.targetId) return false;
  const converged = store.convergence
    && Object.values(store.convergence).every(Boolean);
  if (converged) {
    if (store.kind === 'conversation') conversationDeletionTombstones.delete(targetId);
    else projectMemoryDeletionTombstones.delete(targetId);
    store.phase = 'completed';
    store.outcomeUnconfirmed = false;
    store.errorCode = null;
    store.errorMessage = authoritativeCompletion
      ? null
      : '权威集合已确认目标不存在；未依据单独 404 推断成功。';
  } else {
    store.phase = 'unconfirmed';
    store.outcomeUnconfirmed = true;
    store.errorCode = 'connection_lost';
    store.errorMessage = '已提交删除，等待重新读取；旧响应已被围栏，不会自动重发。';
  }
  renderDeletionDialog(store);
  return converged;
}

async function beginDeletionReconciliation(store) {
  store.actionGeneration += 1;
  prepareDeletionConvergence(store);
  return reconcileDeletionCollections(store, true);
}

async function submitDeletion(kind = null) {
  const store = kind ? deletionStoreFor(kind) : activeDeletionStore();
  if (!deletionCanSubmit(store)) return false;
  const targetId = store.targetId;
  const preview = store.preview;
  const revision = preview.deletionRevision;
  const generation = store.actionGeneration + 1;
  store.actionGeneration = generation;
  store.phase = 'submitting';
  store.errorCode = null;
  store.errorMessage = null;
  renderDeletionDialog(store);
  let response;
  let payload;
  try {
    response = await fetch(deletionPath(store), {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ deletionRevision: revision }),
    });
    payload = await deletionResponseJson(response);
  } catch (_error) {
    if (generation !== store.actionGeneration || targetId !== store.targetId) return false;
    store.phase = 'unconfirmed';
    store.outcomeUnconfirmed = true;
    store.errorCode = 'connection_lost';
    store.errorMessage = fixedDeletionError('connection_lost');
    renderDeletionDialog(store);
    return false;
  }
  if (generation !== store.actionGeneration || targetId !== store.targetId) return false;
  if (!response.ok) {
    const code = deletionErrorCode(payload);
    store.errorCode = code;
    store.errorMessage = fixedDeletionError(code);
    if (code === 'deletion_revision_stale' || code === 'deletion_write_conflict') {
      store.phase = 'stale';
      await loadDeletionPreview(store, 'stale');
      if (targetId === store.targetId && store.preview) {
        store.phase = store.preview.status === 'partial' ? 'partial' : 'stale';
        store.errorCode = code;
        store.errorMessage = fixedDeletionError(code);
        renderDeletionDialog(store);
      }
    } else if (code === 'deletion_target_busy') {
      store.phase = 'busy';
      renderDeletionDialog(store);
    } else if (code === 'deletion_target_not_found') {
      store.phase = 'unconfirmed';
      store.outcomeUnconfirmed = true;
      store.convergence = store.kind === 'conversation'
        ? { sessions: false, runs: false }
        : { memory: false, approvals: false };
      await reconcileDeletionCollections(store, false);
    } else {
      store.phase = ['deletion_store_busy', 'deletion_unavailable'].includes(code)
        ? 'unconfirmed'
        : 'error';
      store.outcomeUnconfirmed = store.phase === 'unconfirmed';
      renderDeletionDialog(store);
    }
    return false;
  }
  const result = validateDeletionResult(
    payload,
    store.kind,
    targetId,
    revision,
  );
  if (!result) {
    store.phase = 'error';
    store.errorCode = 'invalid_response';
    store.errorMessage = fixedDeletionError('invalid_response');
    renderDeletionDialog(store);
    return false;
  }
  store.result = result;
  if (result.status === 'partial') {
    store.phase = 'partial';
    store.preview = null;
    store.errorCode = 'deletion_retry_required';
    store.errorMessage = fixedDeletionError('deletion_retry_required');
    renderDeletionDialog(store);
    return false;
  }
  await beginDeletionReconciliation(store);
  return true;
}

function wireDeletionDialog() {
  ensureDeletionDialogHost();
  document.addEventListener('keydown', (event) => {
    const store = activeDeletionStore();
    const dialog = document.querySelector('.deletion-dialog');
    if (!store || !dialog) return;
    if (event.key === 'Escape') {
      if (store.phase !== 'submitting') {
        event.preventDefault();
        closeDeletionDialog(store.kind);
      }
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...dialog.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    )];
    if (!focusable.length) {
      event.preventDefault();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}

function fixedPermissionError(code = 'permission_unavailable') {
  const messages = {
    invalid_request: '审批请求无效，已停用当前操作并重新读取。',
    permission_not_found: '该审批已结束或不再属于当前 Gateway。',
    permission_turn_mismatch: '审批与 Turn 不匹配，未执行任何 Tool 操作。',
    permission_already_decided: '该审批已由另一决定完成。',
    permission_expired: '该审批已过期，未执行任何 Tool 操作。',
    permission_cancelled: '该审批已随 Turn 取消。',
    permission_unavailable: '当前 Gateway 无法提供权限审批。',
    permission_not_reviewable: '该内容不能安全审查，只能拒绝。',
    invalid_response: '审批响应未通过安全验证，未确认任何决定。',
    connection_lost: '审批连接已中断；不会自动重试决定，请手动刷新状态。',
  };
  return messages[code] || messages.permission_unavailable;
}

async function permissionResponseJson(response, maxBytes = PERMISSION_PENDING_MAX_BYTES) {
  const text = await response.text();
  if (utf8ByteLength(text) > maxBytes) throw new Error('invalid_response');
  return JSON.parse(text);
}

async function loadPendingPermissions() {
  const requestId = permissionStore.requestId + 1;
  permissionStore.requestId = requestId;
  permissionStore.actionGeneration += 1;
  permissionStore.actingPermissionId = null;
  permissionStore.phase = 'loading';
  permissionStore.error = null;
  renderPermissionPanel();
  try {
    const response = await fetch('/api/v1/permissions/pending', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    const payload = await permissionResponseJson(response);
    if (requestId !== permissionStore.requestId) return false;
    const validated = response.ok ? validatePermissionPendingPayload(payload) : null;
    if (!validated) {
      const code = typeof payload?.error?.code === 'string' ? payload.error.code : 'invalid_response';
      throw new Error(code === 'permission_unavailable' ? code : 'invalid_response');
    }
    const currentItems = validated.items.filter(
      (item) => !retiredPermissionTurnIds.has(item.turnId),
    );
    permissionStore.items = currentItems;
    permissionStore.revision = validated.revision;
    permissionStore.phase = currentItems.length ? 'live' : 'empty';
    permissionStore.error = null;
    permissionStore.lastUpdatedAt = validated.generatedAt;
    renderPermissionPanel();
    return true;
  } catch (error) {
    if (requestId !== permissionStore.requestId) return false;
    permissionStore.phase = 'error';
    permissionStore.error = fixedPermissionError(
      error?.message === 'permission_unavailable' ? 'permission_unavailable' : 'invalid_response',
    );
    permissionStore.actingPermissionId = null;
    renderPermissionPanel();
    return false;
  }
}

function permissionActionAvailable(item, decision) {
  if (permissionStore.phase !== 'live'
      || permissionStore.actingPermissionId !== null
      || permissionStore.items[0] !== item
      || retiredPermissionTurnIds.has(item.turnId)
      || !item.choices.includes(decision)) return false;
  if (item.turnId === chatStore.activeTurnId
      && ['cancelling', 'cancel_requested', 'cancelled'].includes(chatStore.phase)) return false;
  return decision === 'deny_once' || canAllowPermission(item);
}

function disablePermissionActionsForTurn(turnId) {
  if (!permissionStore.items.some((item) => item.turnId === turnId)) return;
  permissionStore.actionGeneration += 1;
  permissionStore.actingPermissionId = null;
  renderPermissionPanel();
}

function retirePermissionTurn(turnId) {
  if (!TURN_ID_PATTERN.test(turnId || '') || retiredPermissionTurnIds.has(turnId)) return false;
  retiredPermissionTurnIds.add(turnId);
  permissionStore.requestId += 1;
  permissionStore.actionGeneration += 1;
  permissionStore.actingPermissionId = null;
  permissionStore.items = permissionStore.items.filter((item) => item.turnId !== turnId);
  const reconciliation = loadPendingPermissions();
  permissionStore.reconciliationPromise = reconciliation;
  reconciliation.then(() => {
    if (permissionStore.reconciliationPromise === reconciliation) {
      permissionStore.reconciliationPromise = null;
    }
  });
  return true;
}

async function decidePermission(permissionId, turnId, decision) {
  const item = permissionStore.items[0];
  if (!item
      || item.permissionId !== permissionId
      || item.turnId !== turnId
      || !['allow_once', 'deny_once'].includes(decision)
      || !permissionActionAvailable(item, decision)) return false;
  const actionGeneration = permissionStore.actionGeneration + 1;
  permissionStore.actionGeneration = actionGeneration;
  permissionStore.actingPermissionId = permissionId;
  permissionStore.error = null;
  renderPermissionPanel();
  let response;
  try {
    response = await fetch(`/api/v1/permissions/${encodeURIComponent(permissionId)}/decision`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ turnId, decision }),
    });
  } catch (_error) {
    if (actionGeneration !== permissionStore.actionGeneration) return false;
    permissionStore.phase = 'error';
    permissionStore.error = fixedPermissionError('connection_lost');
    permissionStore.actingPermissionId = null;
    renderPermissionPanel();
    return false;
  }

  let payload = null;
  try {
    payload = await permissionResponseJson(response, 16 * 1024);
  } catch (_error) {
    // A received but malformed response is reconciled from pending authority.
  }
  if (actionGeneration !== permissionStore.actionGeneration
      || permissionStore.items[0]?.permissionId !== permissionId
      || permissionStore.items[0]?.turnId !== turnId) return false;
  if (response.ok && validPermissionDecisionPayload(payload, { permissionId, turnId, decision })) {
    permissionStore.actingPermissionId = null;
    await loadPendingPermissions();
    return true;
  }

  const code = typeof payload?.error?.code === 'string' ? payload.error.code : 'invalid_response';
  permissionStore.phase = 'error';
  permissionStore.error = fixedPermissionError(code);
  permissionStore.actingPermissionId = null;
  renderPermissionPanel();
  await loadPendingPermissions();
  return false;
}

function permissionReviewHtml(item) {
  const review = item.review;
  if (item.kind === 'network') {
    if (!item.reviewable) {
      return '<div class="permission-review network"><strong>网络请求详情不可安全审查，只能拒绝。</strong></div>';
    }
    const host = review.hostname.includes(':') ? `[${review.hostname}]` : review.hostname;
    const destination = `${review.scheme}://${host}:${review.port}`;
    const body = review.hasBody ? '有' : '无';
    const sensitive = review.hasSensitiveHeaders ? '有（值已隐藏）' : '无';
    return `<div class="permission-review network"><span>方法</span><code>${esc(review.method)}</code><span>目标</span><code>${esc(destination)}</code><span>路径</span><code>${esc(review.pathSummary)}</code><span>请求体</span><p>${esc(body)}</p><span>敏感请求头</span><p>${esc(sensitive)}</p></div>`;
  }
  if (item.kind === 'edit') {
    return `<div class="permission-review"><span>目标文件</span><code>${esc(review.targetPath)}</code><span>变更预览</span><pre aria-label="文件变更预览"><code>${esc(review.diffPreview)}</code></pre></div>`;
  }
  if (item.kind === 'command') {
    const hidden = review.commandPreview === PERMISSION_REDACTED_REVIEW || review.redacted;
    return `<div class="permission-review"><span>命令预览</span><pre aria-label="命令预览"><code>${esc(review.commandPreview)}</code></pre><span>工作目录</span><code>${esc(review.cwd)}</code><span>安全原因</span><p>${esc(review.reason)}</p>${hidden ? '<strong>内容因安全原因隐藏，只能拒绝。</strong>' : ''}</div>`;
  }
  return `<div class="permission-review path"><strong>Workspace 外部路径访问</strong><p>意图：${esc(review.intent)}。绝对路径不会显示；此类请求只能拒绝。</p></div>`;
}

function renderPermissionPanel() {
  const panel = document.querySelector('#permission-panel');
  if (!panel) return;
  const item = permissionStore.items[0] || null;
  if (!item && permissionStore.phase !== 'error') {
    panel.hidden = true;
    panel.innerHTML = '';
    return;
  }
  panel.hidden = false;
  if (!item) {
    panel.innerHTML = `<div class="permission-panel-head"><h2>权限审批</h2></div><div class="permission-error" role="alert"><p>${esc(permissionStore.error || fixedPermissionError())}</p><button type="button" data-permission-retry>Retry</button></div>`;
    panel.querySelector('[data-permission-retry]')?.addEventListener('click', () => { loadPendingPermissions(); });
    return;
  }
  const sameTurn = item.turnId === chatStore.activeTurnId;
  const acting = permissionStore.actingPermissionId === item.permissionId;
  const allowVisible = canAllowPermission(item);
  const allowEnabled = permissionActionAvailable(item, 'allow_once');
  const denyEnabled = permissionActionAvailable(item, 'deny_once');
  const stateText = sameTurn
    ? '当前 Tool 正在等待权限审批'
    : '此 Workspace 的其他任务正在等待审批';
  const error = permissionStore.error
    ? `<div class="permission-error" role="alert"><p>${esc(permissionStore.error)}</p><button type="button" data-permission-retry>Retry</button></div>`
    : '';
  const safety = !allowVisible
    ? '<p class="permission-deny-only">审查不完整、被截断、已隐藏或属于外部路径；只能拒绝。</p>'
    : '';
  panel.innerHTML = `<div class="permission-panel-head"><h2>等待权限审批</h2><span>${esc(`1 / ${permissionStore.items.length}`)}</span></div><article class="permission-card"><div class="permission-context" role="status" aria-live="polite">${esc(stateText)}</div><div class="permission-summary"><code>${esc(item.toolName)}</code><span>${esc(item.kind)}</span></div><p>${esc(item.summary)}</p>${permissionReviewHtml(item)}${safety}<small>本次请求将于 ${esc(new Date(item.expiresAt).toLocaleTimeString('zh-CN', { hour12: false }))} 到期。</small>${error}<div class="permission-actions">${allowVisible ? `<button type="button" class="permission-allow" data-permission-decision="allow_once" ${allowEnabled ? '' : 'disabled'}>${acting ? '处理中…' : '仅允许这一次'}</button>` : ''}<button type="button" class="permission-deny" data-permission-decision="deny_once" ${denyEnabled ? '' : 'disabled'}>${acting ? '处理中…' : '拒绝这一次'}</button></div></article>`;
  panel.querySelectorAll('[data-permission-decision]').forEach((button) => {
    button.addEventListener('click', () => {
      decidePermission(item.permissionId, item.turnId, button.dataset.permissionDecision);
    });
  });
  panel.querySelector('[data-permission-retry]')?.addEventListener('click', () => { loadPendingPermissions(); });
}

function renderSessionMenu() {
  const menu = document.querySelector('#session-menu');
  if (!sessionsStore.items.length) {
    menu.innerHTML = '<div class="dock-menu-empty">当前 Workspace 暂无可选 Session。</div>';
    return;
  }
  menu.innerHTML = sessionsStore.items.map((session) => `<button class="${session.id === sessionDetailStore.sessionId ? 'on' : ''}" onclick="selectHistoricalSession('${esc(session.id)}')"><b>${esc(session.title)}</b><small>${esc(formatSnapshotTime(session.updatedAt))} · ${esc(session.messageCount)} msg · saved</small></button>`).join('');
}

function chatTargetSessionId() {
  return chatStore.targetMode === 'existing' && SESSION_ID_PATTERN.test(sessionDetailStore.sessionId || '')
    ? sessionDetailStore.sessionId
    : null;
}

function syncChatControls() {
  const input = document.querySelector('#message');
  const submit = document.querySelector('#chat-submit');
  const cancel = document.querySelector('#chat-cancel');
  const submitting = Boolean(chatStore.activeTurnId)
    || ['submitting', 'recovering', 'in_progress', 'cancelling', 'cancel_requested', 'committing'].includes(chatStore.phase);
  if (input.value !== chatStore.draft) input.value = chatStore.draft;
  input.disabled = submitting;
  input.placeholder = chatStore.targetMode === 'new' ? '开始新的 CodeLoop 对话' : '继续所选 Session';
  submit.disabled = submitting || !chatStore.draft.trim();
  submit.textContent = submitting ? '发送中…' : '发送';
  const cancellable = Boolean(chatStore.activeTurnId)
    && ['submitting', 'recovering', 'in_progress', 'cancelling', 'cancel_requested'].includes(chatStore.phase);
  cancel.hidden = !cancellable;
  cancel.disabled = ['cancelling', 'cancel_requested'].includes(chatStore.phase);
  cancel.textContent = chatStore.phase === 'cancelling'
    ? '取消中…'
    : chatStore.phase === 'cancel_requested' ? '已请求取消' : '取消';
}

function chatFeedback() {
  if (!chatStore.error) return '';
  const kind = chatStore.phase === 'conflict'
    ? 'conflict'
    : ['cancelling', 'cancel_requested', 'committing', 'cancelled', 'completed_unavailable'].includes(chatStore.phase) ? 'info' : 'error';
  const title = chatStore.phase === 'conflict'
    ? '请求身份或 Session 已变化'
    : chatStore.phase === 'in_progress'
      ? '本轮可能仍在处理'
      : ['cancelling', 'cancel_requested'].includes(chatStore.phase)
        ? '正在协作取消'
        : chatStore.phase === 'committing'
          ? '本轮正在提交'
          : chatStore.phase === 'cancelled'
            ? '本轮已取消'
      : chatStore.phase === 'interrupted'
        ? '本轮已中断'
        : '本轮状态';
  const check = chatStore.activeTurnId && ['in_progress', 'recovery_error', 'error', 'cancel_requested', 'committing'].includes(chatStore.phase)
    ? '<button class="snapshot-button" onclick="checkActiveTurnStatus()">检查状态</button>'
    : '';
  return `<div class="dock-chat-feedback ${kind}" role="alert"><b>${title}</b><p>${esc(chatStore.error)}</p><small>不会自动重发；Turn revision 变化时只会检查既有状态。</small>${check}</div>`;
}

function resetChatFeedbackTarget() {
  chatStore.feedbackGeneration += 1;
  chatStore.feedbackTurnId = null;
  chatStore.feedbackRunId = null;
  chatStore.feedbackSessionId = null;
  chatStore.feedbackPhase = 'idle';
  chatStore.feedbackSignal = null;
  chatStore.feedbackError = null;
}

function setCompletedFeedbackTarget(payload) {
  if (
    !TURN_ID_PATTERN.test(payload?.turnId || '')
    || !CHAT_STREAM_RUN_ID_PATTERN.test(payload?.runId || '')
    || !SESSION_ID_PATTERN.test(payload?.sessionId || '')
  ) {
    resetChatFeedbackTarget();
    return;
  }
  chatStore.feedbackGeneration += 1;
  chatStore.feedbackTurnId = payload.turnId;
  chatStore.feedbackRunId = payload.runId;
  chatStore.feedbackSessionId = payload.sessionId;
  chatStore.feedbackPhase = 'available';
  chatStore.feedbackSignal = null;
  chatStore.feedbackError = null;
}

function validChatFeedbackResponse(payload, turnId, runId, signal) {
  return payload?.ok === true
    && payload?.schemaVersion === 1
    && payload?.mode === 'read-write'
    && payload?.turnId === turnId
    && payload?.runId === runId
    && payload?.signal === signal
    && payload?.source === 'explicit_user_action'
    && typeof payload?.recordedAt === 'string';
}

async function recordChatFeedback(signal) {
  const turnId = chatStore.feedbackTurnId;
  const runId = chatStore.feedbackRunId;
  if (
    !['accept', 'correct', 'reject'].includes(signal)
    || !TURN_ID_PATTERN.test(turnId || '')
    || !CHAT_STREAM_RUN_ID_PATTERN.test(runId || '')
    || !['available', 'error'].includes(chatStore.feedbackPhase)
  ) return;
  const generation = chatStore.feedbackGeneration + 1;
  chatStore.feedbackGeneration = generation;
  chatStore.feedbackPhase = 'submitting';
  chatStore.feedbackError = null;
  renderConversationDock();
  try {
    const response = await fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}/feedback`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ signal }),
    });
    const payload = await response.json().catch(() => null);
    if (
      generation !== chatStore.feedbackGeneration
      || chatStore.feedbackTurnId !== turnId
      || chatStore.feedbackRunId !== runId
    ) return;
    if (
      response.status === 409
      && ['feedback_conflict', 'feedback_unavailable'].includes(payload?.error?.code)
    ) {
      chatStore.feedbackPhase = payload.error.code === 'feedback_conflict'
        ? 'conflict'
        : 'unavailable';
      chatStore.feedbackError = payload.error.code === 'feedback_conflict'
        ? '这一 Run 已记录另一项不可变反馈。'
        : '这一 Run 当前无法记录反馈。';
    } else if (response.status === 404) {
      chatStore.feedbackPhase = 'unavailable';
      chatStore.feedbackError = '这一 Turn 已不可用。';
    } else if (!response.ok || !validChatFeedbackResponse(payload, turnId, runId, signal)) {
      throw new Error('feedback response contract mismatch');
    } else {
      chatStore.feedbackPhase = 'recorded';
      chatStore.feedbackSignal = signal;
      chatStore.feedbackError = null;
    }
  } catch (_error) {
    if (
      generation !== chatStore.feedbackGeneration
      || chatStore.feedbackTurnId !== turnId
      || chatStore.feedbackRunId !== runId
    ) return;
    chatStore.feedbackPhase = 'error';
    chatStore.feedbackError = '反馈暂时未确认；可再次明确选择。';
  }
  renderConversationDock();
}

function chatUserSignal() {
  if (
    !chatStore.feedbackTurnId
    || !chatStore.feedbackRunId
    || chatStore.feedbackPhase === 'idle'
    || chatStore.targetMode !== 'existing'
    || sessionDetailStore.sessionId !== chatStore.feedbackSessionId
  ) return '';
  const labels = {
    accept: '已接受结果',
    correct: '已标记需要纠正',
    reject: '已拒绝结果',
  };
  if (chatStore.feedbackPhase === 'recorded') {
    return `<section class="dock-user-signal recorded"><b>${esc(labels[chatStore.feedbackSignal] || '反馈已记录')}</b><p>只保存了这一项显式选择，不保存消息正文或原因。</p></section>`;
  }
  if (['conflict', 'unavailable'].includes(chatStore.feedbackPhase)) {
    return `<section class="dock-user-signal unavailable"><b>反馈不可用</b><p>${esc(chatStore.feedbackError || '这一 Run 无法记录反馈。')}</p></section>`;
  }
  const disabled = chatStore.feedbackPhase === 'submitting' ? 'disabled' : '';
  const status = chatStore.feedbackPhase === 'submitting'
    ? '<small>正在记录显式选择…</small>'
    : chatStore.feedbackError
      ? `<small class="error">${esc(chatStore.feedbackError)}</small>`
      : '<small>不会把沉默或后续消息当作接受。</small>';
  return `<section class="dock-user-signal"><b>这次结果是否解决了任务？</b><p>选择会作为与当前 Run 绑定的内容无关证据。</p><div class="dock-user-signal-actions"><button class="snapshot-button" ${disabled} onclick="recordChatFeedback('accept')">接受结果</button><button class="snapshot-button" ${disabled} onclick="recordChatFeedback('correct')">需要纠正</button><button class="snapshot-button" ${disabled} onclick="recordChatFeedback('reject')">拒绝结果</button></div>${status}</section>`;
}

function newConversation() {
  if (chatStore.activeTurnId) return;
  chatStore.targetMode = 'new';
  chatStore.phase = 'idle';
  chatStore.error = null;
  renderConversationDock();
  document.querySelector('#message').focus();
}

function fixedChatError(code) {
  const messages = {
    invalid_request: '消息请求无效，请检查内容后再发送。',
    session_not_found: '所选 Session 已不存在；请选择其他 Session 或开始新对话。',
    session_conflict: '该 Session 已被其他进程更新。已刷新最新内容，请确认后手动发送。',
    session_busy: 'Session 存储正忙，草稿已保留，请稍后手动发送。',
    runtime_unavailable: 'CodeLoop Runtime 暂时不可用，草稿已保留。',
    turn_failed: 'CodeLoop 未能完成并提交本轮，草稿已保留。',
    turn_id_conflict: '该 turnId 已属于另一请求。本轮没有执行；请明确重新发送以生成新身份。',
    turn_in_progress: '该请求可能仍在处理中。页面不会自动重发；可等待只读状态检查或手动检查。',
    turn_interrupted: '该请求已中断且不会自动重跑。若要重试，请明确重新发送。',
    turn_cancelled: '本轮已取消；不会自动重发。已经发生的 Tool 副作用无法回滚。',
    completed_unavailable: '本轮已完成，但对应 Session 结果已不可用；不会误报为取消。',
    turn_not_found: '未找到该请求状态；本地恢复标记已安全清理。',
    status_unavailable: '暂时无法读取本轮状态。其他 Dashboard 页面不受影响。',
  };
  return messages[code] || messages.turn_failed;
}

function chatLogIsNearBottom(log = document.querySelector('#chat-log')) {
  return !log || log.scrollHeight - log.scrollTop - log.clientHeight <= 32;
}

function scheduleChatStreamRender(generation) {
  if (chatStreamRenderPending) return;
  chatStreamRenderPending = true;
  const follow = chatLogIsNearBottom();
  requestAnimationFrame(() => {
    chatStreamRenderPending = false;
    if (generation !== chatStreamStore.generation) return;
    renderConversationDock(follow);
  });
}

function chatStreamPresentationHtml() {
  if (chatStreamStore.turnId !== chatStore.activeTurnId || chatStreamStore.phase === 'idle') return '';
  const labels = {
    connecting: '正在建立本轮专属流…',
    ready: '已连接，等待 Assistant…',
    generating: 'Assistant 正在生成（临时）',
    tool: 'Tool 状态已更新',
    completed: '已完成，正在重新读取最终 Session…',
    error: '本轮已返回安全错误状态',
    disconnected: '连接已中断；临时内容不完整、未确认',
  };
  const phaseLabel = ['cancelling', 'cancel_requested'].includes(chatStore.phase)
    ? '取消已请求；Provider 或 Tool 可能仍会产生迟到增量'
    : chatStore.phase === 'committing'
      ? '结果正在提交；最终以 Sessions REST 为准'
      : labels[chatStreamStore.phase] || '本轮临时展示';
  const assistant = chatStreamStore.provisionalText
    ? `<article class="chat-message assistant provisional"><small>MINICODE · 生成中 / 临时</small><div>${esc(chatStreamStore.provisionalText)}</div></article>`
    : '';
  const tools = chatStreamStore.tools.length
    ? `<div class="dock-stream-tools">${chatStreamStore.tools.map((tool) => `<div class="dock-stream-tool ${esc(tool.status)}"><code>${esc(tool.toolName)}</code><span>${esc(tool.status === 'running' ? 'running' : tool.status)}</span></div>`).join('')}</div>`
    : '';
  const notices = [
    chatStreamStore.truncatedAssistant ? 'Assistant 临时展示已截断；最终 Session 不受影响。' : '',
    chatStreamStore.truncatedTools ? 'Tool 临时状态已截断；Agent 执行不受影响。' : '',
    chatStreamStore.incomplete && chatStreamStore.phase !== 'disconnected' ? '临时流存在缺口；不会拼接缺失正文，最终以 Session 为准。' : '',
  ].filter(Boolean).map((notice) => `<small>${esc(notice)}</small>`).join('');
  return `<section class="dock-chat-stream ${chatStreamStore.incomplete ? 'incomplete' : ''}" data-stream-phase="${esc(chatStreamStore.phase)}"><div class="dock-stream-phase" role="status" aria-live="polite">${esc(phaseLabel)}</div>${assistant}${tools}${notices}</section>`;
}

function stopActiveChatStreamReader() {
  const reader = activeChatStreamReader;
  activeChatStreamReader = null;
  if (!reader) return;
  try {
    const cancellation = reader.cancel();
    if (cancellation && typeof cancellation.catch === 'function') cancellation.catch(() => {});
  } catch (_error) {
    // Page teardown never changes the Turn running on the Gateway.
  }
}

function validTurnStatus(payload, turnId) {
  return payload?.ok === true
    && payload?.schemaVersion === 1
    && payload?.mode === 'read-only'
    && payload?.turnId === turnId
    && ['accepted', 'running', 'cancel_requested', 'committing', 'completed', 'failed', 'interrupted', 'cancelled'].includes(payload?.status)
    && (payload?.sessionId === null || SESSION_ID_PATTERN.test(payload?.sessionId || ''))
    && (payload?.created === null || typeof payload?.created === 'boolean')
    && (payload?.runId === null || /^run_[0-9a-f]{32}$/.test(payload?.runId || ''))
    && typeof payload?.createdAt === 'string'
    && typeof payload?.updatedAt === 'string'
    && (payload?.completedAt === null || typeof payload?.completedAt === 'string')
    && (payload?.errorCode === null || typeof payload?.errorCode === 'string')
    && typeof payload?.resultAvailable === 'boolean';
}

function finishCancelledTurn(turnId) {
  if (chatStore.activeTurnId !== turnId) return;
  if (chatStore.terminalTurnId === turnId && chatStore.terminalPromise) return;
  chatStore.operationGeneration += 1;
  chatStore.terminalTurnId = turnId;
  chatStore.terminalPromise = null;
  retirePermissionTurn(turnId);
  clearActiveTurn(turnId);
  chatStore.phase = 'cancelled';
  chatStore.error = '本轮已取消；不会自动重发。已经发生的 Tool 副作用无法回滚。';
}

function validCancelResponse(payload, turnId) {
  return payload?.ok === true
    && payload?.schemaVersion === 1
    && payload?.mode === 'read-write'
    && payload?.turnId === turnId
    && ['accepted', 'running', 'cancel_requested', 'committing', 'completed', 'failed', 'interrupted', 'cancelled'].includes(payload?.status)
    && typeof payload?.cancellationAccepted === 'boolean'
    && (payload?.sessionId === null || SESSION_ID_PATTERN.test(payload?.sessionId || ''))
    && (payload?.runId === null || /^run_[0-9a-f]{32}$/.test(payload?.runId || ''))
    && typeof payload?.updatedAt === 'string';
}

async function cancelActiveTurn() {
  const turnId = chatStore.activeTurnId;
  if (!TURN_ID_PATTERN.test(turnId || '') || ['cancelling', 'cancel_requested', 'committing'].includes(chatStore.phase)) return;
  const operationGeneration = chatStore.operationGeneration + 1;
  chatStore.operationGeneration = operationGeneration;
  if (chatStreamStore.turnId !== turnId) chatStore.requestGeneration += 1;
  chatStore.phase = 'cancelling';
  chatStore.error = '取消请求已记录；当前 Provider 或 Tool 调用可能需要完成后才能停止。';
  disablePermissionActionsForTurn(turnId);
  renderConversationDock();
  try {
    const response = await fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}/cancel`, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: '{}',
    });
    const payload = await response.json().catch(() => null);
    if (operationGeneration !== chatStore.operationGeneration || chatStore.activeTurnId !== turnId) return;
    if (response.status === 404) {
      retirePermissionTurn(turnId);
      clearActiveTurn(turnId);
      chatStore.phase = 'error';
      chatStore.error = fixedChatError('turn_not_found');
    } else if (!response.ok || !validCancelResponse(payload, turnId)) {
      throw new Error('cancel response unavailable');
    } else if (payload.status === 'cancelled') {
      finishCancelledTurn(turnId);
    } else if (payload.status === 'completed') {
      chatStore.phase = 'committing';
      chatStore.error = '结果正在提交，取消已无法抢占本轮完成。';
      await checkActiveTurnStatus();
      return;
    } else if (payload.status === 'committing') {
      chatStore.phase = 'committing';
      chatStore.error = '结果正在提交，取消已无法抢占本轮完成。';
    } else if (payload.status === 'failed' || payload.status === 'interrupted') {
      retirePermissionTurn(turnId);
      clearActiveTurn(turnId);
      chatStore.phase = payload.status === 'interrupted' ? 'interrupted' : 'error';
      chatStore.error = fixedChatError(payload.status === 'interrupted' ? 'turn_interrupted' : 'turn_failed');
    } else if (payload.cancellationAccepted) {
      chatStore.phase = 'cancel_requested';
      chatStore.error = '取消请求已记录；当前 Provider 或 Tool 调用可能需要完成后才能停止。';
    } else {
      chatStore.phase = 'in_progress';
      chatStore.error = fixedChatError('turn_in_progress');
    }
  } catch (_error) {
    if (operationGeneration !== chatStore.operationGeneration || chatStore.activeTurnId !== turnId) return;
    chatStore.phase = 'recovery_error';
    chatStore.error = '取消状态暂时不可用；不会自动重发，请等待只读状态检查或手动检查。';
  }
  renderConversationDock();
}

async function refreshCompletedTurn(payload, preserveRunSelection = false) {
  const turnId = payload.turnId;
  chatStore.draft = '';
  chatStore.targetMode = 'existing';
  chatStore.lastSessionId = payload.sessionId;
  chatStore.phase = 'committing';
  chatStore.error = null;
  sessionDetailStore.selectionVersion += 1;
  sessionDetailStore.sessionId = payload.sessionId;
  persistSessionSelection(payload.sessionId);
  await Promise.all([
    refreshSessions(),
    preserveRunSelection ? refreshRunsFromChangeFeed() : refreshRuns(),
    refreshDashboardSnapshot(),
    preserveRunSelection ? loadOps() : refreshOps(),
  ]);
  const loadOutcome = await loadSessionDetail(payload.sessionId, false, true);
  const finalLoaded = loadOutcome === 'loaded'
    && sessionDetailStore.sessionId === payload.sessionId
    && sessionDetailStore.data?.session?.id === payload.sessionId;
  if (!finalLoaded) {
    chatStore.phase = 'completed_unavailable';
    chatStore.error = '本轮已提交，但最终 Session 尚未重新读取；保留临时内容和 Turn 恢复标记。';
    renderConversationDock();
    return false;
  }
  clearActiveTurn(turnId);
  if (chatStreamStore.turnId === turnId) resetChatStreamState();
  setCompletedFeedbackTarget(payload);
  chatStore.phase = 'success';
  chatStore.error = null;
  renderConversationDock();
  return true;
}

async function finalizeCompletedTurn(payload, preserveRunSelection = false) {
  const turnId = payload?.turnId;
  if (chatStore.activeTurnId !== turnId || !SESSION_ID_PATTERN.test(payload?.sessionId || '')) return false;
  if (chatStore.terminalTurnId === turnId && chatStore.terminalPromise) return chatStore.terminalPromise;
  retirePermissionTurn(turnId);
  chatStore.operationGeneration += 1;
  chatStore.terminalTurnId = turnId;
  const pending = refreshCompletedTurn(payload, preserveRunSelection);
  chatStore.terminalPromise = pending;
  try {
    const completed = await pending;
    if (!completed && chatStore.activeTurnId === turnId) chatStore.terminalTurnId = null;
    return completed;
  } finally {
    if (chatStore.terminalPromise === pending) chatStore.terminalPromise = null;
  }
}

async function checkActiveTurnStatus(fromLiveRefresh = false) {
  const turnId = chatStore.activeTurnId;
  if (!TURN_ID_PATTERN.test(turnId || '')) return;
  const operationGeneration = chatStore.operationGeneration + 1;
  chatStore.operationGeneration = operationGeneration;
  chatStore.phase = 'recovering';
  chatStore.error = null;
  renderConversationDock();
  try {
    const response = await fetch(`/api/v1/chat/turns/${encodeURIComponent(turnId)}`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });
    const payload = await response.json().catch(() => null);
    if (operationGeneration !== chatStore.operationGeneration || chatStore.activeTurnId !== turnId) return;
    if (response.status === 404) {
      chatStore.requestGeneration += 1;
      retirePermissionTurn(turnId);
      clearActiveTurn(turnId);
      chatStore.phase = 'error';
      chatStore.error = fixedChatError('turn_not_found');
      renderConversationDock();
      return;
    }
    if (!response.ok || !validTurnStatus(payload, turnId)) throw new Error('turn status unavailable');
    if (['completed', 'cancelled', 'failed', 'interrupted'].includes(payload.status)) {
      chatStore.requestGeneration += 1;
    }
    if (payload.status === 'completed') {
      if (!payload.resultAvailable || !SESSION_ID_PATTERN.test(payload.sessionId || '')) {
        retirePermissionTurn(turnId);
        clearActiveTurn(turnId);
        chatStore.phase = 'completed_unavailable';
        chatStore.error = fixedChatError('completed_unavailable');
      } else {
        await finalizeCompletedTurn(payload, fromLiveRefresh);
      }
    } else if (payload.status === 'cancelled') {
      finishCancelledTurn(turnId);
    } else if (payload.status === 'failed' || payload.status === 'interrupted') {
      retirePermissionTurn(turnId);
      clearActiveTurn(turnId);
      chatStore.phase = payload.status === 'interrupted' ? 'interrupted' : 'error';
      chatStore.error = fixedChatError(payload.errorCode || (payload.status === 'interrupted' ? 'turn_interrupted' : 'turn_failed'));
    } else if (payload.status === 'cancel_requested') {
      chatStore.phase = 'cancel_requested';
      chatStore.error = '取消请求已记录；当前 Provider 或 Tool 调用可能需要完成后才能停止。';
    } else if (payload.status === 'committing') {
      chatStore.phase = 'committing';
      chatStore.error = '结果正在提交，取消已无法抢占本轮完成。';
    } else {
      chatStore.phase = 'in_progress';
      chatStore.error = fixedChatError('turn_in_progress');
    }
  } catch (_error) {
    if (operationGeneration !== chatStore.operationGeneration || chatStore.activeTurnId !== turnId) return;
    chatStore.phase = 'recovery_error';
    chatStore.error = fixedChatError('status_unavailable');
  }
  renderConversationDock();
}

async function reconcileActiveTurnOnce() {
  if (chatStore.recoveryChecked) return;
  chatStore.recoveryChecked = true;
  const active = storedActiveTurn();
  if (!active) return;
  chatStore.activeTurnId = active.turnId;
  chatStore.activeTargetSessionId = active.targetSessionId;
  chatStore.targetMode = active.targetSessionId === null ? 'new' : 'existing';
  if (active.targetSessionId !== null) sessionDetailStore.sessionId = active.targetSessionId;
  await checkActiveTurnStatus();
}

async function submitChatTurn() {
  if (chatStore.activeTurnId || ['submitting', 'recovering', 'in_progress', 'cancelling', 'cancel_requested', 'committing'].includes(chatStore.phase)) return;
  const message = chatStore.draft.trim();
  if (!message || message.length > 32000) {
    chatStore.phase = 'error';
    chatStore.error = fixedChatError('invalid_request');
    renderConversationDock();
    return;
  }
  const targetSessionId = chatTargetSessionId();
  let turnId;
  try {
    turnId = createTurnId();
  } catch (_error) {
    chatStore.phase = 'error';
    chatStore.error = fixedChatError('turn_failed');
    renderConversationDock();
    return;
  }
  const requestGeneration = chatStore.requestGeneration + 1;
  chatStore.requestGeneration = requestGeneration;
  chatStore.phase = 'submitting';
  chatStore.error = null;
  resetChatFeedbackTarget();
  chatStore.activeTurnId = turnId;
  chatStore.activeTargetSessionId = targetSessionId;
  chatStore.terminalTurnId = null;
  chatStore.terminalPromise = null;
  resetChatStreamState(turnId, requestGeneration);
  persistActiveTurn(turnId, targetSessionId);
  renderConversationDock();
  try {
    const response = await fetch('/api/v1/chat/turns', {
      method: 'POST',
      headers: { Accept: 'application/x-ndjson', 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ message, sessionId: targetSessionId, turnId }),
    });
    const responseType = String(response.headers?.get('Content-Type') || '').split(';', 1)[0].trim().toLowerCase();
    if (response.ok && responseType === 'application/x-ndjson') {
      let terminalFrame = null;
      await consumeChatNdjson(response.body, {
        turnId,
        generation: requestGeneration,
        store: chatStreamStore,
        onFrame: (frame) => {
          if (requestGeneration !== chatStreamStore.generation || chatStore.activeTurnId !== turnId) return;
          if (frame.type === 'chat.turn.completed' || frame.type === 'chat.turn.error') terminalFrame = frame;
          scheduleChatStreamRender(requestGeneration);
        },
        onInvalid: () => scheduleChatStreamRender(requestGeneration),
      });
      if (requestGeneration !== chatStreamStore.generation || chatStore.activeTurnId !== turnId) return;
      if (terminalFrame?.type === 'chat.turn.completed') {
        chatStore.phase = 'committing';
        chatStore.error = '本轮已提交，正在从 Sessions REST 重新读取最终消息。';
        renderConversationDock();
        await finalizeCompletedTurn(terminalFrame);
        return;
      }
      if (terminalFrame?.type === 'chat.turn.error') {
        const code = terminalFrame.code;
        if (code === 'turn_cancelled') {
          finishCancelledTurn(turnId);
        } else {
          if (code !== 'turn_in_progress') retirePermissionTurn(turnId);
          chatStore.phase = code === 'turn_in_progress'
            ? 'in_progress'
            : ['session_conflict', 'turn_id_conflict'].includes(code) ? 'conflict' : code === 'turn_interrupted' ? 'interrupted' : 'error';
          chatStore.error = fixedChatError(code);
          if (!['turn_in_progress', 'turn_failed'].includes(code)) clearActiveTurn(turnId);
        }
        renderConversationDock();
        return;
      }
      detachChatStreamState(chatStreamStore);
      chatStore.phase = 'recovery_error';
      chatStore.error = 'Chat 流连接已中断；临时内容不完整、未确认。不会自动重发，请检查既有 Turn 状态。';
      renderConversationDock(false);
      return;
    }

    const payload = await response.json().catch(() => null);
    if (chatStreamStore.turnId === turnId) resetChatStreamState();
    if (requestGeneration !== chatStore.requestGeneration) {
      if (chatStore.activeTurnId !== turnId) return;
      const staleCode = typeof payload?.error?.code === 'string' ? payload.error.code : null;
      if (!response.ok && staleCode === 'turn_cancelled' && ['cancelling', 'cancel_requested'].includes(chatStore.phase)) {
        finishCancelledTurn(turnId);
        renderConversationDock();
      } else if (response.ok && chatStore.phase === 'committing') {
        await checkActiveTurnStatus();
      }
      return;
    }
    if (!response.ok) {
      const code = typeof payload?.error?.code === 'string' ? payload.error.code : 'turn_failed';
      if (code !== 'turn_in_progress') retirePermissionTurn(turnId);
      chatStore.phase = code === 'turn_in_progress'
        ? 'in_progress'
        : ['session_conflict', 'turn_id_conflict'].includes(code) ? 'conflict' : code === 'turn_interrupted' ? 'interrupted' : code === 'turn_cancelled' ? 'cancelled' : 'error';
      chatStore.error = fixedChatError(code);
      if (code === 'session_conflict') {
        await refreshSessions();
      } else if (code === 'session_not_found') {
        await refreshSessions();
        if (!sessionsStore.items.some((item) => item.id === targetSessionId)) chatStore.targetMode = 'new';
      }
      if (!['turn_in_progress', 'turn_failed'].includes(code)) clearActiveTurn(turnId);
      if (requestGeneration !== chatStore.requestGeneration) return;
      renderConversationDock();
      return;
    }
    const valid = payload?.ok === true
      && payload?.schemaVersion === 1
      && payload?.mode === 'read-write'
      && payload?.turnId === turnId
      && SESSION_ID_PATTERN.test(payload?.sessionId || '')
      && typeof payload?.created === 'boolean'
      && payload?.assistant?.role === 'assistant'
      && typeof payload?.assistant?.content === 'string'
      && typeof payload?.updatedAt === 'string'
      && (payload?.runId === null || /^run_[0-9a-f]{32}$/.test(payload?.runId || ''));
    if (!valid) throw new Error('chat response contract mismatch');

    retirePermissionTurn(turnId);
    clearActiveTurn(turnId);
    chatStore.draft = '';
    chatStore.targetMode = 'existing';
    chatStore.lastSessionId = payload.sessionId;
    sessionDetailStore.selectionVersion += 1;
    sessionDetailStore.sessionId = payload.sessionId;
    persistSessionSelection(payload.sessionId);
    await Promise.all([
      refreshSessions(),
      refreshRuns(),
      refreshDashboardSnapshot(),
      refreshOps(),
    ]);
    if (requestGeneration !== chatStore.requestGeneration) return;
    if (sessionDetailStore.sessionId !== payload.sessionId || !sessionDetailStore.data) {
      await loadSessionDetail(payload.sessionId, false, true);
    }
    if (requestGeneration !== chatStore.requestGeneration) return;
    setCompletedFeedbackTarget(payload);
    chatStore.phase = 'success';
    chatStore.error = null;
  } catch (_error) {
    if (requestGeneration !== chatStore.requestGeneration) return;
    if (chatStreamStore.turnId === turnId) detachChatStreamState(chatStreamStore);
    chatStore.phase = 'recovery_error';
    chatStore.error = '连接已中断，服务器可能已完成本轮。临时内容不完整、未确认；不会自动重发，请手动检查状态。';
  }
  renderConversationDock();
}

function renderConversationDock(followStream = null) {
  renderPermissionPanel();
  const log = document.querySelector('#chat-log');
  const status = document.querySelector('#dock-status');
  const presenting = chatStreamStore.turnId === chatStore.activeTurnId && chatStreamStore.phase !== 'idle';
  const shouldFollow = followStream === null
    ? (!presenting || chatLogIsNearBottom(log))
    : followStream;
  const settleScroll = () => {
    if (shouldFollow) log.scrollTop = log.scrollHeight;
  };
  renderSessionMenu();
  syncChatControls();
  if (chatStore.targetMode === 'new') {
    status.textContent = ['cancelling', 'cancel_requested', 'committing'].includes(chatStore.phase)
      ? chatStore.phase
      : presenting
      ? chatStreamStore.phase
      : chatStore.phase === 'submitting'
      ? 'submitting'
      : chatStore.phase === 'recovering' ? 'checking' : chatStore.phase === 'in_progress' ? 'recoverable' : chatStore.phase === 'success' ? 'success' : 'new';
    const stateTitle = chatStore.phase === 'submitting'
      ? 'CodeLoop 正在处理…'
      : chatStore.phase === 'recovering' ? '正在检查持久化状态…' : chatStore.phase === 'in_progress' ? '本轮可能仍在处理' : '开始新对话';
    log.innerHTML = `<div class="dock-session-summary"><b>新 Session</b><small>首条消息成功提交后创建并持久化。</small></div><div class="dock-state"><b>${stateTitle}</b><p>连接内临时展示、可恢复；最终正文只以 Sessions REST 为准，不会自动重发。</p></div>${chatStreamPresentationHtml()}${chatFeedback()}${chatUserSignal()}`;
    settleScroll();
    return;
  }
  if (sessionsStore.phase === 'idle' || sessionsStore.phase === 'loading') {
    status.textContent = 'loading';
    log.innerHTML = '<div class="dock-state"><b>正在读取真实 Session…</b><p>仅请求当前 Workspace 的 user / assistant 消息。</p></div>';
    return;
  }
  if (sessionsStore.phase === 'error' && !sessionsStore.items.length) {
    status.textContent = 'error';
    log.innerHTML = `<div class="dock-state error"><b>Session 列表不可用</b><p>${esc(sessionsStore.error || '请手动重试。')}</p><button class="snapshot-button" onclick="refreshSessions()">Retry</button></div>`;
    return;
  }
  if (!sessionsStore.items.length && !sessionDetailStore.sessionId) {
    chatStore.targetMode = 'new';
    renderConversationDock();
    return;
  }
  if (sessionDetailStore.phase === 'loading' || !sessionDetailStore.sessionId) {
    status.textContent = 'loading';
    log.innerHTML = '<div class="dock-state"><b>正在读取所选 Session…</b><p>消息正文不会写入浏览器存储。</p></div>';
    return;
  }
  if (sessionDetailStore.phase === 'error' || !sessionDetailStore.data) {
    status.textContent = 'error';
    log.innerHTML = `<div class="dock-state error"><b>Session 详情不可用</b><p>${esc(sessionDetailStore.error || '请手动重试。')}</p><button class="snapshot-button" onclick="loadSessionDetail('${esc(sessionDetailStore.sessionId)}', false, true)">Retry</button></div>`;
    return;
  }
  const detail = sessionDetailStore.data;
  const summary = sessionsStore.items.find((item) => item.id === sessionDetailStore.sessionId);
  const dockPhase = detail.source?.status === 'error' || detail.page?.hasMore ? 'partial' : 'live';
  status.textContent = ['cancelling', 'cancel_requested', 'committing'].includes(chatStore.phase)
    ? chatStore.phase
    : presenting
    ? chatStreamStore.phase
    : chatStore.phase === 'submitting'
    ? 'submitting'
    : chatStore.phase === 'recovering' ? 'checking' : chatStore.phase === 'in_progress' ? 'recoverable' : chatStore.phase === 'conflict' ? 'conflict' : dockPhase;
  const heading = `<div class="dock-session-summary"><b>${esc(summary?.title || `Session ${String(sessionDetailStore.sessionId).slice(0, 8)}`)}</b><small>${esc(formatSnapshotTime(detail.session?.updatedAt || summary?.updatedAt))} · ${esc(detail.messages.length)} visible messages</small></div>`;
  const messages = detail.messages.map((message) => `<article class="chat-message ${esc(message.role)}"><small>${message.role === 'user' ? 'YOU' : 'MINICODE'} · #${esc(message.index)}${message.truncated ? ' · truncated' : ''}</small><div>${esc(message.content)}</div></article>`).join('') || '<div class="dock-state"><b>没有可展示的对话消息</b><p>system、tool、thinking 和 transcript 不会显示。</p></div>';
  const more = detail.page?.hasMore ? `<button class="load-more dock-load-more" onclick="loadMoreSessionMessages()" ${sessionDetailStore.loadingMore ? 'disabled' : ''}>${sessionDetailStore.loadingMore ? '加载中…' : '加载更多消息'}</button>` : '';
  const submitting = !presenting && ['submitting', 'cancelling', 'cancel_requested', 'committing'].includes(chatStore.phase)
    ? `<div class="dock-chat-progress"><i></i><span>${esc(chatStore.phase === 'committing' ? '结果正在提交…' : chatStore.phase === 'submitting' ? 'CodeLoop 正在同步处理本轮…' : '正在等待安全取消点…')}</span></div>`
    : '';
  log.innerHTML = `${heading}${messages}${chatStreamPresentationHtml()}${submitting}${chatFeedback()}${chatUserSignal()}${more}`;
  settleScroll();
}

const SHELL_BREAKPOINTS = Object.freeze({
  dockOverlay: 1100,
  navOverlay: 640,
});

function setShellPanelState(panel, expanded) {
  const config = panel === 'nav'
    ? {
      hiddenClass: 'nav-hidden',
      panelId: 'nav',
      resizerId: 'nav-resizer',
      controls: ['nav-toggle', 'nav-reopen'],
    }
    : {
      hiddenClass: 'dock-closed',
      panelId: 'dock',
      resizerId: 'dock-resizer',
      controls: ['dock-close', 'dock-reopen'],
    };
  document.body.classList.toggle(config.hiddenClass, !expanded);
  document.querySelector(`#${config.panelId}`)?.setAttribute('aria-hidden', String(!expanded));
  document.querySelector(`#${config.resizerId}`)?.setAttribute('aria-hidden', String(!expanded));
  config.controls.forEach((id) => {
    document.querySelector(`#${id}`)?.setAttribute('aria-expanded', String(expanded));
  });
}

function wireResize(id, variable, storageKey, fromRight, min, max) {
  const handle = document.querySelector(`#${id}`);
  const controlled = document.querySelector(`#${handle.getAttribute('aria-controls')}`);
  if (controlled) {
    handle.setAttribute('aria-valuenow', String(Math.round(controlled.getBoundingClientRect().width)));
  }
  const commit = (raw) => {
    const value = Math.max(min, Math.min(max, Math.round(raw)));
    document.documentElement.style.setProperty(variable, `${value}px`);
    localStorage.setItem(storageKey, value);
    handle.setAttribute('aria-valuenow', String(value));
  };
  handle.addEventListener('mousedown', (event) => {
    event.preventDefault();
    document.body.classList.add('resizing');
    const move = (moveEvent) => {
      const raw = fromRight ? window.innerWidth - moveEvent.clientX : moveEvent.clientX;
      commit(raw);
    };
    const up = () => {
      document.body.classList.remove('resizing');
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
  handle.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const current = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue(variable),
    ) || Number(handle.getAttribute('aria-valuenow'));
    const screenDirection = event.key === 'ArrowRight' ? 1 : -1;
    const panelDirection = fromRight ? -screenDirection : screenDirection;
    commit(current + panelDirection * (event.shiftKey ? 24 : 8));
  });
}

function wireShell() {
  wireDeletionDialog();
  const navWidth = localStorage.getItem('miniNavW');
  const dockWidth = localStorage.getItem('miniDockW');
  if (navWidth) document.documentElement.style.setProperty('--nav-w', `${navWidth}px`);
  if (dockWidth) document.documentElement.style.setProperty('--dock-w', `${dockWidth}px`);
  setShellPanelState('dock', window.innerWidth > SHELL_BREAKPOINTS.dockOverlay);
  setShellPanelState('nav', window.innerWidth > SHELL_BREAKPOINTS.navOverlay);
  wireResize('nav-resizer', '--nav-w', 'miniNavW', false, 160, 320);
  wireResize('dock-resizer', '--dock-w', 'miniDockW', true, 280, 620);

  document.querySelector('#nav-toggle').addEventListener('click', () => setShellPanelState('nav', false));
  document.querySelector('#nav-reopen').addEventListener('click', () => {
    setShellPanelState('nav', true);
    document.querySelector('#nav-toggle').focus();
  });
  document.querySelector('#dock-close').addEventListener('click', () => setShellPanelState('dock', false));
  document.querySelector('#dock-reopen').addEventListener('click', () => {
    setShellPanelState('dock', true);
    document.querySelector('#dock-close').focus();
  });
  document.querySelector('#history-toggle').addEventListener('click', () => {
    const menu = document.querySelector('#session-menu');
    menu.hidden = !menu.hidden;
    document.querySelector('#history-toggle').setAttribute('aria-expanded', String(!menu.hidden));
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const menu = document.querySelector('#session-menu');
    if (!menu.hidden) {
      menu.hidden = true;
      document.querySelector('#history-toggle').setAttribute('aria-expanded', 'false');
      document.querySelector('#history-toggle').focus();
      return;
    }
    if (window.innerWidth <= SHELL_BREAKPOINTS.dockOverlay
        && !document.body.classList.contains('dock-closed')) {
      setShellPanelState('dock', false);
      document.querySelector('#dock-reopen').focus();
    }
  });
  document.querySelector('#dock-new').addEventListener('click', newConversation);
  document.querySelector('#dock-refresh').addEventListener('click', refreshSessions);
  document.querySelector('#chat-cancel').addEventListener('click', cancelActiveTurn);
  document.querySelector('#message').addEventListener('input', (event) => {
    chatStore.draft = event.target.value;
    syncChatControls();
  });
  document.querySelector('#chat-form').addEventListener('submit', (event) => {
    event.preventDefault();
    submitChatTurn();
  });
}

const querySection = new URLSearchParams(location.search).get('section');
if (!location.hash && VIEW_IDS.has(querySection)) history.replaceState({}, '', `${location.pathname}${location.search}#${querySection}`);

function loadRouteData() {
  const [view, sub] = currentRoute();
  if (view === 'overview' && observatoryStore.phase === 'idle') loadObservatory();
  if (view === 'runs' && runsStore.phase === 'idle') refreshRunsFromChangeFeed();
  if (view === 'sessions' && sessionsStore.phase === 'idle' && snapshotStore.phase !== 'loading') loadSessions(false);
  if (view === 'memory' && !['retrieval', 'injection'].includes(sub || 'overview') && memoryStore.phase === 'idle') loadMemory(false);
  if (view === 'memory' && sub === 'approvals' && memoryApprovalStore.phase === 'idle') loadMemoryApprovals();
  if (view === 'memory' && ['retrieval', 'injection'].includes(sub) && runtimeTraceStore.phase === 'idle') loadRuntimeTrace();
  if (view === 'skills' && sub !== 'routing' && skillsStore.phase === 'idle') loadSkills(false);
  if (view === 'skills' && sub === 'routing' && runtimeTraceStore.phase === 'idle') loadRuntimeTrace();
  if (view === 'connections' && connectionsStore.phase === 'idle') loadConnections();
  if (view === 'ops' && opsStore.phase === 'idle') loadOps();
  if (view === 'system' && systemStore.phase === 'idle') loadSystem();
  if (view === 'system' && dataHealthStore.phase === 'idle') loadDataHealth();
}

function handleRouteChange() {
  render();
  loadRouteData();
}

function renderLiveRefreshState(liveState) {
  const target = document.querySelector('#live-refresh-status');
  if (!target) return;
  target.className = `live-refresh-status ${liveState.phase}`;
  target.textContent = liveState.label;
  target.title = liveState.retryMs ? `下一次尝试约 ${Math.round(liveState.retryMs / 1000)} 秒后` : 'SSE invalidation · REST authority · read-only';
}

const resourceRefreshQueue = createResourceRefreshQueue({
  refreshResources: refreshChangedResources,
});

let realtimeRefreshController = null;
const liveRefreshController = createLiveRefreshController({
  fetchChanges: async ({ signal }) => {
    const response = await fetch('/api/v1/changes', {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
      signal,
    });
    if (!response.ok) throw new Error('change feed request failed');
    return response.json();
  },
  refreshResources: (resourceNames) => resourceRefreshQueue.enqueue(resourceNames),
  isVisible: () => document.visibilityState === 'visible',
  schedule: (callback, delay) => setTimeout(callback, delay),
  cancelSchedule: (timerId) => clearTimeout(timerId),
  createAbortController: () => new AbortController(),
  onState: (pollState) => realtimeRefreshController?.pollingStateChanged(pollState),
});

realtimeRefreshController = createRealtimeRefreshController({
  createEventSource: () => new EventSource('/api/v1/events'),
  pollingController: liveRefreshController,
  refreshQueue: resourceRefreshQueue,
  isVisible: () => document.visibilityState === 'visible',
  schedule: (callback, delay) => setTimeout(callback, delay),
  cancelSchedule: (timerId) => clearTimeout(timerId),
  onState: renderLiveRefreshState,
});

window.addEventListener('hashchange', handleRouteChange);
document.addEventListener('visibilitychange', realtimeRefreshController.visibilityChanged);
window.addEventListener('beforeunload', () => {
  stopActiveChatStreamReader();
  realtimeRefreshController.stop();
});
wireShell();
renderConversationDock();
render();
loadRouteData();
realtimeRefreshController.start();
loadPendingPermissions();
loadDashboardSnapshot().finally(async () => {
  if (sessionsStore.phase === 'idle') await loadSessions(false);
  await reconcileActiveTurnOnce();
});
setInterval(tickMeta, 1000);
