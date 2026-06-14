/* ============================================================
 * QQ Bot WebUI 前端逻辑（原生 JS，零依赖）
 * ============================================================ */

// ─────────────── 全局状态 ───────────────
const STATE = {
  ws: null,                // WebSocket 连接
  page: 'dashboard',       // 当前页面
  dashboard: null,         // 最近一次仪表盘数据
  traceGroups: [],         // 思维链分组（按消息聚合）
  traceGroupsById: new Map(), // seq -> group（快速查找）
  logs: [],                // 日志缓冲
  logsFiltered: [],        // 过滤后的日志（渲染用）
  logMaxBufferSize: 1000,
};

const TRACE_GROUP_TIMEOUT = 15000; // 15 秒无新事件则关闭分组
const MAX_TRACE_GROUPS = 30;

// ─────────────── 工具函数 ───────────────
function $(id) { return document.getElementById(id); }
function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}
function fmtUptime(sec) {
  if (!sec || sec < 0) return '—';
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d}天${h}小时`;
  if (h > 0) return `${h}小时${m}分`;
  return `${m}分`;
}
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}
function showToast(msg, type = '') {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'toast ' + type;
  setTimeout(() => t.classList.add('hidden'), 2500);
}

// ─────────────── 导航 ───────────────
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', (e) => {
    e.preventDefault();
    const page = item.dataset.page;
    switchPage(page);
  });
});
function switchPage(page) {
  STATE.page = page;
  document.querySelectorAll('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.page === page));
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  const target = $('page-' + page);
  if (target) target.classList.add('active');
  if (page === 'dashboard') refreshDashboard();
  // 各页懒加载
  if (page === 'groups' && typeof loadGroups === 'function') loadGroups();
  if (page === 'config' && typeof loadConfig === 'function') loadConfig();
  if (page === 'memory' && typeof loadMemGroups === 'function') loadMemGroups();
  if (page === 'skills' && typeof loadSkills === 'function') loadSkills();
  if (page === 'tools' && typeof loadTools === 'function') loadTools();
}

// ─────────────── 仪表盘 ───────────────
async function fetchJSON(url, opts) {
  try {
    const r = await fetch(url, opts);
    return await r.json();
  } catch (e) {
    console.error('fetch failed', url, e);
    return null;
  }
}
async function refreshDashboard() {
  const resp = await fetchJSON('/api/dashboard');
  if (!resp || !resp.ok) {
    showToast('仪表盘加载失败', 'error');
    return;
  }
  STATE.dashboard = resp.data;
  renderDashboard(resp.data);
}
function renderDashboard(d) {
  $('brand-bot-name').textContent = d.bot_name || 'Bot';
  $('d-bot-name').textContent = d.bot_name || '—';
  $('d-uptime').textContent = fmtUptime(d.uptime_seconds);
  const nc = d.napcat || {};
  $('d-napcat').innerHTML = nc.connected
    ? `<span class="badge badge-green">在线</span> ${nc.mode || ''}`
    : `<span class="badge badge-red">离线</span>`;
  $('d-groups').textContent = (d.activator && d.activator.active_groups) || 0;
  $('d-mem').textContent = (d.memory && d.memory.long_term_count) || 0;
  $('d-skills').textContent = (d.skills && d.skills.length) || 0;

  $('d-napcat-detail').textContent = JSON.stringify(d.napcat, null, 2);
  $('d-activator').textContent = JSON.stringify(d.activator, null, 2);

  const jobs = d.scheduler_jobs || [];
  $('d-jobs-count').textContent = jobs.length;
  const tbody = $('d-jobs').querySelector('tbody');
  tbody.innerHTML = '';
  if (jobs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="2" style="color:#8c959f;text-align:center;padding:14px;">暂无定时任务</td></tr>';
  } else {
    jobs.forEach(j => {
      const tr = el('tr');
      tr.appendChild(el('td', '', j.id));
      tr.appendChild(el('td', '', j.next_run || '—'));
      tbody.appendChild(tr);
    });
  }
}

// ─────────────── WebSocket 事件流 ───────────────
function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${proto}//${location.host}/api/ws`;
  STATE.ws = new WebSocket(url);

  STATE.ws.onopen = () => {
    setConnStatus(true);
    showToast('已连接', 'success');
  };
  STATE.ws.onclose = () => {
    setConnStatus(false);
    showToast('连接断开，5 秒后重连', 'error');
    setTimeout(connectWS, 5000);
  };
  STATE.ws.onerror = () => { /* onclose 会处理 */ };
  STATE.ws.onmessage = (evt) => {
    try {
      const msg = JSON.parse(evt.data);
      handleWSMessage(msg);
    } catch (e) {
      console.error('parse ws msg failed', e);
    }
  };
}
function setConnStatus(connected) {
  const box = $('conn-status');
  box.innerHTML = `<span class="dot ${connected ? 'dot-on' : 'dot-off'}"></span><span>${connected ? '已连接' : '未连接'}</span>`;
}

function handleWSMessage(msg) {
  if (msg.type === 'snapshot') {
    // 初始快照
    if (msg.data && msg.data.dashboard) {
      STATE.dashboard = msg.data.dashboard;
      renderDashboard(msg.data.dashboard);
    }
    if (msg.data && msg.data.recent_events) {
      msg.data.recent_events.forEach(replayEvent);
    }
    return;
  }
  // 普通事件
  $('event-counter').textContent = '事件 ' + (msg.seq || 0);

  // 日志事件单独走日志缓冲
  if (msg.type === 'log') {
    pushLog(msg.data);
    return;
  }
  // 其他事件进思维链
  pushTraceEvent(msg);
}

// ─────────────── 思维链 ───────────────
function getOrCreateTraceGroup(event) {
  // 用 group_id 把同一群的事件聚合到最近的"未关闭"分组
  const data = event.data || {};
  const gid = data.group_id;
  if (!gid) return null;

  // 找最近一个该群的、未关闭的分组
  for (let i = STATE.traceGroups.length - 1; i >= 0; i--) {
    const g = STATE.traceGroups[i];
    if (g.group_id === gid && !g.closed) return g;
  }

  // 新建分组（以 msg_received 或第一条带 group_id 的事件为起点）
  const g = {
    id: 'g' + Date.now() + Math.random().toString(36).slice(2, 6),
    group_id: gid,
    events: [],
    closed: false,
    startTs: event.ts,
    lastTs: event.ts,
  };
  STATE.traceGroups.push(g);
  if (STATE.traceGroups.length > MAX_TRACE_GROUPS) {
    STATE.traceGroups.shift();
  }
  return g;
}

function replayEvent(event) {
  pushTraceEvent(event, true);
}

function pushTraceEvent(event, silent = false) {
  const type = event.type;
  const data = event.data || {};

  // 这些事件触发"新分组"或归到现有分组
  if (type === 'msg_received') {
    // 强制新建一个分组
    const g = {
      id: 'g' + Date.now() + Math.random().toString(36).slice(2, 6),
      group_id: data.group_id,
      events: [event],
      closed: false,
      startTs: event.ts,
      lastTs: event.ts,
      title: `群 ${data.group_id} · ${data.user_id}`,
    };
    STATE.traceGroups.push(g);
    if (STATE.traceGroups.length > MAX_TRACE_GROUPS) STATE.traceGroups.shift();
    if (!silent) renderTrace();
    return;
  }

  // 其他事件追加到对应群分组
  if (!data.group_id) return;
  const g = getOrCreateTraceGroup(event);
  if (!g) return;
  g.events.push(event);
  g.lastTs = event.ts;

  // 收到 msg_sent 后关闭分组
  if (type === 'msg_sent') g.closed = true;
  // decision 不回复也关闭
  if (type === 'decision' && data.should_reply === false) g.closed = true;

  if (!silent) renderTrace();
}

function renderTrace() {
  if (STATE.page !== 'trace') return;
  const box = $('trace-list');
  box.innerHTML = '';
  if (STATE.traceGroups.length === 0) {
    box.innerHTML = '<div class="hint">等待群消息接入…收到消息后这里会实时展示处理流程。</div>';
    return;
  }
  // 倒序展示（最新的在最上面）
  for (let i = STATE.traceGroups.length - 1; i >= 0; i--) {
    const g = STATE.traceGroups[i];
    box.appendChild(renderTraceGroup(g));
  }
  if ($('trace-autoscroll').checked) {
    box.scrollTop = 0;
  }
}

const EVENT_LABELS = {
  msg_received: '收到消息',
  decision: '决策',
  llm_thinking_start: '开始思考',
  tool_call: '调用工具',
  tool_result: '工具结果',
  llm_reply: 'LLM 回复',
  msg_sent: '已发送',
  skill_trigger: '技能触发',
};

function renderTraceGroup(g) {
  const group = el('div', 'trace-group');
  const header = el('div', 'trace-group-header');
  header.appendChild(el('span', 'grp-title', g.title || `群 ${g.group_id}`));
  const right = el('div', 'grp-time', `${fmtTime(g.startTs)} · ${g.events.length} 事件`);
  header.appendChild(right);
  group.appendChild(header);

  const events = el('div', 'trace-events');
  g.events.forEach(ev => events.appendChild(renderTraceEvent(ev)));
  group.appendChild(events);
  return group;
}

function renderTraceEvent(ev) {
  const div = el('div', 'trace-event ev-' + ev.type);
  div.appendChild(el('div', 'ev-time', fmtTime(ev.ts)));
  const body = el('div', 'ev-body');
  const tag = el('span', 'ev-tag', EVENT_LABELS[ev.type] || ev.type);
  body.appendChild(tag);
  body.appendChild(document.createTextNode(formatEventData(ev)));
  div.appendChild(body);
  return div;
}

function formatEventData(ev) {
  const d = ev.data || {};
  switch (ev.type) {
    case 'msg_received':
      return d.raw_message ? `「${truncate(d.raw_message, 80)}」` : '';
    case 'decision':
      return `should_reply=${d.should_reply} 原因=${d.reason || '—'} 优先级=${d.priority || 0}`;
    case 'llm_thinking_start':
      return `上下文约 ${d.context_chars || 0} 字符，${d.tool_count || 0} 个工具可用`;
    case 'tool_call':
      return `${d.name}(${JSON.stringify(d.args || {})}) 第${d.round || 1}轮`;
    case 'tool_result':
      return `${d.name} → ${truncate(String(d.result_preview || ''), 200)} ${d.success ? '✓' : '✗'}`;
    case 'llm_reply':
      return `「${truncate(d.reply_preview || '', 120)}」 (${d.elapsed_ms || 0}ms)`;
    case 'msg_sent':
      return `${d.segment_count || 1} 段`;
    case 'skill_trigger':
      return `${d.skill_name}`;
    default:
      return JSON.stringify(d);
  }
}

function truncate(s, n) { return s.length > n ? s.slice(0, n) + '…' : s; }

function clearTrace() {
  STATE.traceGroups = [];
  renderTrace();
}

// ─────────────── 日志 ───────────────
function pushLog(data) {
  STATE.logs.push({
    ts: Date.now() / 1000,
    level: data.level,
    message: data.message,
    module: data.module,
  });
  if (STATE.logs.length > STATE.logMaxBufferSize) {
    STATE.logs = STATE.logs.slice(-STATE.logMaxBufferSize);
  }
  if (STATE.page === 'logs') renderLogs();
}

function getMinLevelNum() {
  const v = $('log-level-filter').value;
  const order = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40 };
  return order[v] || 0;
}

function renderLogs() {
  const box = $('log-list');
  const minLevel = getMinLevelNum();
  const order = { 'DEBUG': 10, 'INFO': 20, 'WARNING': 30, 'ERROR': 40 };
  const search = ($('log-search').value || '').toLowerCase();

  STATE.logsFiltered = STATE.logs.filter(l => {
    const lv = order[l.level] || 0;
    if (lv < minLevel) return false;
    if (search && !l.message.toLowerCase().includes(search)) return false;
    return true;
  });

  box.innerHTML = '';
  const show = STATE.logsFiltered.slice(-500);
  show.forEach(l => {
    const line = el('div', 'log-line');
    line.appendChild(el('span', 'log-time', fmtTime(l.ts)));
    line.appendChild(el('span', `log-level log-level-${l.level}`, l.level));
    line.appendChild(el('span', 'log-module', l.module || ''));
    line.appendChild(el('span', 'log-msg', l.message));
    box.appendChild(line);
  });
  if ($('log-autoscroll').checked) {
    box.scrollTop = box.scrollHeight;
  }
}

function clearLogs() {
  STATE.logs = [];
  renderLogs();
}

// ─────────────── 启动 ───────────────
window.addEventListener('DOMContentLoaded', () => {
  refreshDashboard();
  connectWS();
  // 定期刷新仪表盘（即使 WS 没事件也能更新运行时长等）
  setInterval(() => { if (STATE.page === 'dashboard') refreshDashboard(); }, 30000);
});

// ─────────────── 配置页 ───────────────
const STATUS_LABEL = {
  applied: { text: '热生效', cls: 'badge-green' },
  rebuild_required: { text: '需重建', cls: 'badge-orange' },
  restart_required: { text: '需重启', cls: 'badge-red' },
};

// 可编辑字段的分组定义（key 用 dotted path）
const CONFIG_GROUPS = [
  {
    title: 'Bot 基础',
    fields: [
      { key: 'bot.name', label: 'Bot 昵称', type: 'text' },
      { key: 'bot.master_id', label: '主人 QQ', type: 'text', help: '主人发言时 Bot 会更亲近' },
    ],
  },
  {
    title: 'LLM 大模型',
    fields: [
      { key: 'llm.provider', label: '提供商', type: 'text' },
      { key: 'llm.api_key', label: 'API Key', type: 'password', help: '保存后显示为 ****，但实际值已存储' },
      { key: 'llm.base_url', label: 'Base URL', type: 'text' },
      { key: 'llm.model', label: '模型名', type: 'text' },
      { key: 'llm.max_tokens', label: 'max_tokens', type: 'number' },
      { key: 'llm.temperature', label: 'temperature', type: 'number', step: '0.1' },
      { key: 'llm.context_window', label: '上下文窗口', type: 'number' },
      { key: 'llm.context_compression_threshold', label: '压缩阈值(字符)', type: 'number' },
    ],
  },
  {
    title: '主动活跃',
    fields: [
      { key: 'engagement.random_reply_frequency', label: '随机回复概率', type: 'number', step: '0.01', help: '0~1，越大越爱插话' },
      { key: 'engagement.bot_names', label: 'Bot 别名', type: 'list', help: '逗号分隔' },
      { key: 'engagement.name_match_mode', label: '匹配模式', type: 'select', options: ['contains', 'prefix'] },
    ],
  },
  {
    title: '冷却与限流',
    fields: [
      { key: 'cooldown.global_cooldown', label: '全局冷却(秒)', type: 'number' },
      { key: 'cooldown.group_cooldown', label: '每群冷却(秒)', type: 'number' },
      { key: 'cooldown.user_cooldown', label: '每人冷却(秒)', type: 'number' },
      { key: 'cooldown.rate_limit.window', label: '限流窗口(秒)', type: 'number', help: '改此项需重启才完全生效（结构变更）' },
      { key: 'cooldown.rate_limit.max_count', label: '窗口内最大回复', type: 'number' },
      { key: 'cooldown.rate_limit.strategy', label: '限流策略', type: 'select', options: ['discard', 'stall'] },
    ],
  },
  {
    title: '长期记忆',
    fields: [
      { key: 'memory.long_term.retrieval_k', label: '检索返回数', type: 'number' },
      { key: 'memory.long_term.min_save_length', label: '最小保存长度', type: 'number' },
    ],
  },
  {
    title: 'WebUI',
    fields: [
      { key: 'webui.enabled', label: '启用 WebUI', type: 'select', options: [true, false] },
      { key: 'webui.host', label: '监听地址', type: 'text', help: '改后需重启' },
      { key: 'webui.port', label: '端口', type: 'number', help: '改后需重启' },
    ],
  },
];

let CONFIG_META = {};  // key -> status

async function loadConfig() {
  const resp = await fetchJSON('/api/config');
  if (!resp || !resp.ok) { showToast('加载配置失败', 'error'); return; }
  CONFIG_META = {};
  (resp.data.meta || []).forEach(m => { CONFIG_META[m.key] = m.status; });
  renderConfigForms(resp.data.config);
}

function getStatusOfKey(key) {
  // 特例：rate_limit 的子字段归类到 applied（虽然结构变化需重启）
  if (key.startsWith('cooldown.rate_limit.')) return 'applied';
  return CONFIG_META[key] || 'restart_required';
}

function renderConfigForms(config) {
  const box = $('config-forms');
  box.innerHTML = '';
  CONFIG_GROUPS.forEach(group => {
    const g = el('div', 'config-group');
    g.appendChild(el('div', 'config-group-title', group.title));
    const fields = el('div', 'config-fields');
    group.fields.forEach(f => {
      fields.appendChild(renderConfigField(f, config));
    });
    g.appendChild(fields);
    box.appendChild(g);
  });
}

function renderConfigField(f, config) {
  const row = el('div', 'config-field');
  // label
  const lab = el('div', 'cf-label');
  lab.appendChild(document.createTextNode(f.label));
  const help = el('div', 'cf-help', f.key);
  lab.appendChild(help);
  row.appendChild(lab);

  // input
  const val = getConfigValue(config, f.key);
  const inputWrap = el('div', 'cf-input');
  let input;
  if (f.type === 'select') {
    input = el('select', 'input');
    f.options.forEach(opt => {
      const o = el('option');
      o.value = opt; o.textContent = String(opt);
      if (String(opt) === String(val)) o.selected = true;
      input.appendChild(o);
    });
    input.dataset.origType = typeof f.options[0];
  } else if (f.type === 'list') {
    input = el('input', 'input');
    input.type = 'text';
    input.value = Array.isArray(val) ? val.join(', ') : String(val || '');
  } else if (f.type === 'password') {
    input = el('input', 'input');
    input.type = 'text';  // 用 text 让用户能看到脱敏值
    input.value = val || '';
    input.placeholder = '（脱敏显示，留空不改）';
  } else {
    input = el('input', 'input');
    input.type = f.type;
    input.value = val ?? '';
    if (f.step) input.step = f.step;
  }
  input.dataset.key = f.key;
  input.dataset.fieldType = f.type;
  if (f.help && f.type !== 'password') {
    // help 已在 label 区
  }
  // 回车保存
  input.addEventListener('change', () => saveField(f, input));
  inputWrap.appendChild(input);
  row.appendChild(inputWrap);

  // status badge
  const statusKey = getStatusOfKey(f.key);
  const sl = STATUS_LABEL[statusKey] || STATUS_LABEL.restart_required;
  const status = el('div', 'cf-status');
  status.appendChild(el('span', `badge ${sl.cls}`, sl.text));
  row.appendChild(status);

  return row;
}

function getConfigValue(config, dotted) {
  const parts = dotted.split('.');
  let cur = config;
  for (const p of parts) {
    if (cur == null) return undefined;
    cur = cur[p];
  }
  return cur;
}

async function saveField(f, input) {
  let value;
  if (f.type === 'list') {
    value = input.value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  } else if (f.type === 'select') {
    value = input.dataset.origType === 'boolean' ? (input.value === 'true') : input.value;
  } else if (f.type === 'number') {
    value = input.value === '' ? 0 : Number(input.value);
  } else {
    value = input.value;
  }
  // password 字段：如果值是脱敏的 ****，跳过不保存
  if (f.type === 'password' && /\*/.test(input.value)) {
    showToast('API Key 看起来是脱敏值，未保存', '');
    return;
  }
  const resp = await fetchJSON('/api/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key: f.key, value }),
  });
  if (!resp || !resp.ok) {
    showToast(`保存失败: ${(resp && resp.message) || '未知错误'}`, 'error');
    return;
  }
  const sl = STATUS_LABEL[resp.status] || STATUS_LABEL.restart_required;
  showToast(`${f.label}: ${sl.text}`, resp.status === 'applied' ? 'success' : '');
}

async function rebuildLLM() {
  if (!confirm('确认根据当前 config 重建 LLM 客户端？重建期间 LLM 调用会短暂不可用。')) return;
  const resp = await fetchJSON('/api/config/reload-llm', { method: 'POST' });
  if (resp && resp.ok) {
    showToast('LLM 客户端已重建', 'success');
  } else {
    showToast(`重建失败: ${(resp && resp.message) || '未知错误'}`, 'error');
  }
}

// ============================================================
// 记忆管理页
// ============================================================
let MEM_TAB = 'short';

function switchMemTab(tab) {
  MEM_TAB = tab;
  document.querySelectorAll('[data-mtab]').forEach(b => b.classList.toggle('active', b.dataset.mtab === tab));
  $('mem-short').classList.toggle('hidden', tab !== 'short');
  $('mem-long').classList.toggle('hidden', tab !== 'long');
  if (tab === 'long') {
    loadLongTermStats();
    if (!$('mem-long-list').innerHTML) loadLongTermRecent();
  }
}

async function loadMemGroups() {
  const resp = await fetchJSON('/api/memory/groups');
  if (!resp || !resp.ok) { showToast('加载失败', 'error'); return; }
  const box = $('mem-groups');
  box.innerHTML = '';
  if (!resp.data || resp.data.length === 0) {
    box.innerHTML = '<div class="hint">暂无短期记忆（Bot 启动后开始记录）</div>';
    return;
  }
  resp.data.forEach(g => {
    const card = el('div', 'card');
    card.style.cursor = 'pointer';
    card.innerHTML = `<div class="card-label">群 ${g.group_id}</div>` +
                     `<div class="card-value">${g.turn_count}</div>` +
                     `<div class="card-label">${g.last_time ? fmtTime(g.last_time) : '—'}</div>`;
    card.addEventListener('click', () => loadMemTurns(g.group_id));
    box.appendChild(card);
  });
}

async function loadMemTurns(groupId) {
  const resp = await fetchJSON(`/api/memory/short-term?group_id=${groupId}&limit=50`);
  if (!resp || !resp.ok) { showToast('加载对话失败', 'error'); return; }
  const box = $('mem-turns');
  box.className = '';
  box.innerHTML = '';
  const toolbar = el('div', 'inline-controls');
  toolbar.style.marginBottom = '10px';
  toolbar.appendChild(el('span', '', `群 ${groupId} · ${resp.data.length} 轮对话`));
  const delBtn = el('button', 'btn btn-danger', '清空该群短期记忆');
  delBtn.addEventListener('click', () => clearMemGroup(groupId));
  toolbar.appendChild(delBtn);
  box.appendChild(toolbar);

  if (resp.data.length === 0) {
    box.appendChild(el('div', 'hint', '该群暂无对话记录'));
    return;
  }
  const list = el('div');
  list.style.border = '1px solid var(--border)';
  list.style.borderRadius = '6px';
  list.style.overflow = 'hidden';
  resp.data.forEach(t => {
    const row = el('div', `turn t-${t.role}`);
    row.appendChild(el('div', 't-time', t.time ? fmtTime(t.time) : ''));
    row.appendChild(el('div', 't-role', t.role));
    row.appendChild(el('div', 't-content', t.content || ''));
    list.appendChild(row);
  });
  box.appendChild(list);
}

async function clearMemGroup(groupId) {
  if (!confirm(`确认清空群 ${groupId} 的全部短期记忆？此操作不可撤销。`)) return;
  const resp = await fetchJSON(`/api/memory/short-term/${groupId}`, { method: 'DELETE' });
  if (resp && resp.ok) {
    showToast('已清空', 'success');
    loadMemGroups();
    $('mem-turns').innerHTML = '<div class="hint">选择上面的群查看对话</div>';
    $('mem-turns').className = 'hint';
  } else {
    showToast('清空失败', 'error');
  }
}

async function loadLongTermStats() {
  const resp = await fetchJSON('/api/memory/long-term/stats');
  if (!resp || !resp.ok) return;
  const cnt = resp.data && resp.data.count ? resp.data.count : 0;
  $('mem-long-count').textContent = `${cnt} 条` + (resp.data && resp.data.enabled === false ? '（未启用）' : '');
}

async function searchLongTerm() {
  const q = $('mem-search').value.trim();
  if (!q) { loadLongTermRecent(); return; }
  await loadLongTermList(`q=${encodeURIComponent(q)}`);
}

async function loadLongTermRecent() {
  $('mem-search').value = '';
  await loadLongTermList('limit=30');
}

async function loadLongTermList(query) {
  const resp = await fetchJSON(`/api/memory/long-term?${query}`);
  if (!resp || !resp.ok) { showToast('加载失败', 'error'); return; }
  const box = $('mem-long-list');
  box.innerHTML = '';
  if (!resp.data || resp.data.length === 0) {
    box.innerHTML = '<div class="hint">没有匹配的记忆</div>';
    return;
  }
  resp.data.forEach(m => {
    const item = el('div', 'mem-item');
    const meta = el('div', 'mi-meta');
    meta.appendChild(el('span', '', m.timestamp ? fmtTime(m.timestamp) : ''));
    if (m.group_id) meta.appendChild(el('span', '', `群 ${m.group_id}`));
    if (m.user_id) meta.appendChild(el('span', '', `用户 ${m.user_id}`));
    item.appendChild(meta);
    item.appendChild(el('div', 'mi-content', m.content || ''));
    const delBtn = el('button', 'btn btn-danger');
    delBtn.style.marginTop = '8px';
    delBtn.textContent = '删除';
    delBtn.addEventListener('click', () => deleteLongTerm(m.id));
    item.appendChild(delBtn);
    box.appendChild(item);
  });
}

async function deleteLongTerm(id) {
  if (!confirm('确认删除这条长期记忆？')) return;
  const resp = await fetchJSON(`/api/memory/long-term/${id}`, { method: 'DELETE' });
  if (resp && resp.ok) {
    showToast('已删除', 'success');
    loadLongTermStats();
    // 重新执行当前查询
    const q = $('mem-search').value.trim();
    if (q) searchLongTerm(); else loadLongTermRecent();
  } else {
    showToast('删除失败', 'error');
  }
}

// ============================================================
// 技能管理页
// ============================================================
async function loadSkills() {
  const resp = await fetchJSON('/api/skills');
  if (!resp || !resp.ok) { showToast('加载失败', 'error'); return; }
  const box = $('skills-list');
  box.innerHTML = '';
  if (!resp.data || resp.data.length === 0) {
    box.innerHTML = '<div class="hint">未加载任何技能</div>';
    return;
  }
  resp.data.forEach(s => box.appendChild(renderSkillCard(s)));
}

function renderSkillCard(s) {
  const card = el('div', 'skill-card');
  // head
  const head = el('div', 'sc-head');
  head.appendChild(el('span', 'sc-name', s.name));
  const toggleWrap = el('label', 'toggle');
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = s.enabled;
  cb.addEventListener('change', () => toggleSkill(s.name, cb.checked));
  toggleWrap.appendChild(cb);
  toggleWrap.appendChild(el('span', 'toggle-slider'));
  head.appendChild(toggleWrap);
  card.appendChild(head);
  // desc
  card.appendChild(el('div', 'sc-desc', s.desc || '（无描述）'));
  // triggers 展示
  const chips = el('div', 'sc-triggers');
  (s.triggers || []).forEach(t => chips.appendChild(el('span', 'trigger-chip', t)));
  if (s.customized) chips.appendChild(el('span', 'badge badge-orange', '已自定义'));
  card.appendChild(chips);
  // 编辑区
  const edit = el('div', 'sc-edit');
  const input = el('input', 'input');
  input.type = 'text';
  input.placeholder = '新触发词，逗号分隔（留空=用默认）';
  input.value = (s.triggers || []).join(', ');
  edit.appendChild(input);
  const saveBtn = el('button', 'btn btn-primary', '保存');
  saveBtn.addEventListener('click', () => saveSkillTriggers(s.name, input.value));
  edit.appendChild(saveBtn);
  const resetBtn = el('button', 'btn btn-ghost', '恢复默认');
  resetBtn.addEventListener('click', () => saveSkillTriggers(s.name, ''));
  edit.appendChild(resetBtn);
  card.appendChild(edit);
  return card;
}

async function saveSkillTriggers(name, value) {
  const triggers = value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  const resp = await fetchJSON(`/api/skills/${name}/triggers`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ triggers }),
  });
  if (resp && resp.ok) {
    showToast(triggers.length ? '触发词已更新' : '已恢复默认', 'success');
    loadSkills();
  } else {
    showToast(`保存失败: ${(resp && resp.message) || ''}`, 'error');
  }
}

async function toggleSkill(name, enabled) {
  const resp = await fetchJSON(`/api/skills/${name}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (resp && resp.ok) {
    showToast(enabled ? '已启用' : '已禁用', 'success');
  } else {
    showToast('操作失败', 'error');
    loadSkills();
  }
}

// ============================================================
// 工具管理页
// ============================================================
async function loadTools() {
  const resp = await fetchJSON('/api/tools');
  if (!resp || !resp.ok) { showToast('加载失败', 'error'); return; }
  const box = $('tools-list');
  box.innerHTML = '';
  if (!resp.data || resp.data.length === 0) {
    box.innerHTML = '<div class="hint">没有已注册的工具</div>';
    return;
  }
  resp.data.forEach(t => box.appendChild(renderToolCard(t)));
}

function renderToolCard(t) {
  const card = el('div', 'tool-card');
  const head = el('div', 'tc-head');
  head.appendChild(el('span', 'tc-name', t.name));
  const toggleWrap = el('label', 'toggle');
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = t.enabled;
  cb.addEventListener('change', () => toggleTool(t.name, cb.checked));
  toggleWrap.appendChild(cb);
  toggleWrap.appendChild(el('span', 'toggle-slider'));
  head.appendChild(toggleWrap);
  card.appendChild(head);
  card.appendChild(el('div', 'tc-desc', t.description || ''));

  // schema 折叠
  const details = el('details');
  details.style.marginTop = '8px';
  const summary = el('summary', '');
  summary.textContent = '参数 schema';
  summary.style.cursor = 'pointer';
  summary.style.fontSize = '12px';
  summary.style.color = 'var(--primary)';
  details.appendChild(summary);
  const pre = el('div', 'tool-schema');
  pre.textContent = JSON.stringify(t.parameters, null, 2);
  details.appendChild(pre);
  card.appendChild(details);
  return card;
}

async function toggleTool(name, enabled) {
  const resp = await fetchJSON(`/api/tools/${name}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (resp && resp.ok) {
    showToast(enabled ? '已启用' : '已禁用', 'success');
  } else {
    showToast('操作失败', 'error');
    loadTools();
  }
}

// ============================================================
// 群聊管理页
// ============================================================
async function loadGroups() {
  const resp = await fetchJSON('/api/groups');
  const box = $('groups-list');
  const statusBar = $('groups-status');
  const summary = $('groups-summary');

  if (!resp || !resp.ok) {
    box.innerHTML = '<div class="hint">加载失败，请检查 NapCat 是否已连接</div>';
    showToast('加载群列表失败', 'error');
    return;
  }

  // 未连接提示
  if (resp.connected === false) {
    statusBar.style.display = 'block';
    statusBar.textContent = '⚠️ NapCat 未连接，无法获取群列表。请先在 NapCat 窗口完成 QQ 登录。';
    box.innerHTML = '<div class="hint">等待 NapCat 连接…</div>';
    summary.textContent = '未连接';
    return;
  }
  statusBar.style.display = 'none';

  const groups = resp.data || [];
  const disabledCount = resp.disabled_count || 0;
  const enabledCount = groups.length - groups.filter(g => !g.enabled).length;
  summary.textContent = `${enabledCount}/${groups.length} 个群启用` + (disabledCount ? ` · ${disabledCount} 个禁用` : '');

  if (groups.length === 0) {
    box.innerHTML = '<div class="hint">Bot 未加入任何群，或 NapCat 未返回群列表。</div>';
    return;
  }

  box.innerHTML = '';
  groups.forEach(g => box.appendChild(renderGroupCard(g)));
}

function renderGroupCard(g) {
  const card = el('div', 'group-card' + (g.enabled ? '' : ' group-card-disabled'));
  // 头部：群名 + toggle
  const head = el('div', 'gc-head');
  const titleWrap = el('div', 'gc-title-wrap');
  titleWrap.appendChild(el('div', 'gc-name', g.group_name || ('群 ' + g.group_id)));
  titleWrap.appendChild(el('div', 'gc-id', String(g.group_id)));
  head.appendChild(titleWrap);

  const toggleWrap = el('label', 'toggle');
  const cb = el('input'); cb.type = 'checkbox'; cb.checked = g.enabled;
  cb.addEventListener('change', () => toggleGroup(g.group_id, cb.checked, g.group_name));
  toggleWrap.appendChild(cb);
  toggleWrap.appendChild(el('span', 'toggle-slider'));
  head.appendChild(toggleWrap);
  card.appendChild(head);

  // 状态标签 + 成员数
  const meta = el('div', 'gc-meta');
  if (g.enabled) {
    meta.appendChild(el('span', 'badge badge-green', '已启用'));
  } else {
    meta.appendChild(el('span', 'badge badge-red', '已禁用'));
  }
  meta.appendChild(el('span', 'gc-members', `${g.member_count} 成员`));
  card.appendChild(meta);

  return card;
}

async function toggleGroup(groupId, enabled, groupName) {
  const resp = await fetchJSON(`/api/groups/${groupId}/toggle`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled }),
  });
  if (resp && resp.ok) {
    const name = groupName || ('群 ' + groupId);
    showToast(`${name} 已${enabled ? '启用' : '禁用'}`, 'success');
    loadGroups();  // 刷新统计
  } else {
    showToast('操作失败', 'error');
    loadGroups();
  }
}
