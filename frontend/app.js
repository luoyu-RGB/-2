/* 个人理财助手 — 前端逻辑（原生 JS，无构建步骤）
 *
 * 与后端的分工：
 *   · 所有金额、月数、结论都来自后端接口，前端只负责展示，不做任何财务计算；
 *   · 写操作（记账/转账/预算/资产负债）成功后重新拉取受影响的数据；
 *   · AI 写操作走「预览 → 确认/取消 → 审计」三步，前端只负责把 pending_action 呈现出来。
 */
'use strict';

const API = '/api';

/* ------------------------------------------------------------------ */
/* 小工具                                                              */
/* ------------------------------------------------------------------ */
const $ = (id) => document.getElementById(id);
const money = (value) => {
  const n = Number(value || 0);
  return (n < 0 ? '-' : '') + '¥' + Math.abs(n).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const isoToday = () => new Date().toLocaleDateString('sv-SE');
const thisMonth = () => isoToday().slice(0, 7);
const monthOffset = (offset) => {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - offset);
  return d.toLocaleDateString('sv-SE').slice(0, 7);
};

let toastTimer = null;
function toast(message, kind = 'ok') {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast show${kind === 'error' ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.className = 'toast'; }, 3200);
}

async function api(path, options = {}) {
  const { method = 'GET', body } = options;
  const init = { method };
  if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
    init.body = JSON.stringify(body);
  }
  const response = await fetch(API + path, init);
  const raw = await response.text();
  let data = null;
  if (raw) { try { data = JSON.parse(raw); } catch { data = raw; } }
  if (!response.ok) {
    let detail = (data && (data.error || data.detail)) || response.statusText;
    if (Array.isArray(detail)) detail = detail.map((item) => item.msg || JSON.stringify(item)).join('；');
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

/* ------------------------------------------------------------------ */
/* 全局状态                                                            */
/* ------------------------------------------------------------------ */
const state = {
  view: 'overview',
  accounts: [],
  expenseCategories: [],
  incomeCategories: [],
  txnOffset: 0,
  txnLimit: 20,
  chatReady: false,
  chatBusy: false,
  pendingLogId: null,
};

const PAGE_META = {
  overview: { eyebrow: 'YOUR MONEY, CLEARER', title: '财务概览', subtitle: '本月收支、账户余额与净资产一览' },
  records: { eyebrow: 'LEDGER', title: '收支明细', subtitle: '按月份、类型、账户与关键词筛选交易' },
  budget: { eyebrow: 'BUDGET', title: '预算规划', subtitle: '预算执行情况来自数据库视图 v_budget_status' },
  assets: { eyebrow: 'BALANCE SHEET', title: '资产负债', subtitle: '净资产 = 资产 − 负债 + 账户余额' },
  assistant: { eyebrow: 'AI ADVISOR', title: 'AI 顾问', subtitle: '对话式记账、可负担性判断与资料问答' },
};

/* ------------------------------------------------------------------ */
/* 基础数据                                                            */
/* ------------------------------------------------------------------ */
async function loadHealth() {
  const health = await api('/api/health');
  const config = health.config || {};
  const database = health.database || {};
  $('dbMode').textContent = database.mode === 'postgres' ? 'GaussDB / PostgreSQL' : 'SQLite 离线库';
  $('dbNote').textContent = database.schema_ok
    ? `${(database.tables || []).length} 张表 · ${(database.views || []).length} 个视图 · ${(database.triggers || []).length} 个触发器`
    : '结构不完整，请检查 docs/sql';
  state.chatReady = !!config.chat_ready;
  $('aiMode').textContent = state.chatReady ? `模型：${config.chat_model}` : '规则模式（未配置 Key）';
  return health;
}

async function loadAccounts() {
  state.accounts = await api('/api/accounts');
  const options = state.accounts.map((a) => `<option value="${a.account_id}">${esc(a.account_name)}（${esc(a.account_type)}）</option>`).join('');
  ['fAccount', 'txnAccount', 'trFrom', 'trTo'].forEach((id) => {
    const select = $(id);
    if (!select) return;
    const keepFirst = id === 'fAccount' ? '<option value="">全部</option>' : '';
    select.innerHTML = keepFirst + options;
  });
  if ($('trTo') && state.accounts.length > 1) $('trTo').value = state.accounts[1].account_id;
}

async function loadCategories() {
  const [expense, income] = await Promise.all([
    api('/api/categories?type=' + encodeURIComponent('支出')),
    api('/api/categories?type=' + encodeURIComponent('收入')),
  ]);
  state.expenseCategories = expense;
  state.incomeCategories = income;
  $('budgetCategory').innerHTML = expense.map((c) => `<option value="${c.category_id}">${esc(c.category_name)}</option>`).join('');
  renderTxnCategoryOptions();
}

function renderTxnCategoryOptions() {
  const type = $('txnType').value;
  const list = type === '收入' ? state.incomeCategories : state.expenseCategories;
  $('txnCategory').innerHTML = list
    .map((c) => `<option value="${c.category_id}">${esc(c.parent_name ? c.parent_name + ' / ' : '')}${esc(c.category_name)}</option>`)
    .join('');
}

/* ------------------------------------------------------------------ */
/* 视图路由                                                            */
/* ------------------------------------------------------------------ */
function switchView(name) {
  state.view = name;
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === name));
  document.querySelectorAll('.view').forEach((section) => section.classList.toggle('active', section.id === `view-${name}`));
  const meta = PAGE_META[name] || PAGE_META.overview;
  $('pageEyebrow').textContent = meta.eyebrow;
  $('pageTitle').textContent = meta.title;
  $('pageSubtitle').textContent = meta.subtitle;
  return render(name);
}

function render(name) {
  if (name === 'overview') return renderOverview();
  if (name === 'records') return renderRecords();
  if (name === 'budget') return renderBudget();
  if (name === 'assets') return renderAssets();
  if (name === 'assistant') return renderAssistant();
  return Promise.resolve();
}

/* ------------------------------------------------------------------ */
/* 概览                                                                */
/* ------------------------------------------------------------------ */
async function renderOverview() {
  const month = thisMonth();
  const [overview, worth, ratio, trend, transactions, budgetStatus] = await Promise.all([
    api('/api/overview'),
    api('/api/net-worth'),
    api(`/api/category-ratio?month=${month}`),
    api('/api/monthly-trend'),
    api('/api/transactions?limit=8'),
    api(`/api/budget-status?month=${month}`),
  ]);

  const net = Number(overview.month_income) - Number(overview.month_expense);
  $('net').textContent = money(net);
  $('income').textContent = money(overview.month_income);
  $('expense').textContent = money(overview.month_expense);
  $('balance').textContent = money(worth.account_balance);
  $('netWorth').textContent = money(worth.net_worth);

  $('accounts').innerHTML = state.accounts.length
    ? state.accounts.map((a) => `<div class="account"><span class="dot"></span>${esc(a.account_name)}<small>${esc(a.account_type)}</small><b>${money(a.balance)}</b></div>`).join('')
    : '<p class="hint">还没有账户，点右上角「新增账户」开始。</p>';

  renderInsights(overview, ratio, budgetStatus, net);
  renderBars($('ratio'), ratio.map((row) => ({
    label: row.category_name,
    value: Number(row.expense_amount),
    text: `${money(row.expense_amount)} · ${pct(row.expense_ratio)}`,
    ratio: Number(row.expense_ratio),
  })), '本月还没有支出记录');
  $('ratioMonth').textContent = month;

  renderTrend(trend);
  renderTransactionList($('recent'), transactions, false);
}

function renderInsights(overview, ratio, budgetStatus, net) {
  const items = [];
  const overspent = budgetStatus.filter((row) => String(row.status).startsWith('超支'));
  const nearLimit = budgetStatus.filter((row) => String(row.status).startsWith('接近预算'));
  if (overspent.length) {
    items.push(`<p><b>超支提醒</b>：${overspent.map((r) => esc(r.category_name)).join('、')} 已超出预算，建议本月暂停该类可选消费。</p>`);
  } else if (nearLimit.length) {
    items.push(`<p><b>接近预算</b>：${nearLimit.map((r) => esc(r.category_name)).join('、')} 已用掉 90% 以上额度。</p>`);
  }
  if (ratio.length) {
    const top = ratio[0];
    items.push(`<p><b>最大支出类别</b>：${esc(top.category_name)}，${money(top.expense_amount)}（占 ${pct(top.expense_ratio)}）。</p>`);
  }
  if (net > 0) {
    items.push(`<p><b>本月结余 ${money(net)}</b>：可优先补足应急金。经验口径是 3–6 个月的必要支出。</p>`);
  } else if (net < 0) {
    items.push(`<p><b>本月支出超过收入 ${money(-net)}</b>：建议先压缩可选支出，再考虑新增消费或分期。</p>`);
  }
  if (!items.length) items.push('<p>本月数据还不多，先记几笔账，我就能给出更有针对性的提醒。</p>');
  $('insights').innerHTML = items.join('');
}

function renderBars(container, rows, emptyText = '暂无数据') {
  if (!rows.length) { container.innerHTML = `<p class="hint">${emptyText}</p>`; return; }
  const max = Math.max(...rows.map((row) => row.ratio || 0), 1);
  container.innerHTML = rows.map((row) => {
    const width = Math.max(2, Math.min(100, ((row.ratio || 0) / max) * 100));
    const cls = row.ratio > 100 ? 'bar-fill over' : row.ratio >= 90 ? 'bar-fill warn' : 'bar-fill';
    return `<div class="bar-row">
      <div class="bar-top"><span>${esc(row.label)}</span><span>${esc(row.text)}</span></div>
      <div class="bar-track"><div class="${cls}" style="width:${width}%"></div></div>
    </div>`;
  }).join('');
}

function renderTrend(rows) {
  const container = $('trend');
  if (!rows.length) { container.innerHTML = '<p class="hint">暂无月度数据</p>'; return; }
  const max = Math.max(...rows.flatMap((row) => [Number(row.income), Number(row.expense)]), 1);
  container.innerHTML = rows.slice(-6).map((row) => {
    const incomeHeight = Math.max(2, (Number(row.income) / max) * 100);
    const expenseHeight = Math.max(2, (Number(row.expense) / max) * 100);
    return `<div class="trend-col" title="${esc(row.ym)}：收入 ${money(row.income)}，支出 ${money(row.expense)}">
      <div class="trend-bars"><i style="height:${incomeHeight}%"></i><i class="exp" style="height:${expenseHeight}%"></i></div>
      <small>${esc(String(row.ym).slice(2))}</small>
    </div>`;
  }).join('');
}

function renderTransactionList(container, rows, withActions) {
  if (!rows.length) { container.innerHTML = '<p class="hint">没有符合条件的交易</p>'; return; }
  container.innerHTML = rows.map((row) => {
    const isIncome = row.trans_type === '收入';
    const tag = row.is_transfer ? '<span class="tag">转账</span>' : '';
    const actions = withActions
      ? `<td class="right"><button class="text-btn" data-edit-txn="${row.transaction_id}">编辑</button><button class="danger" data-del-txn="${row.transaction_id}">删除</button></td>`
      : '';
    return `<div class="transaction">
      <div><b>${esc(row.category_name)}</b>${tag}<small>${esc(row.account_name)} · ${esc(row.trans_date)}${row.remark ? ' · ' + esc(row.remark) : ''}</small></div>
      <div class="amount ${isIncome ? 'income' : 'expense'}">${isIncome ? '+' : '−'}${money(row.amount)}</div>
    </div>`;
  }).join('');

  if (withActions) {
    // 明细视图用表格，这里不再复用列表结构
  }
}

/* ------------------------------------------------------------------ */
/* 收支明细                                                            */
/* ------------------------------------------------------------------ */
async function renderRecords() {
  if (!$('fMonth').value) $('fMonth').value = thisMonth();
  const params = new URLSearchParams();
  if ($('fMonth').value) params.set('month', $('fMonth').value);
  if ($('fType').value) params.set('trans_type', $('fType').value);
  if ($('fAccount').value) params.set('account_id', $('fAccount').value);
  if ($('fKeyword').value.trim()) params.set('keyword', $('fKeyword').value.trim());
  params.set('limit', state.txnLimit);
  params.set('offset', state.txnOffset);

  const rows = await api('/api/transactions?' + params.toString());
  const body = $('txnBody');
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="hint">没有符合条件的交易</td></tr>';
  } else {
    body.innerHTML = rows.map((row) => {
      const isIncome = row.trans_type === '收入';
      const tag = row.is_transfer ? '<span class="tag">转账</span>' : '';
      return `<tr>
        <td>${esc(row.trans_date)}</td>
        <td>${esc(row.parent_category_name ? row.parent_category_name + ' / ' : '')}${esc(row.category_name)}${tag}</td>
        <td>${esc(row.account_name)}</td>
        <td>${esc(row.remark || '')}</td>
        <td class="right amount ${isIncome ? 'income' : 'expense'}">${isIncome ? '+' : '−'}${money(row.amount)}</td>
        <td class="right">
          <button class="text-btn" data-edit-txn="${row.transaction_id}">编辑</button>
          <button class="danger" data-del-txn="${row.transaction_id}">删除</button>
        </td>
      </tr>`;
    }).join('');
  }
  $('txnCount').textContent = `第 ${state.txnOffset + 1}–${state.txnOffset + rows.length} 条`;
  $('prevPage').disabled = state.txnOffset === 0;
  $('nextPage').disabled = rows.length < state.txnLimit;
  state.txnCache = rows;
}

/* ------------------------------------------------------------------ */
/* 预算规划                                                            */
/* ------------------------------------------------------------------ */
async function renderBudget() {
  const month = thisMonth();
  if (!$('budgetFormMonth').value) $('budgetFormMonth').value = month;
  const rows = await api(`/api/budget-status?month=${month}`);
  $('budgetMonth').textContent = month;
  renderBars($('budgetStatus'), rows.map((row) => ({
    label: `${row.category_name}（${row.status}）`,
    value: Number(row.actual_expense),
    text: `${money(row.actual_expense)} / ${money(row.budget_amount)} · ${pct(row.completion_rate)}`,
    ratio: Number(row.completion_rate),
  })), '本月还没有设置预算，右侧可以添加。');
}

/* ------------------------------------------------------------------ */
/* 资产负债                                                            */
/* ------------------------------------------------------------------ */
async function renderAssets() {
  const [worth, items] = await Promise.all([api('/api/net-worth'), api('/api/asset-liability')]);
  $('worthValue').textContent = money(worth.net_worth);
  $('worthAssets').textContent = money(worth.assets);
  $('worthLiabilities').textContent = money(worth.liabilities);
  $('worthBalance').textContent = money(worth.account_balance);

  $('assetBody').innerHTML = items.length
    ? items.map((item) => `<tr>
        <td>${esc(item.item_name)}</td>
        <td>${esc(item.item_type)}</td>
        <td>${esc(item.acquire_date || '')}</td>
        <td>${esc(item.remark || '')}</td>
        <td class="right amount ${item.item_type === '资产' ? 'income' : 'expense'}">${money(item.amount)}</td>
        <td class="right">
          <button class="text-btn" data-edit-asset="${item.item_id}">编辑</button>
          <button class="danger" data-del-asset="${item.item_id}">删除</button>
        </td>
      </tr>`).join('')
    : '<tr><td colspan="6" class="hint">还没有资产或负债记录</td></tr>';
  state.assetCache = items;
}

/* ------------------------------------------------------------------ */
/* AI 顾问                                                             */
/* ------------------------------------------------------------------ */
const QUICK_PROMPTS = [
  '今天午餐花了 32 元，用支付宝',
  '收到工资 15000 元',
  '我想买一台 8500 元的笔记本，现在能买吗',
  '这个月的情况怎么样',
  '帮我看看预算执行情况',
];

function pushBubble(role, text, extra = '') {
  const log = $('chatLog');
  const node = document.createElement('div');
  node.className = `bubble ${role === 'user' ? 'user' : 'bot'}${extra.includes('pending') ? ' pending' : ''}`;
  node.innerHTML = esc(text) + extra;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
  return node;
}

async function renderAssistant() {
  if (!$('chatLog').dataset.ready) {
    pushBubble('bot', '你好，我是你的理财顾问。可以直接说一笔消费让我记账，或者问我某笔开销是否负担得起；上传理财资料后我还能帮你查条款。');
    $('chatLog').dataset.ready = '1';
  }
  $('quickPrompts').innerHTML = QUICK_PROMPTS.map((p) => `<button data-prompt="${esc(p)}">${esc(p)}</button>`).join('');
  await Promise.all([renderAudit(), renderKnowledge()]);
}

async function renderAudit() {
  const rows = await api('/api/ai/actions?limit=12');
  $('auditLog').innerHTML = rows.length
    ? rows.map((row) => `<div class="audit-item">
        <div>${esc(row.action)}<br><small class="hint">${esc(row.user_input || row.message || '')}</small></div>
        <div class="status-${esc(row.status)}">${esc(row.status)}</div>
      </div>`).join('')
    : '<p class="hint">还没有 AI 操作记录</p>';
}

async function renderKnowledge() {
  const rows = await api('/api/knowledge');
  $('knowledgeList').innerHTML = rows.length
    ? rows.map((row) => `<div class="kb-item">
        <div>${esc(row.title)}<br><small>${row.chunk_count} 个片段 · ${esc(String(row.create_time).slice(0, 10))}</small></div>
        <button class="danger" data-del-kb="${row.document_id}">删除</button>
      </div>`).join('')
    : '<p class="hint">还没有资料，粘贴一段条款或理财笔记试试。</p>';
}

function pendingCard(pending, affordability, sources) {
  const lines = [];
  if (affordability) {
    lines.push(`<div class="kv">
      <span>到手总成本</span><b>${money(affordability.total_cost)}</b>
      <span>可动用现金</span><b>${money(affordability.liquid_balance)}</b>
      <span>月必要支出</span><b>${money(affordability.monthly_necessary_expense)}</b>
      <span>月结余</span><b>${money(affordability.monthly_surplus)}</b>
      <span>购买后应急金</span><b>${Number(affordability.emergency_months_after).toFixed(2)} 个月</b>
      <span>系统结论</span><b>${esc(affordability.verdict)}</b>
    </div>`);
  }
  if (sources && sources.length) {
    lines.push(`<div class="hint">引用片段：${sources.map((s) => `《${esc(s.title)}》#${s.chunk_index}`).join('、')}</div>`);
  }
  if (pending) {
    lines.push(`<div class="pending-actions">
      <button class="primary" data-confirm="${pending.log_id}">确认执行</button>
      <button class="ghost" data-reject="${pending.log_id}">取消</button>
    </div>`);
  }
  return lines.join('');
}

async function sendChat(message) {
  if (state.chatBusy) return;
  state.chatBusy = true;
  pushBubble('user', message);
  const thinking = pushBubble('bot', '正在核对账本…');
  try {
    const result = await api('/api/chat', { method: 'POST', body: { message } });
    thinking.remove();
    pushBubble('bot', result.reply, pendingCard(result.pending_action, result.affordability, result.sources));
    if (result.pending_action) state.pendingLogId = result.pending_action.log_id;
    if (result.mode === 'rules' && !state.chatReady) {
      $('aiMode').textContent = '规则模式（未配置 Key）';
    }
    await renderAudit();
  } catch (error) {
    thinking.remove();
    pushBubble('bot', `出错了：${error.message}`);
    toast(error.message, 'error');
  } finally {
    state.chatBusy = false;
  }
}

/* ------------------------------------------------------------------ */
/* 事件绑定                                                            */
/* ------------------------------------------------------------------ */
function openTxnDialog(row) {
  $('txnTitle').textContent = row ? '编辑交易' : '记录一笔交易';
  $('txnId').value = row ? row.transaction_id : '';
  $('txnType').value = row ? row.trans_type : '支出';
  renderTxnCategoryOptions();
  if (row) {
    $('txnAccount').value = row.account_id;
    $('txnCategory').value = row.category_id;
    $('txnAmount').value = row.amount;
    $('txnDate').value = row.trans_date;
    $('txnRemark').value = row.remark || '';
  } else {
    $('txnAmount').value = '';
    $('txnDate').value = isoToday();
    $('txnRemark').value = '';
  }
  $('txnDialog').showModal();
}

function openAccountDialog(account) {
  $('accountTitle').textContent = account ? '编辑账户' : '新增账户';
  $('accountId').value = account ? account.account_id : '';
  $('accountName').value = account ? account.account_name : '';
  $('accountType').value = account ? account.account_type : '银行卡';
  $('accountBalance').value = account ? account.balance : 0;
  $('openingWrap').style.display = account ? 'none' : '';
  $('accountDialog').showModal();
}

function wireEvents() {
  $('nav').addEventListener('click', (event) => {
    const link = event.target.closest('a[data-view]');
    if (link) switchView(link.dataset.view).catch((error) => toast(error.message, 'error'));
  });

  document.querySelectorAll('[data-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog').close());
  });

  $('addBtn').addEventListener('click', () => openTxnDialog(null));
  $('addAccountBtn').addEventListener('click', () => openAccountDialog(null));
  $('transferBtn').addEventListener('click', () => { $('trDate').value = isoToday(); $('transferDialog').showModal(); });
  $('refreshBtn').addEventListener('click', () => render('overview').then(() => toast('已刷新')).catch((e) => toast(e.message, 'error')));

  $('txnType').addEventListener('change', renderTxnCategoryOptions);

  // 交易表单
  $('txnForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = $('txnId').value;
    const payload = {
      account_id: Number($('txnAccount').value),
      category_id: Number($('txnCategory').value),
      amount: Number($('txnAmount').value),
      trans_type: $('txnType').value,
      trans_date: $('txnDate').value,
      remark: $('txnRemark').value,
    };
    try {
      if (id) await api(`/api/transactions/${id}`, { method: 'PUT', body: payload });
      else await api('/api/transactions', { method: 'POST', body: payload });
      $('txnDialog').close();
      toast('已保存，账户余额由触发器自动更新');
      await loadAccounts();
      await render(state.view);
    } catch (error) { toast(error.message, 'error'); }
  });

  // 转账
  $('transferForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/transfers', {
        method: 'POST',
        body: {
          from_account: Number($('trFrom').value),
          to_account: Number($('trTo').value),
          amount: Number($('trAmount').value),
          trans_date: $('trDate').value,
          remark: $('trRemark').value,
        },
      });
      $('transferDialog').close();
      toast('转账完成（两条记录共享同一个 transfer_group_id）');
      await loadAccounts();
      await render(state.view);
    } catch (error) { toast(error.message, 'error'); }
  });

  // 账户
  $('accountForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const id = $('accountId').value;
    try {
      if (id) {
        await api(`/api/accounts/${id}`, {
          method: 'PUT',
          body: { account_name: $('accountName').value, account_type: $('accountType').value },
        });
      } else {
        await api('/api/accounts', {
          method: 'POST',
          body: {
            account_name: $('accountName').value,
            account_type: $('accountType').value,
            balance: Number($('accountBalance').value || 0),
          },
        });
      }
      $('accountDialog').close();
      toast('账户已保存');
      await loadAccounts();
      await render(state.view);
    } catch (error) { toast(error.message, 'error'); }
  });

  // 明细筛选
  $('fApply').addEventListener('click', () => { state.txnOffset = 0; renderRecords().catch((e) => toast(e.message, 'error')); });
  $('fReset').addEventListener('click', () => {
    $('fMonth').value = thisMonth(); $('fType').value = ''; $('fAccount').value = ''; $('fKeyword').value = '';
    state.txnOffset = 0; renderRecords().catch((e) => toast(e.message, 'error'));
  });
  $('prevPage').addEventListener('click', () => { state.txnOffset = Math.max(0, state.txnOffset - state.txnLimit); renderRecords().catch((e) => toast(e.message, 'error')); });
  $('nextPage').addEventListener('click', () => { state.txnOffset += state.txnLimit; renderRecords().catch((e) => toast(e.message, 'error')); });

  // 行内编辑/删除（事件委托）
  document.addEventListener('click', async (event) => {
    const edit = event.target.closest('[data-edit-txn]');
    const del = event.target.closest('[data-del-txn]');
    const editAsset = event.target.closest('[data-edit-asset]');
    const delAsset = event.target.closest('[data-del-asset]');
    const delKb = event.target.closest('[data-del-kb]');
    const confirmBtn = event.target.closest('[data-confirm]');
    const rejectBtn = event.target.closest('[data-reject]');
    const prompt = event.target.closest('[data-prompt]');
    try {
      if (edit) {
        const id = Number(edit.dataset.editTxn);
        const row = (state.txnCache || []).find((item) => item.transaction_id === id)
          || (await api(`/api/transactions?limit=200`)).find((item) => item.transaction_id === id);
        if (row) openTxnDialog(row);
      } else if (del) {
        if (!window.confirm('确认删除这笔交易？删除后余额会由触发器回滚。')) return;
        await api(`/api/transactions/${Number(del.dataset.delTxn)}`, { method: 'DELETE' });
        toast('已删除');
        await loadAccounts();
        await render(state.view);
      } else if (editAsset) {
        const id = Number(editAsset.dataset.editAsset);
        const item = (state.assetCache || []).find((row) => row.item_id === id);
        if (item) {
          $('editAssetId').value = item.item_id;
          $('editAssetName').value = item.item_name;
          $('editAssetType').value = item.item_type;
          $('editAssetAmount').value = item.amount;
          $('editAssetDate').value = item.acquire_date;
          $('editAssetRemark').value = item.remark || '';
          $('assetDialog').showModal();
        }
      } else if (delAsset) {
        if (!window.confirm('确认删除该资产/负债项？')) return;
        await api(`/api/asset-liability/${Number(delAsset.dataset.delAsset)}`, { method: 'DELETE' });
        toast('已删除');
        await renderAssets();
      } else if (delKb) {
        await api(`/api/knowledge/${Number(delKb.dataset.delKb)}`, { method: 'DELETE' });
        toast('资料已删除');
        await renderKnowledge();
      } else if (confirmBtn) {
        const result = await api('/api/chat', { method: 'POST', body: { message: '确认', confirm: true } });
        pushBubble('bot', result.reply);
        state.pendingLogId = null;
        await loadAccounts();
        await renderAudit();
      } else if (rejectBtn) {
        await api(`/api/ai/actions/${Number(rejectBtn.dataset.reject)}/reject`, { method: 'POST' });
        pushBubble('bot', '已取消该操作，账本没有发生任何变化。');
        state.pendingLogId = null;
        await renderAudit();
      } else if (prompt) {
        $('chatInput').value = prompt.dataset.prompt;
        $('chatInput').focus();
      }
    } catch (error) { toast(error.message, 'error'); }
  });

  // 预算
  $('budgetForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/budgets', {
        method: 'POST',
        body: {
          category_id: Number($('budgetCategory').value),
          year_month: $('budgetFormMonth').value,
          amount: Number($('budgetAmount').value),
        },
      });
      toast('预算已保存（upsert）');
      await renderBudget();
    } catch (error) { toast(error.message, 'error'); }
  });

  $('copyBudgetBtn').addEventListener('click', async () => {
    try {
      const result = await api('/api/budgets/copy', {
        method: 'POST',
        body: { source_month: monthOffset(1), target_month: thisMonth() },
      });
      toast(`已复制 ${result.copied} 条预算（PostgreSQL 下走 sp_copy_budget 存储过程）`);
      await renderBudget();
    } catch (error) { toast(error.message, 'error'); }
  });

  // 资产负债
  $('assetForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api('/api/asset-liability', {
        method: 'POST',
        body: {
          item_name: $('assetName').value,
          item_type: $('assetType').value,
          amount: Number($('assetAmount').value),
          acquire_date: $('assetDate').value || isoToday(),
          remark: $('assetRemark').value,
        },
      });
      $('assetForm').reset();
      $('assetDate').value = isoToday();
      toast('已添加');
      await renderAssets();
    } catch (error) { toast(error.message, 'error'); }
  });

  $('assetEditForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    try {
      await api(`/api/asset-liability/${Number($('editAssetId').value)}`, {
        method: 'PUT',
        body: {
          item_name: $('editAssetName').value,
          item_type: $('editAssetType').value,
          amount: Number($('editAssetAmount').value),
          acquire_date: $('editAssetDate').value,
          remark: $('editAssetRemark').value,
        },
      });
      $('assetDialog').close();
      toast('已保存');
      await renderAssets();
    } catch (error) { toast(error.message, 'error'); }
  });

  // AI 对话
  $('chatForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const value = $('chatInput').value.trim();
    if (!value) return;
    $('chatInput').value = '';
    sendChat(value);
  });

  $('knowledgeForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const content = $('kbContent').value.trim();
    if (!content) { toast('请填写资料内容', 'error'); return; }
    try {
      const result = await api('/api/knowledge', {
        method: 'POST',
        body: { title: $('kbTitle').value.trim() || '未命名资料', content },
      });
      $('kbTitle').value = '';
      $('kbContent').value = '';
      toast(`已入库 ${result.chunk_count} 个片段（检索模式：${result.mode}）`);
      await renderKnowledge();
    } catch (error) { toast(error.message, 'error'); }
  });
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function bootstrap() {
  wireEvents();
  $('txnDate').value = isoToday();
  $('trDate').value = isoToday();
  $('assetDate').value = isoToday();
  $('fMonth').value = thisMonth();
  try {
    await loadHealth();
    await loadAccounts();
    await loadCategories();
    await switchView('overview');
  } catch (error) {
    toast(`初始化失败：${error.message}`, 'error');
    $('insights').innerHTML = `<p>后端未就绪：${esc(error.message)}</p><p class="hint">请确认已运行 <code>uvicorn app.main:app</code>。</p>`;
  }
}

document.addEventListener('DOMContentLoaded', bootstrap);
