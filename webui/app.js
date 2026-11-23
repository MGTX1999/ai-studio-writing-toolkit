/* 长篇创作工具箱 · 本地可视化编辑器（原生 JavaScript，零依赖）
   所有数据都来自本机接口；令牌只保存在当前浏览器会话中。 */
(function () {
  'use strict';

  const enc = encodeURIComponent;

  // ---------------------------------------------------------------- 词表
  const KIND_LABELS = { novel: '小说', nonfiction: '非虚构', script: '剧本', academic: '学术' };
  const STATUS_LABELS = { draft: '草稿', revise: '修订', approved: '通过', blocked: '阻塞' };
  const MATERIAL_KIND_LABELS = { quote: '摘录', reference: '参考', idea: '灵感', research: '调研' };
  const SOURCE_KIND_LABELS = { file: '文件', url: '链接', ima: 'IMA', book: '书籍', dialog: '对话', other: '其他' };
  const FORESHADOW_STATUS_LABELS = { open: '未回收', resolved: '已回收', dropped: '已放弃' };
  const EXPECTATION_LABELS = { meet: '满足', delay: '延迟', dimension: '换维', violate: '违背' };
  const FACT_STATUS_LABELS = { pending: '待核', verified: '已核', disputed: '存疑', rejected: '驳回' };
  const LEVEL_LABELS = { master: '总纲', volume: '卷纲', chapter: '章纲' };
  const FIXED_STATE_KEYS = ['arc-status', 'character-state', 'next-hook', 'used-tropes'];
  const STAT_LABELS = {
    entities: '设定条目',
    events: '事件',
    foreshadow_open: '未回收伏笔',
    foreshadow_resolved: '已回收伏笔',
    chapters: '章节',
    total_words: '总字数',
    facts_pending: '待核事实',
    audits: '审校记录',
    outline_master: '总纲节点',
    outline_volumes: '卷纲',
    outline_chapters: '章纲',
    outline_nodes: '大纲版本行',
    materials: '素材',
    materials_unsourced: '无来源素材',
    sections: '段落'
  };

  const PROJECT_SECTIONS = ['overview', 'studio', 'automation', 'graph', 'outline', 'chapters', 'bible',
    'workbench', 'assistant', 'materials', 'foreshadow', 'facts', 'aistudio', 'style', 'quality', 'summaries', 'state', 'config'];
  const PROJECT_NAV = [
    ['overview', '概览'], ['studio', '创作台'], ['automation', '自动化'], ['graph', '故事图谱'],
    ['outline', '大纲'], ['chapters', '章节'],
    ['bible', '人物与设定'], ['workbench', '叙事工作台'],
    ['assistant', '写作助手'], ['materials', '素材与知识库'], ['foreshadow', '伏笔'], ['facts', '事实'],
    ['aistudio', 'AI Studio'], ['style', '文风'], ['quality', '质量'], ['summaries', '摘要'], ['state', '故事状态'],
    ['config', '项目配置']
  ];

  // 一级导航 8 组；旧 PROJECT_SECTIONS 仍保留，供旧链接重定向与业务分派。
  const NAV_GROUPS = [
    { id: 'dashboard', label: '驾驶舱', sections: ['overview'], icon: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>' },
    { id: 'writing', label: '写作', sections: ['studio', 'chapters', 'outline', 'summaries', 'state'], icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>' },
    { id: 'world', label: '世界', sections: ['bible', 'graph', 'foreshadow', 'facts'], icon: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18"/>' },
    { id: 'materials', label: '素材', sections: ['materials'], icon: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>' },
    { id: 'quality', label: '质量', sections: ['quality', 'style', 'assistant'], icon: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>' },
    { id: 'automation', label: '自动化', sections: ['automation'], icon: '<path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z"/>' },
    { id: 'aistudio', label: 'AI Studio', sections: ['aistudio'], icon: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><path d="m6 6 2 2M16 16l2 2M18 6l-2 2M8 16l-2 2"/>' },
    { id: 'settings', label: '设置', sections: ['config', 'settings'], icon: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>' }
  ];
  const SECTION_TO_GROUP = {
    overview: 'dashboard',
    studio: 'writing', chapters: 'writing', outline: 'writing', summaries: 'writing',
    state: 'writing', workbench: 'writing',
    bible: 'world', graph: 'world', foreshadow: 'world', facts: 'world',
    materials: 'materials',
    quality: 'quality', style: 'quality', assistant: 'quality',
    automation: 'automation',
    aistudio: 'aistudio',
    config: 'settings'
  };
  const SECTION_LABELS = {
    overview: '概览', studio: '创作台', chapters: '章节', outline: '大纲', summaries: '摘要',
    state: '故事状态', workbench: '叙事工作台',
    bible: '人物与设定', graph: '故事图谱', foreshadow: '伏笔', facts: '事实',
    materials: '素材与知识库',
    quality: '质量', style: '文风', assistant: '写作助手',
    automation: '自动化', aistudio: 'AI Studio',
    config: '项目配置', settings: '全局设置'
  };
  const BADGE_ICONS = { ok: '✓', warn: '!', danger: '✕', running: '●', awaiting: '◷', muted: '·' };

  const session = { token: '', pid: null, projectMeta: null };
  let route = { name: 'projects' };
  let renderToken = 0;
  let lastHash = '';
  let authBroken = false;
  let modalOnClose = null;
  let lastFocus = null;
  let uidSeed = 0;

  // ---------------------------------------------------------------- 基础工具
  function uid(prefix) {
    uidSeed += 1;
    return (prefix || 'f') + uidSeed;
  }

  function displayText(value, fallback) {
    const empty = fallback === undefined ? '—' : fallback;
    if (value === null || value === undefined) return empty;
    if (typeof value === 'number' && !Number.isFinite(value)) return empty;
    if (typeof value === 'string'
      && (value === 'null' || value === 'undefined' || value === 'NaN')) return empty;
    return String(value);
  }

  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    if (props) {
      for (const key of Object.keys(props)) {
        const value = props[key];
        if (key === 'text') {
          node.textContent = displayText(value, '');
          continue;
        }
        if (key === 'html') {
          node.innerHTML = value === null || value === undefined ? '' : String(value);
          continue;
        }
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'value') node.value = value;
        else if (key === 'checked') node.checked = !!value;
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else if (key.slice(0, 2) === 'on' && typeof value === 'function') {
          node.addEventListener(key.slice(2).toLowerCase(), value);
        } else if (value === true) {
          node.setAttribute(key, '');
        } else {
          node.setAttribute(key, value);
        }
      }
    }
    appendKids(node, kids);
    return node;
  }

  function appendKids(node, kids) {
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      if (kid instanceof Node) node.append(kid);
      else if (typeof kid === 'number' && !Number.isFinite(kid)) node.append(document.createTextNode('—'));
      else {
        const text = String(kid);
        node.append(document.createTextNode(
          text === 'null' || text === 'undefined' || text === 'NaN' ? '—' : text));
      }
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  // 先把整段文本转义，再只还原知识库返回的两个标记标签。
  function highlighted(snippet) {
    return escapeHtml(snippet || '')
      .replace(/&lt;mark&gt;/g, '<mark>')
      .replace(/&lt;\/mark&gt;/g, '</mark>');
  }

  function jsonText(value) {
    if (typeof value === 'string') return value;
    try { return JSON.stringify(value, null, 2); } catch (e) { return String(value); }
  }

  function formatTime(value) {
    if (value === null || value === undefined || value === '') return '—';
    const number = typeof value === 'number' ? value : Number(value);
    if (!Number.isFinite(number)) return String(value);
    const date = new Date(number < 1e12 ? number * 1000 : number);
    if (Number.isNaN(date.getTime())) return String(value);
    const pad = (n) => (n < 10 ? '0' + n : String(n));
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate())
      + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  function safeJson(raw, fallback) {
    if (raw && typeof raw === 'object') return raw;
    try { return JSON.parse(raw); } catch (e) { return fallback; }
  }

  function parseLoose(raw) {
    try { return { ok: true, value: JSON.parse(raw) }; }
    catch (e) { return { ok: true, value: raw }; }
  }

  function flattenConfig(obj, prefix, out) {
    const acc = out || [];
    const src = obj && typeof obj === 'object' ? obj : {};
    for (const key of Object.keys(src)) {
      const path = prefix ? prefix + '.' + key : key;
      const value = src[key];
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        flattenConfig(value, path, acc);
      } else {
        acc.push([path, value]);
      }
    }
    return acc;
  }

  // ---------------------------------------------------------------- 通用 DOM
  function field(labelText, control, hint, extraClass) {
    const id = control.id || uid('field');
    control.id = id;
    return el('div', { class: 'field' + (extraClass ? ' ' + extraClass : '') },
      el('label', { for: id, text: labelText }),
      control,
      hint ? el('p', { class: 'hint', text: hint }) : null);
  }

  function checkboxField(labelText, input, hint) {
    const id = input.id || uid('check');
    input.id = id;
    return el('div', { class: 'field checkbox-field' },
      el('label', { class: 'checkbox', for: id }, input, el('span', { text: labelText })),
      hint ? el('p', { class: 'hint', text: hint }) : null);
  }

  function selectEl(options, value) {
    const node = el('select');
    for (const pair of options) node.append(el('option', { value: pair[0], text: pair[1] }));
    if (value !== undefined && value !== null) node.value = value;
    return node;
  }

  function opts(map, allLabel) {
    const list = Object.keys(map).map((key) => [key, map[key]]);
    if (allLabel) list.unshift(['', allLabel]);
    return list;
  }

  function card(title, ...children) {
    return el('section', { class: 'card' },
      title ? el('h2', { class: 'card-title', text: title }) : null,
      ...children);
  }

  function pageHead(title, subtitle, ...actions) {
    return el('div', { class: 'page-head' },
      el('div', {},
        el('h1', { text: title }),
        subtitle ? el('p', { class: 'muted', text: subtitle }) : null),
      actions.length ? el('div', { class: 'page-actions' }, ...actions) : null);
  }

  function emptyState(title, hint, action) {
    const node = el('div', { class: 'empty' });
    node.append(el('p', { class: 'empty-title', text: title || '暂无数据' }));
    if (hint) node.append(el('p', { class: 'empty-hint', text: hint }));
    if (action) node.append(el('div', { class: 'empty-actions' }, action));
    return node;
  }

  function loadingState(text) {
    return el('div', { class: 'loading-state', role: 'status', 'aria-live': 'polite' },
      el('span', { class: 'spinner', 'aria-hidden': 'true' }),
      el('span', { class: 'loading-text', text: text || '加载中…' }));
  }

  function errorState(text, retry) {
    const button = retry
      ? el('button', { class: 'btn', type: 'button', text: '重试' })
      : null;
    if (button) button.addEventListener('click', retry);
    return el('div', { class: 'error-state', role: 'alert' },
      el('p', { class: 'error-title', text: '加载失败' }),
      el('p', { class: 'error-hint', text: text || '请稍后重试。' }),
      button ? el('div', { class: 'empty-actions' }, button) : null);
  }

  function badge(label, tone) {
    const node = el('span', { class: 'badge' + (tone ? ' badge-' + tone : '') });
    if (tone && BADGE_ICONS[tone]) {
      node.append(el('span', { class: 'badge-icon', 'aria-hidden': 'true', text: BADGE_ICONS[tone] }));
    }
    node.append(el('span', { class: 'badge-text', text: displayText(label) }));
    return node;
  }

  function dataTable(headers, rows, emptyText) {
    if (!rows || !rows.length) return emptyState(emptyText);
    const tbody = el('tbody');
    for (const row of rows) {
      tbody.append(el('tr', {}, row.map((cell) => el('td', {}, cell))));
    }
    const table = el('table', { class: 'table' },
      el('thead', {}, el('tr', {}, headers.map((h) => el('th', { scope: 'col', text: h })))),
      tbody);
    return el('div', { class: 'table-wrap' }, table);
  }

  function kvList(pairs) {
    const dl = el('dl', { class: 'kv' });
    for (const pair of pairs) {
      const value = pair[1] instanceof Node ? pair[1] : displayText(pair[1]);
      dl.append(el('dt', { text: pair[0] }), el('dd', {}, value));
    }
    return dl;
  }

  function statGrid(stats) {
    const data = stats || {};
    const keys = Object.keys(STAT_LABELS).filter((key) => data[key] !== undefined);
    for (const key of Object.keys(data)) {
      if (keys.indexOf(key) < 0) keys.push(key);
    }
    if (!keys.length) return emptyState('暂无统计');
    const grid = el('div', { class: 'stat-grid' });
    for (const key of keys) {
      grid.append(el('div', { class: 'stat' },
        el('div', { class: 'stat-value', text: displayText(data[key]) }),
        el('div', { class: 'stat-label', text: STAT_LABELS[key] || key })));
    }
    return grid;
  }

  function warningsBox(list, title) {
    if (!list || !list.length) return null;
    return el('div', { class: 'notice' },
      el('strong', { text: title || '配置提醒' }),
      el('ul', {}, list.map((item) => el('li', { text: item }))));
  }

  // ---------------------------------------------------------------- 错误与请求
  class ApiError extends Error {
    constructor(message, status, payload) {
      super(message);
      this.name = 'ApiError';
      this.status = status || 0;
      this.payload = payload || null;
    }
  }

  function reportError(error) {
    if (error instanceof ApiError) return;
    if (error && error.name === 'AbortError') return;
    console.error(error);
    toast('操作失败：' + (error && error.message ? error.message : String(error)), 'error');
  }

  function handler(fn) {
    return function (event) {
      let result;
      try {
        result = fn(event);
      } catch (error) {
        reportError(error);
        return;
      }
      if (result && typeof result.then === 'function') result.then(null, reportError);
    };
  }

  async function api(path, options) {
    const opts2 = options || {};
    const method = opts2.method || 'GET';
    const headers = { 'Accept': 'application/json', 'X-Longform-Token': session.token };
    const init = { method: method, headers: headers, cache: 'no-store' };
    if (opts2.body !== undefined && opts2.body !== null) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts2.body);
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      if (!opts2.quiet) toast('网络请求失败：' + error.message, 'error');
      throw new ApiError('网络请求失败');
    }
    const raw = await response.text();
    let data = null;
    if (raw) {
      try { data = JSON.parse(raw); } catch (error) { data = null; }
    }
    if (response.status === 401 || (data && data.error === 'unauthorized')) {
      showAuthError();
      throw new ApiError('令牌无效', 401);
    }
    if (!response.ok || (data && data.ok === false)) {
      const message = (data && data.error) || ('请求失败（HTTP ' + response.status + '）');
      if (!opts2.quiet) toast(message, 'error');
      throw new ApiError(message, response.status, data);
    }
    return data;
  }

  async function withBusy(button, fn) {
    if (!button || button.disabled) return undefined;
    const previous = button.textContent;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = '处理中…';
    try {
      return await fn();
    } finally {
      button.disabled = false;
      button.removeAttribute('aria-busy');
      button.textContent = previous;
    }
  }

  // ---------------------------------------------------------------- 提示条
  function toast(message, tone) {
    const box = document.getElementById('toasts');
    if (!box) return;
    const close = el('button', { class: 'toast-close', type: 'button', 'aria-label': '关闭提示', text: '关闭' });
    const node = el('div', { class: 'toast' + (tone ? ' toast-' + tone : '') },
      el('span', { class: 'toast-text', text: message }), close);
    close.addEventListener('click', () => node.remove());
    box.append(node);
    while (box.children.length > 4) box.firstElementChild.remove();
    if (tone === 'success') {
      window.setTimeout(() => { if (node.isConnected) node.remove(); }, 6000);
    }
  }

  function showAuthError() {
    if (authBroken) return;
    authBroken = true;
    const view = document.getElementById('view');
    if (!view) return;
    view.replaceChildren(el('section', { class: 'card auth-error' },
      el('h1', { text: '令牌无效' }),
      el('p', { text: '令牌无效，请重新运行 python cli.py serve，并用新打开的地址访问。' }),
      el('p', { class: 'hint', text: '令牌只保存在当前浏览器会话中，关闭标签页后需要重新获取。' })));
    const nav = document.getElementById('nav');
    if (nav) nav.replaceChildren();
    setHeaderProject(null);
  }

  // ---------------------------------------------------------------- 弹窗
  function openModal(options) {
    closeModal();
    lastFocus = document.activeElement;
    const root = document.getElementById('modal-root');
    if (!root) return;
    const closeBtn = el('button', { class: 'btn btn-small', type: 'button', text: '关闭' });
    closeBtn.addEventListener('click', () => closeModal());
    const dialog = el('div', {
      class: 'modal' + (options.wide ? ' modal-wide' : ''),
      role: 'dialog',
      'aria-modal': 'true',
      'aria-labelledby': 'modal-title'
    },
      el('div', { class: 'modal-head' },
        el('h2', { id: 'modal-title', text: options.title || '' }),
        closeBtn));
    const bodyNodes = Array.isArray(options.body) ? options.body : [options.body];
    for (const node of bodyNodes) if (node) dialog.append(node);
    const actions = options.actions || [];
    if (actions.length) dialog.append(el('div', { class: 'modal-actions' }, ...actions));
    root.replaceChildren(el('div', { class: 'modal-overlay' }, dialog));
    document.body.classList.add('modal-open');
    modalOnClose = options.onClose || null;
    const focusTarget = options.initialFocus
      || dialog.querySelector('input, select, textarea')
      || dialog.querySelector('button, a[href], [tabindex]:not([tabindex="-1"])');
    if (focusTarget) focusTarget.focus();
  }

  function closeModal() {
    const root = document.getElementById('modal-root');
    if (!root) return;
    if (!root.firstElementChild) return;
    root.replaceChildren();
    document.body.classList.remove('modal-open');
    const callback = modalOnClose;
    modalOnClose = null;
    if (callback) callback();
    if (lastFocus && lastFocus.isConnected && typeof lastFocus.focus === 'function') {
      lastFocus.focus();
    }
    lastFocus = null;
  }

  function trapFocus(event) {
    const root = document.getElementById('modal-root');
    const dialog = root && root.firstElementChild;
    if (!dialog) return;
    const nodes = Array.from(dialog.querySelectorAll(
      'button, input, select, textarea, a[href], [tabindex]:not([tabindex="-1"])'))
      .filter((node) => !node.disabled && node.getClientRects().length > 0);
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function confirmDialog(options) {
    return new Promise((resolve) => {
      let settled = false;
      const finish = (value) => {
        if (settled) return;
        settled = true;
        closeModal();
        resolve(value);
      };
      const cancel = el('button', { class: 'btn', type: 'button', text: '取消' });
      cancel.addEventListener('click', () => finish(false));
      const ok = el('button', { class: 'btn btn-primary', type: 'button', text: options.confirmText || '确认' });
      ok.addEventListener('click', () => finish(true));
      openModal({
        title: options.title || '请确认',
        body: el('p', { class: 'modal-text', text: options.message || '' }),
        actions: [cancel, ok],
        onClose: () => { if (!settled) { settled = true; resolve(false); } }
      });
    });
  }

  // ---------------------------------------------------------------- 差异
  function lineDiff(aText, bText) {
    const a = String(aText || '').split('\n');
    const b = String(bText || '').split('\n');
    if (a.length > 600 || b.length > 600) return null;
    const rows = a.length;
    const cols = b.length;
    const dp = [];
    for (let i = 0; i <= rows; i += 1) dp.push(new Uint16Array(cols + 1));
    for (let i = rows - 1; i >= 0; i -= 1) {
      for (let j = cols - 1; j >= 0; j -= 1) {
        dp[i][j] = a[i] === b[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
    const out = [];
    let i = 0;
    let j = 0;
    while (i < rows && j < cols) {
      if (a[i] === b[j]) { out.push([' ', a[i]]); i += 1; j += 1; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push(['-', a[i]]); i += 1; }
      else { out.push(['+', b[j]]); j += 1; }
    }
    while (i < rows) { out.push(['-', a[i]]); i += 1; }
    while (j < cols) { out.push(['+', b[j]]); j += 1; }
    return out;
  }

  function renderLineDiff(pairs) {
    const box = el('div', { class: 'diff' });
    if (!pairs) {
      box.append(el('span', { class: 'diff-meta', text: '文本过长，已跳过逐行对比。' }));
      return box;
    }
    const limit = 800;
    for (let index = 0; index < Math.min(pairs.length, limit); index += 1) {
      const pair = pairs[index];
      const tone = pair[0] === '+' ? ' diff-add' : (pair[0] === '-' ? ' diff-del' : '');
      box.append(el('div', { class: 'diff-line' + tone, text: pair[0] + ' ' + pair[1] }));
    }
    if (pairs.length > limit) {
      box.append(el('div', { class: 'diff-line diff-meta', text: '…（其余 ' + (pairs.length - limit) + ' 行已省略）' }));
    }
    return box;
  }

  function renderUnifiedDiff(text) {
    const box = el('div', { class: 'diff' });
    if (!text) {
      box.append(el('span', { class: 'diff-meta', text: '（无差异）' }));
      return box;
    }
    const lines = String(text).split('\n');
    for (const line of lines) {
      let cls = 'diff-line';
      if (line.slice(0, 3) === '+++' || line.slice(0, 3) === '---' || line.slice(0, 2) === '@@') {
        cls += ' diff-meta';
      } else if (line.slice(0, 1) === '+') {
        cls += ' diff-add';
      } else if (line.slice(0, 1) === '-') {
        cls += ' diff-del';
      }
      box.append(el('div', { class: cls, text: line || ' ' }));
    }
    return box;
  }

  // ---------------------------------------------------------------- 路由与导航
  function navGroupById(id) {
    for (const group of NAV_GROUPS) if (group.id === id) return group;
    return null;
  }

  function projectHash(pid, group, section, extra) {
    let hash = '#/p/' + enc(pid) + '/' + group + '/' + section;
    for (const part of (extra || [])) hash += '/' + enc(part);
    return hash;
  }

  function parseRoute() {
    const raw = location.hash.replace(/^#/, '');
    const parts = raw.split('/').filter(Boolean);
    if (parts[0] === 'settings') return { name: 'settings' };
    if (parts[0] === 'onboarding') return { name: 'onboarding' };
    if (parts[0] === 'p' && parts[1]) {
      const pid = decodeURIComponent(parts[1]);
      const second = parts[2] ? decodeURIComponent(parts[2]) : '';
      const third = parts[3] ? decodeURIComponent(parts[3]) : '';
      const group = navGroupById(second);
      if (group) {
        if (group.id === 'settings' && third === 'settings') return { name: 'settings' };
        const valid = group.sections.indexOf(third) >= 0
          || (PROJECT_SECTIONS.indexOf(third) >= 0 && SECTION_TO_GROUP[third] === group.id);
        return {
          name: 'project',
          pid: pid,
          group: group.id,
          section: valid ? third : group.sections[0],
          extra: valid ? parts.slice(4).map((part) => decodeURIComponent(part)) : [],
          legacy: !valid
        };
      }
      if (PROJECT_SECTIONS.indexOf(second) >= 0) {
        return {
          name: 'project',
          pid: pid,
          group: SECTION_TO_GROUP[second] || 'dashboard',
          section: second,
          extra: parts.slice(3).map((part) => decodeURIComponent(part)),
          legacy: true
        };
      }
      return {
        name: 'project', pid: pid, group: 'dashboard', section: 'overview',
        extra: parts.slice(3).map((part) => decodeURIComponent(part)), legacy: true
      };
    }
    return { name: 'projects' };
  }

  function navIcon(paths) {
    return el('span', {
      class: 'nav-icon', 'aria-hidden': 'true',
      html: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        + 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + paths + '</svg>'
    });
  }

  function groupHref(group) {
    if (group.id === 'settings' && route.name !== 'project') return '#/settings';
    if (route.name === 'project') {
      return projectHash(route.pid, group.id, group.sections[0]);
    }
    return '#/projects';
  }

  function navLink(href, label, active, group) {
    const link = el('a', {
      class: 'nav-link' + (active ? ' is-active' : ''),
      href: href,
      'aria-current': active ? 'page' : null,
      'aria-label': label,
      title: label
    },
      group ? navIcon(group.icon) : null,
      el('span', { class: 'nav-label', text: label }),
      el('span', { class: 'nav-badge' }));
    if (group) link.dataset.group = group.id;
    return link;
  }

  function renderNav() {
    const nav = document.getElementById('nav');
    if (!nav) return;
    const links = [];
    for (const group of NAV_GROUPS) {
      const active = route.name === 'settings'
        ? group.id === 'settings'
        : (route.name === 'project' && route.group === group.id);
      links.push(navLink(groupHref(group), group.label, active, group));
    }
    nav.replaceChildren(...links);
    if (route.name === 'project') refreshNavBadges(route.pid);
  }

  function setNavBadge(nav, groupId, text, tone) {
    const host = nav.querySelector('[data-group="' + groupId + '"] .nav-badge');
    const link = nav.querySelector('[data-group="' + groupId + '"]');
    if (!host) return;
    host.replaceChildren(badge(text, tone));
    if (link) {
      const label = (navGroupById(groupId) || {}).label || groupId;
      link.setAttribute('aria-label', label + '，' + text);
    }
  }

  async function refreshNavBadges(pid) {
    const nav = document.getElementById('nav');
    if (!nav) return;
    let pending = null;
    let latest = null;
    try {
      const listed = await api('/api/projects/' + enc(pid) + '/handoffs?status=pending',
        { quiet: true });
      pending = (listed && listed.handoffs) || [];
    } catch (error) { pending = null; }
    try {
      const quality = await api('/api/projects/' + enc(pid) + '/quality', { quiet: true });
      latest = quality && quality.latest;
    } catch (error) { latest = null; }
    if (route.name !== 'project' || route.pid !== pid) return;
    if (pending && pending.length) {
      setNavBadge(nav, 'aistudio', '待提交 ' + pending.length, 'warn');
    }
    const counts = latest && latest.lint ? latest.lint.counts : null;
    const blocks = counts ? Number(counts.block) || 0 : 0;
    if (blocks > 0) setNavBadge(nav, 'quality', 'L0 阻断 ' + blocks, 'danger');
  }

  function setHeaderProject(project) {
    const wrap = document.getElementById('project-switch');
    const box = document.getElementById('project-badge');
    if (!wrap || !box) return;
    closeProjectMenu();
    if (!project) {
      wrap.hidden = true;
      box.replaceChildren();
      return;
    }
    wrap.hidden = false;
    const name = project.name || project.id || '未命名项目';
    box.replaceChildren(
      el('span', { class: 'project-badge-name', text: name }),
      el('span', { class: 'caret', 'aria-hidden': 'true', text: '▾' }));
    box.setAttribute('aria-label', '当前项目：' + name + '，点击切换项目');
  }

  // ---------------------------------------------------------------- 项目切换 / 下一步 / 抽屉
  function closeProjectMenu() {
    const menu = document.getElementById('project-menu');
    const btn = document.getElementById('project-badge');
    if (!menu) return;
    menu.hidden = true;
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }

  async function toggleProjectMenu() {
    const menu = document.getElementById('project-menu');
    const btn = document.getElementById('project-badge');
    if (!menu || !btn) return;
    if (!menu.hidden) { closeProjectMenu(); return; }
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    menu.replaceChildren(el('p', { class: 'project-menu-empty', text: '加载中…' }));
    let list = [];
    try {
      list = await api('/api/projects', { quiet: true });
    } catch (error) { list = []; }
    if (!Array.isArray(list)) list = [];
    const items = list.map((project) => {
      const item = el('button', {
        class: 'project-menu-item' + (project.id === session.pid ? ' is-current' : ''),
        type: 'button', role: 'menuitem',
        text: project.name + (project.id && project.id !== project.name ? ' · ' + project.id : '')
      });
      item.addEventListener('click', () => {
        closeProjectMenu();
        location.hash = projectHash(project.id, 'dashboard', 'overview');
      });
      return item;
    });
    if (!items.length) items.push(el('p', { class: 'project-menu-empty', text: '还没有项目' }));
    const all = el('button', { class: 'project-menu-item', type: 'button', role: 'menuitem', text: '全部项目' });
    all.addEventListener('click', () => { closeProjectMenu(); location.hash = '#/projects'; });
    menu.replaceChildren(...items, all);
  }

  function applyFocusMode(on) {
    document.body.classList.toggle('focus-mode', on);
    const btn = document.getElementById('studio-focus-btn');
    if (!btn) return;
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.textContent = on ? '退出专注' : '专注模式';
  }

  function setNavOpen(open) {
    const nav = document.getElementById('nav');
    const btn = document.getElementById('nav-toggle');
    const backdrop = document.getElementById('nav-backdrop');
    if (!nav || !btn) return;
    nav.classList.toggle('is-open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.setAttribute('aria-label', open ? '关闭导航' : '打开导航');
    if (backdrop) backdrop.hidden = !open;
    document.body.classList.toggle('nav-open', open);
  }

  function renderNextAction(pid) {
    const host = document.getElementById('next-action');
    if (!host) return;
    if (!pid) {
      host.hidden = true;
      host.replaceChildren();
      return;
    }
    host.hidden = false;
    const button = el('button', {
      class: 'btn btn-small btn-primary', type: 'button', text: '下一步',
      'aria-label': '执行下一步'
    });
    button.addEventListener('click', handler(() => runNextAction(button, pid)));
    host.replaceChildren(button);
  }

  async function runNextAction(button, pid) {
    if (button.disabled) return;
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    try {
      let out = null;
      try {
        out = await api('/api/projects/' + enc(pid) + '/autopilot', { quiet: true });
      } catch (error) {
        return;
      }
      if (!out) return;
      if (out.action === 'handoff') {
        toast('已生成下一步', 'success');
        location.hash = projectHash(pid, 'aistudio', 'aistudio');
      } else if (out.action === 'done') {
        button.textContent = '已完成';
        toast('全部已规划章节已完成', 'success');
        return;
      } else if (out.action === 'human') {
        const group = out.gate === 'fix_lint' ? 'quality' : 'automation';
        location.hash = projectHash(pid, group, group === 'quality' ? 'quality' : 'automation');
      } else {
        location.hash = projectHash(pid, 'automation', 'automation');
      }
    } finally {
      if (button.textContent !== '已完成') {
        button.disabled = false;
        button.removeAttribute('aria-busy');
      }
    }
  }

  // ---------------------------------------------------------------- 二级导航
  function renderGroupTabs(group, current) {
    if (!group || group.sections.length < 2) return null;
    const bar = el('div', { class: 'subnav', role: 'tablist', 'aria-label': group.label + ' 子页面' });
    for (const section of group.sections) {
      const active = current.section === section;
      const href = group.id === 'settings' && section === 'settings'
        ? '#/settings'
        : projectHash(current.pid, group.id, section);
      const tab = el('a', {
        class: 'subnav-tab' + (active ? ' is-active' : ''),
        href: href,
        role: 'tab',
        'aria-selected': active ? 'true' : 'false',
        'aria-current': active ? 'page' : null,
        text: SECTION_LABELS[section] || section
      });
      bar.append(tab);
    }
    return bar;
  }

  // ---------------------------------------------------------------- 命令面板
  function commandTarget(label, hint, run) {
    return { label: label, hint: hint, run: run };
  }

  function openCommandPalette() {
    const root = document.getElementById('modal-root');
    if (!root) return;
    const items = [];
    for (const group of NAV_GROUPS) {
      items.push(commandTarget(group.label, '一级导航', () => {
        closeModal();
        location.hash = groupHref(group);
      }));
    }
    if (route.name === 'project') {
      const group = navGroupById(route.group);
      if (group) {
        for (const section of group.sections) {
          const href = group.id === 'settings' && section === 'settings'
            ? '#/settings'
            : projectHash(route.pid, group.id, section);
          items.push(commandTarget(SECTION_LABELS[section] || section, group.label + ' 页面', () => {
            closeModal();
            location.hash = href;
          }));
        }
      }
    }
    const input = el('input', {
      type: 'search', class: 'command-input', placeholder: '搜索页面或项目…',
      'aria-label': '搜索命令', autocomplete: 'off', spellcheck: 'false'
    });
    const list = el('div', { class: 'command-list', role: 'listbox', 'aria-label': '命令列表' });
    let active = 0;
    let matches = [];

    function filtered() {
      const query = input.value.trim().toLowerCase();
      if (!query) return items.slice();
      return items.filter((item) => item.label.toLowerCase().indexOf(query) >= 0
        || (item.hint || '').toLowerCase().indexOf(query) >= 0);
    }

    function draw() {
      matches = filtered();
      if (active >= matches.length) active = Math.max(0, matches.length - 1);
      if (!matches.length) {
        list.replaceChildren(el('p', { class: 'command-empty', text: '没有匹配的命令' }));
        input.removeAttribute('aria-activedescendant');
        return;
      }
      const nodes = matches.map((item, index) => {
        const node = el('button', {
          id: 'command-item-' + index,
          class: 'command-item' + (index === active ? ' is-active' : ''),
          type: 'button', role: 'option', 'aria-selected': index === active ? 'true' : 'false'
        },
          el('span', { class: 'command-label', text: item.label }),
          el('span', { class: 'command-hint', text: item.hint }));
        node.addEventListener('click', () => item.run());
        return node;
      });
      list.replaceChildren(...nodes);
      input.setAttribute('aria-activedescendant', 'command-item-' + active);
    }

    input.addEventListener('input', () => { active = 0; draw(); });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        if (matches.length) { active = (active + 1) % matches.length; draw(); }
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        if (matches.length) { active = (active - 1 + matches.length) % matches.length; draw(); }
      } else if (event.key === 'Enter') {
        event.preventDefault();
        if (matches[active]) matches[active].run();
      }
    });

    openModal({
      title: '命令面板',
      body: el('div', { class: 'command' }, input, list),
      initialFocus: input
    });
    draw();
    api('/api/projects', { quiet: true })
      .then((list) => {
        if (!Array.isArray(list) || !list.length) return;
        for (const project of list) {
          items.push(commandTarget(project.name, '项目', () => {
            closeModal();
            location.hash = projectHash(project.id, 'dashboard', 'overview');
          }));
        }
        draw();
      })
      .catch(() => {});
  }

  // ---------------------------------------------------------------- 页面渲染

  async function render() {
    const token = ++renderToken;
    route = parseRoute();
    setNavOpen(false);
    if (!(route.name === 'project' && route.section === 'studio')) applyFocusMode(false);
    if (route.name === 'project' && route.legacy) {
      const target = projectHash(route.pid, route.group, route.section, route.extra);
      if (location.hash !== target) {
        location.replace(target);
        return;
      }
    }
    renderNav();
    const view = document.getElementById('view');
    if (!view) return;
    renderNextAction(route.name === 'project' ? route.pid : null);
    view.replaceChildren(loadingState('加载中…'));
    let nodes;
    try {
      if (route.name === 'settings') {
        setHeaderProject(null);
        session.pid = null;
        nodes = await pageSettings();
      } else if (route.name === 'onboarding') {
        setHeaderProject(null);
        session.pid = null;
        nodes = await pageOnboarding();
      } else if (route.name === 'projects') {
        setHeaderProject(null);
        session.pid = null;
        nodes = await pageProjects();
      } else {
        nodes = await renderProject(route);
      }
    } catch (error) {
      if (token !== renderToken || (error instanceof ApiError && error.status === 401)) return;
      reportError(error);
      view.replaceChildren(errorState(
        error && error.message ? error.message : String(error),
        () => render()));
      return;
    }
    if (token !== renderToken) return;
    const pageNodes = Array.isArray(nodes) ? nodes.slice() : [nodes];
    if (route.name === 'project') {
      const tabs = renderGroupTabs(navGroupById(route.group), route);
      if (tabs) pageNodes.unshift(tabs);
    }
    view.replaceChildren.apply(view, pageNodes);
    if (location.hash !== lastHash) {
      lastHash = location.hash;
      window.scrollTo(0, 0);
    }
  }

  async function renderProject(current) {
    const meta = await api('/api/projects/' + enc(current.pid));
    if (!meta || !meta.project) {
      setHeaderProject(null);
      renderNextAction(null);
      return el('section', { class: 'card' },
        el('h2', { class: 'card-title', text: '项目不存在' }),
        el('p', { class: 'muted', text: '未找到项目 ' + current.pid + '。' }),
        el('a', { class: 'btn', href: '#/projects', text: '返回项目列表' }));
    }
    session.pid = current.pid;
    session.projectMeta = meta;
    setHeaderProject(meta.project);
    const section = PROJECT_SECTIONS.indexOf(current.section) >= 0 ? current.section : 'overview';
    switch (section) {
      case 'overview': return pageOverview(meta);
      case 'studio': return pageStudio(current.pid, current.extra[0]);
      case 'automation': return pageAutomation(current.pid);
      case 'graph': return pageGraph(current.pid);
      case 'outline': return pageOutline(current.pid);
      case 'chapters':
        return current.extra.length
          ? pageChapterEditor(current.pid, current.extra[0])
          : pageChapters(current.pid);
      case 'bible': return pageBible(current.pid);
      case 'workbench': return pageWorkbench(current.pid);
      case 'assistant': return pageAssistant(current.pid);
      case 'materials': return pageMaterials(current.pid);
      case 'foreshadow': return pageForeshadow(current.pid);
      case 'facts': return pageFacts(current.pid);
      case 'aistudio': return pageAiStudio(current.pid);
      case 'style': return pageStyle(current.pid);
      case 'quality': return pageQuality(current.pid);
      case 'summaries': return pageSummaries(current.pid);
      case 'state': return pageState(current.pid);
      case 'config': return pageConfig(meta, current.pid);
      default: return pageOverview(meta);
    }
  }

  // ---------------------------------------------------------------- 项目列表
  async function pageProjects() {
    const list = await api('/api/projects');
    let profiles = [];
    try {
      profiles = await api('/api/profiles', { quiet: true });
    } catch (error) {
      profiles = [];
    }
    if (!Array.isArray(profiles)) profiles = [];

    const rows = (list || []).map((project) => {
      const open = el('a', { class: 'btn btn-small', href: projectHash(project.id, 'dashboard', 'overview'), text: '打开' });
      const remove = el('button', { class: 'btn btn-small btn-danger', type: 'button', text: '删除' });
      remove.addEventListener('click', handler(async () => {
        const answer = prompt('此操作不可撤销。请输入项目 id「' + project.id + '」以确认删除：');
        if (answer === null) return;
        if (answer !== project.id) {
          toast('确认串不匹配，已取消删除', 'error');
          return;
        }
        await withBusy(remove, async () => {
          await api('/api/projects/' + enc(project.id) + '?confirm=' + enc(project.id), { method: 'DELETE' });
          toast('已删除项目 ' + project.id, 'success');
          render();
        });
      }));
      return [
        el('span', { text: project.name }),
        badge(KIND_LABELS[project.kind] || project.kind),
        String(project.chapters),
        el('code', { text: project.id }),
        el('div', { class: 'row-actions' }, open, remove)
      ];
    });

    const nameInput = el('input', { type: 'text', required: true, maxlength: 300, placeholder: '例如：山海旧事' });
    const kindInput = selectEl(opts(KIND_LABELS), 'novel');
    const profileInput = selectEl(
      [['', '不绑定变体']].concat(profiles.map((item) => [item.name, item.name + (item.description ? ' — ' + item.description : '')])),
      '');
    if (!profiles.length) profileInput.disabled = true;
    const submit = el('button', { class: 'btn btn-primary', type: 'submit', text: '创建项目' });
    const form = el('form', { class: 'form-grid' },
      field('项目名称', nameInput, '项目 id 由名称自动生成'),
      field('类型', kindInput),
      field('绑定变体（可选）', profileInput, profiles.length ? '变体来自 config/profiles/' : '未发现可用变体，可稍后在概览页绑定'),
      el('div', { class: 'field field-wide form-actions' }, submit));

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const name = nameInput.value.trim();
      if (!name) {
        toast('请填写项目名称', 'error');
        nameInput.focus();
        return;
      }
      const payload = { name: name, kind: kindInput.value };
      if (profileInput.value) payload.profile = profileInput.value;
      await withBusy(submit, async () => {
        const result = await api('/api/projects', { method: 'POST', body: payload });
        toast('项目已就绪：' + result.id, 'success');
        nameInput.value = '';
        location.hash = projectHash(result.id, 'dashboard', 'overview');
      });
    }));

    return [
      pageHead('项目', '每部作品一个项目；删除项目会连同章节、大纲、素材一并清除。',
        el('a', { class: 'btn btn-primary', href: '#/onboarding', text: '引导式立项' })),
      card('新建项目', form),
      card('全部项目（' + (list || []).length + '）',
        dataTable(['名称', '类型', '章节数', '项目 id', '操作'], rows, '还没有项目，先在上方创建一个。'))
    ];
  }

  // ---------------------------------------------------------------- 引导式立项
  const WIZARD_STEPS = ['核心定位', '深度定制', '确认创建'];
  const WIZARD_POV_FALLBACK = ['第三人称', '第一人称', '第三人称限知'];

  function splitWords(text) {
    return String(text || '').split(/[、,，;；\s]+/)
      .map((word) => word.trim())
      .filter(Boolean);
  }

  function styleSeedText(template) {
    const seed = template && template.style_seed ? template.style_seed : {};
    return typeof seed['语感'] === 'string' ? seed['语感'] : '';
  }

  async function pageOnboarding() {
    let templates = [];
    let preferences = null;
    try {
      const pack = await api('/api/studio/templates', { quiet: true });
      if (pack && pack.ok) {
        templates = Array.isArray(pack.templates) ? pack.templates : [];
        preferences = pack.preferences || null;
      }
    } catch (error) {
      templates = [];
    }
    if (!Array.isArray(templates)) templates = [];
    const prefs = preferences || {};

    const state = {
      step: 1,
      premise: '',
      genre: prefs.genre || '',
      chapters: 100,
      target_words: 100000,
      template: prefs.template || '',
      pov: prefs.pov || '第三人称',
      style: prefs.style || '',
      banned_words: Array.isArray(prefs.banned_words) ? prefs.banned_words.join('、') : '',
      save: true
    };
    if (state.template && !templates.some((item) => item.id === state.template)) {
      state.template = '';
    }

    const host = el('div', { class: 'wizard' });
    const stepsBar = el('ol', { class: 'wizard-steps', 'aria-label': '立项步骤' });
    const body = el('div', { class: 'wizard-body' });
    host.append(stepsBar, body);

    function findTemplate(id) {
      return templates.find((item) => item.id === id) || null;
    }

    function drawSteps() {
      stepsBar.replaceChildren(...WIZARD_STEPS.map((label, index) => el('li', {
        class: 'wizard-step'
          + (state.step === index + 1 ? ' is-current' : '')
          + (state.step > index + 1 ? ' is-done' : ''),
        'aria-current': state.step === index + 1 ? 'step' : null
      },
        el('span', { class: 'wizard-step-index', text: String(index + 1) }),
        el('span', { class: 'wizard-step-label', text: label }))));
    }

    function setStep(next) {
      state.step = Math.max(1, Math.min(3, next));
      draw();
      const focusTarget = body.querySelector('textarea, input, select');
      if (focusTarget) focusTarget.focus();
      // 回到页面顶部，让标题与步骤条保持可见；不要用 host.scrollIntoView，
      // 否则固定顶栏会盖住页面标题。
      if (typeof window.scrollTo === 'function') {
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
    }

    function actionsRow(...buttons) {
      return el('div', { class: 'wizard-actions' }, ...buttons);
    }

    function stepOne() {
      const premise = el('textarea', {
        rows: 4, value: state.premise,
        placeholder: '一句话说清：谁、想要什么、被什么挡住。'
      });
      const genre = el('input', {
        type: 'text', value: state.genre, maxlength: 80,
        list: 'wizard-genres', placeholder: '例如：悬疑'
      });
      const genreList = el('datalist', { id: 'wizard-genres' },
        templates.map((item) => el('option', { value: item.default_genre })));
      const chapters = el('input', {
        type: 'number', min: 1, max: 5000, step: 1,
        value: String(state.chapters)
      });
      const words = el('input', {
        type: 'number', min: 500, max: 200000, step: 500,
        value: String(state.target_words)
      });
      const next = el('button', { class: 'btn btn-primary', type: 'button', text: '下一步' });
      next.addEventListener('click', handler(() => {
        state.premise = premise.value.trim();
        state.genre = genre.value.trim();
        const chapterCount = Number(chapters.value);
        const wordCount = Number(words.value);
        if (!state.premise) {
          toast('请填写核心设定', 'error');
          premise.focus();
          return;
        }
        if (!state.genre) {
          toast('请填写题材', 'error');
          genre.focus();
          return;
        }
        if (!Number.isInteger(chapterCount) || chapterCount < 1 || chapterCount > 5000) {
          toast('目标章数需为 1–5000 的整数', 'error');
          chapters.focus();
          return;
        }
        if (!Number.isInteger(wordCount) || wordCount < 500 || wordCount > 200000) {
          toast('目标字数需为 500–200000 的整数', 'error');
          words.focus();
          return;
        }
        state.chapters = chapterCount;
        state.target_words = wordCount;
        setStep(2);
      }));
      return el('section', { class: 'card wizard-card' },
        el('h2', { class: 'card-title', text: '第 1 步 · 核心定位' }),
        el('p', { class: 'hint', text: '这些信息会写进立项交接单；创建后仍可在项目配置里修改。' }),
        el('div', { class: 'form-grid' },
          field('核心设定（必填）', premise, '12 字左右即可生成项目名，细节留给设计交接单展开。', 'field-wide'),
          field('题材（必填）', genre, '可以手填，也可以从已有题材模板里选。'),
          el('div', { class: 'choice-row' },
            field('目标章数', chapters),
            field('目标字数', words)),
          genreList),
        actionsRow(next));
    }

    function stepTwo() {
      const template = selectEl(
        [['', '不使用模板']].concat(templates.map((item) => [
          item.id, item.name + '（' + item.default_genre + '）'
        ])), state.template);
      const pov = el('input', { type: 'text', value: state.pov, maxlength: 40 });
      const style = el('textarea', {
        rows: 3, value: state.style,
        placeholder: '例如：冷峻短句，重物证与动作，少用比喻。'
      });
      const banned = el('input', {
        type: 'text', value: state.banned_words,
        placeholder: '用逗号/顿号分隔，最多 50 个'
      });

      template.addEventListener('change', () => {
        const picked = findTemplate(template.value);
        if (!picked) return;
        state.banned_words = banned.value;
        state.genre = picked.default_genre;
        const seedPov = picked.style_seed && picked.style_seed['叙事视角'];
        state.pov = typeof seedPov === 'string' && seedPov ? seedPov : state.pov;
        const seedStyle = styleSeedText(picked);
        if (seedStyle) state.style = seedStyle;
        state.template = picked.id;
        draw();
      });

      const capture = () => {
        state.template = template.value;
        state.pov = pov.value.trim() || '第三人称';
        state.style = style.value.trim();
        state.banned_words = banned.value;
      };

      const back = el('button', { class: 'btn', type: 'button', text: '上一步' });
      back.addEventListener('click', handler(() => {
        capture();
        setStep(1);
      }));

      const skip = el('button', { class: 'btn', type: 'button', text: '跳过' });
      skip.addEventListener('click', handler(() => {
        state.template = '';
        state.pov = '第三人称';
        state.style = '';
        state.banned_words = '';
        setStep(3);
      }));

      const random = el('button', { class: 'btn', type: 'button', text: '随机填充' });
      random.addEventListener('click', handler(() => {
        const pool = templates.length ? templates : null;
        if (pool) {
          const picked = pool[Math.floor(Math.random() * pool.length)];
          template.value = picked.id;
          state.template = picked.id;
          state.genre = picked.default_genre;
          const seedPov = picked.style_seed && picked.style_seed['叙事视角'];
          pov.value = typeof seedPov === 'string' && seedPov
            ? seedPov : WIZARD_POV_FALLBACK[Math.floor(Math.random() * WIZARD_POV_FALLBACK.length)];
          style.value = styleSeedText(picked) || style.value;
        } else {
          pov.value = WIZARD_POV_FALLBACK[Math.floor(Math.random() * WIZARD_POV_FALLBACK.length)];
        }
        toast('已随机填充可选项，可继续手动调整', 'success');
      }));

      const next = el('button', { class: 'btn btn-primary', type: 'button', text: '下一步' });
      next.addEventListener('click', handler(() => {
        capture();
        if (!state.pov) {
          toast('叙事视角不能为空', 'error');
          pov.focus();
          return;
        }
        setStep(3);
      }));

      return el('section', { class: 'card wizard-card' },
        el('h2', { class: 'card-title', text: '第 2 步 · 深度定制' }),
        el('p', { class: 'hint', text: '这一步可以跳过；模板只提供起点，不会锁死后续设定。' }),
        el('div', { class: 'form-grid' },
          field('题材模板', template, templates.length
            ? '选中模板会带入题材、视角与文风种子。'
            : '暂未发现题材模板，可直接跳过。'),
          field('叙事视角', pov, '最多 40 字。'),
          field('文风', style, '一句话描述语气、句长与用词偏好。', 'field-wide'),
          field('禁用词', banned, '写入风格设置，写作时会被质检提示。', 'field-wide')),
        actionsRow(back, skip, random, next));
    }

    function defaultName() {
      const raw = state.premise.replace(/\s+/g, ' ').trim();
      return raw.slice(0, 12) || '未命名作品';
    }

    function stepThree() {
      const name = el('input', { type: 'text', value: defaultName(), maxlength: 300 });
      const save = el('input', { type: 'checkbox', checked: state.save });
      const create = el('button', {
        class: 'btn btn-primary', type: 'button', text: '创建项目并生成设计'
      });

      const back = el('button', { class: 'btn', type: 'button', text: '上一步' });
      back.addEventListener('click', handler(() => {
        state.save = save.checked;
        setStep(2);
      }));

      const bannedWords = splitWords(state.banned_words);
      create.addEventListener('click', handler(() => withBusy(create, async () => {
        const finalName = name.value.trim();
        if (!finalName) {
          toast('请填写项目名称', 'error');
          name.focus();
          return;
        }
        const project = await api('/api/projects', {
          method: 'POST',
          body: { name: finalName, kind: 'novel', overwrite: false }
        });
        const pid = project.id;
        await api('/api/projects/' + enc(pid) + '/studio/design', {
          method: 'POST',
          body: {
            premise: state.premise,
            genre: state.genre,
            chapters: state.chapters,
            target_words: state.target_words
          }
        });
        if (save.checked) {
          try {
            await api('/api/preferences', {
              method: 'PUT',
              body: {
                genre: state.genre,
                pov: state.pov,
                style: state.style,
                banned_words: bannedWords,
                template: state.template
              }
            });
          } catch (error) {
            // 偏好保存失败不阻断立项，只在控制台留痕。
            console.warn('保存偏好失败', error);
          }
        }
        toast('项目已创建：' + pid, 'success');
        location.hash = projectHash(pid, 'aistudio', 'aistudio');
      })));

      return el('section', { class: 'card wizard-card' },
        el('h2', { class: 'card-title', text: '第 3 步 · 确认' }),
        el('p', { class: 'hint', text: '创建后将生成一张 project_design 交接单，交给 AI Studio 产出设计 JSON。' }),
        kvList([
          ['核心设定', state.premise],
          ['题材', state.genre],
          ['目标章数', String(state.chapters)],
          ['目标字数', String(state.target_words)],
          ['题材模板', state.template ? (findTemplate(state.template) || {}).name || state.template : '不使用'],
          ['叙事视角', state.pov],
          ['文风', state.style || '未设置'],
          ['禁用词', bannedWords.length ? bannedWords.join('、') : '未设置']
        ]),
        el('div', { class: 'form-grid' },
          field('项目名称', name, '项目 id 会按名称自动生成。', 'field-wide'),
          checkboxField('把本次选择保存为全局偏好', save,
            '写入 profiles/_default/profile.json 的 preferences 段，下次自动预填。')),
        actionsRow(back, create));
    }

    function draw() {
      drawSteps();
      body.replaceChildren(state.step === 1 ? stepOne()
        : state.step === 2 ? stepTwo() : stepThree());
    }

    draw();
    return [
      pageHead('引导式立项', '三步完成核心定位、可选定制与确认；创建后直接进入 AI Studio 自驾页。',
        el('a', { class: 'btn', href: '#/projects', text: '返回项目列表' })),
      host
    ];
  }

  // ---------------------------------------------------------------- 驾驶舱
  const DASH_STATUS_ORDER = ['draft', 'revise', 'approved', 'blocked'];

  function promptSummary(text, limit) {
    const raw = String(text || '').trim();
    if (!raw) return '（还没有 prompt）';
    const max = limit || 360;
    return raw.length > max ? raw.slice(0, max) + '\n…' : raw;
  }

  function dashMetric(label, value, sub, href, small) {
    return el(href ? 'a' : 'div', {
      class: 'dash-metric',
      href: href || null
    },
      el('span', { class: 'dash-metric-label', text: label }),
      el('span', { class: 'dash-metric-value' + (small ? ' is-text' : ''), text: displayText(value) }),
      sub ? el('span', { class: 'dash-metric-sub', text: sub }) : null);
  }

  function nextActionBadge(action) {
    const tones = { tool: 'ok', handoff: 'warn', human: 'danger', done: 'muted' };
    return badge(displayText(action, '未知'), tones[action] || 'muted');
  }

  function dashNextView(pid, next) {
    if (!next || !next.action) {
      return emptyState('暂时无法给出下一步', '刷新重试；若刚建项目，先去 AI Studio 完成立项设计。',
        el('a', { class: 'btn', href: projectHash(pid, 'aistudio', 'aistudio'), text: '去 AI Studio' }));
    }
    const parts = [
      el('div', { class: 'inline-form' },
        nextActionBadge(next.action),
        el('span', { class: 'muted', text: displayText(next.why, '暂无说明') }))
    ];
    if (next.action === 'handoff' && next.handoff) {
      const h = next.handoff;
      const chapterText = h.chapter_n === null || h.chapter_n === undefined
        ? '' : ' · 第 ' + h.chapter_n + ' 章';
      parts.push(el('p', { class: 'hint', text: '交接单 ' + displayText(h.handoff_id)
        + ' · ' + displayText(h.kind) + chapterText }));
      parts.push(el('pre', { class: 'code-block', text: promptSummary(h.prompt) }));
    } else if (next.action === 'tool') {
      parts.push(el('p', { class: 'hint', text: '可执行工具：' + displayText(next.tool) }));
    } else if (next.action === 'human') {
      parts.push(el('p', { class: 'hint', text: '人工闸门：' + displayText(next.gate)
        + (next.run_id ? ' · 运行 ' + next.run_id : '') }));
    } else if (next.action === 'done') {
      parts.push(el('div', { class: 'notice' },
        el('p', { text: displayText(next.why, '已完成') })));
    }
    parts.push(el('a', {
      class: 'btn btn-primary', href: projectHash(pid, 'aistudio', 'aistudio'), text: '去 AI Studio'
    }));
    return el('div', { class: 'stack' }, parts);
  }

  function dashPipelineView(pid, chapters) {
    if (!chapters.length) {
      return emptyState('还没有章节', '先在创作台写第一章，或导入大纲后再生成章节。',
        el('a', { class: 'btn', href: projectHash(pid, 'writing', 'studio'), text: '去创作台' }));
    }
    const groups = {};
    for (const chapter of chapters) {
      const key = chapter.status || 'draft';
      if (!groups[key]) groups[key] = [];
      groups[key].push(chapter);
    }
    const order = DASH_STATUS_ORDER.concat(
      Object.keys(groups).filter((key) => DASH_STATUS_ORDER.indexOf(key) < 0));
    const box = el('div', { class: 'dash-pipeline' });
    for (const status of order) {
      const list = groups[status];
      if (!list || !list.length) continue;
      const chips = el('div', { class: 'dash-pipe-items' });
      for (const chapter of list) {
        const title = chapter.title || ('第' + chapter.n + '章');
        chips.append(el('a', {
          class: 'dash-pipe-chip', title: title,
          href: projectHash(pid, 'writing', 'chapters', [chapter.n])
        },
          el('span', { class: 'dash-pipe-n', text: '第' + chapter.n + '章' }),
          el('span', { class: 'dash-pipe-title', text: title })));
      }
      box.append(el('div', { class: 'dash-pipe-group' },
        el('div', { class: 'dash-pipe-head' },
          chapterStatusBadge(status),
          el('span', { class: 'muted', text: list.length + ' 章' })),
        chips));
    }
    return box;
  }

  function dashRunsView(pid, runs, tokenMap) {
    if (!runs.length) {
      return emptyState('还没有自动化运行', '在自动化页启动一次运行，或直接让 AI Studio 写下一章。',
        el('a', { class: 'btn', href: projectHash(pid, 'automation', 'automation'), text: '去自动化' }));
    }
    const box = el('div', { class: 'dash-runs' });
    for (const run of runs) {
      const target = run.target || {};
      box.append(el('a', {
        class: 'dash-run-row', href: projectHash(pid, 'automation', 'automation')
      },
        runStatusBadge(run.status),
        el('span', { class: 'dash-run-id', text: displayText(run.id) }),
        el('span', { class: 'dash-run-chapter', text: target.chapter_n ? '第' + target.chapter_n + '章' : '—' }),
        el('span', { class: 'dash-run-time', text: formatTime(run.created_at) }),
        el('span', { class: 'dash-run-tokens', text: (tokenMap[run.id] || 0) + ' tokens' })));
    }
    return box;
  }

  function lintFindingsView(lint) {
    const findings = (lint && lint.findings) || [];
    const list = el('div', { class: 'findings' });
    for (const finding of findings.slice(0, 6)) {
      const block = finding.level === 'block';
      list.append(el('div', { class: 'finding ' + (block ? 'finding-red' : 'finding-yellow') },
        el('div', { class: 'finding-head' },
          badge(finding.rule || 'rule', block ? 'danger' : 'warn'),
          badge(block ? '阻断' : '提醒', block ? 'danger' : 'warn'),
          finding.locator ? el('code', { text: finding.locator }) : null),
        finding.evidence ? el('p', { class: 'finding-quote', text: finding.evidence }) : null,
        el('p', { class: 'hint', text: finding.suggestion || '' })));
    }
    if (!findings.length) list.append(emptyState('本章 L0 检查通过，没有发现硬伤。'));
    return list;
  }

  async function pageOverview(meta) {
    const project = meta.project;
    const pid = project.id;
    const settled = await Promise.allSettled([
      api('/api/projects/' + enc(pid) + '/autopilot', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/foreshadow?status=open', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/quality', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/usage?by=chapter', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/usage?by=run', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/runs?limit=5', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/chapters', { quiet: true }),
      api('/api/projects/' + enc(pid) + '/pipeline', { quiet: true }),
      api('/api/profiles', { quiet: true })
    ]);
    const valueAt = (index, fallback) => (settled[index].status === 'fulfilled'
      && settled[index].value !== undefined && settled[index].value !== null)
      ? settled[index].value : fallback;

    const next = valueAt(0, null);
    const foreshadows = valueAt(1, null);
    const quality = valueAt(2, null);
    const chapterUsage = valueAt(3, null);
    const runUsage = valueAt(4, null);
    const runs = valueAt(5, []);
    const chapters = valueAt(6, []);
    const pipeline = valueAt(7, null);
    let profiles = valueAt(8, []);
    if (!Array.isArray(profiles)) profiles = [];
    if (!Array.isArray(runs)) runs = [];
    if (!Array.isArray(chapters)) chapters = [];

    const stats = meta.stats || {};
    const chapterCount = stats.chapters !== undefined && stats.chapters !== null
      ? stats.chapters : chapters.length;
    const totalWords = stats.total_words !== undefined && stats.total_words !== null
      ? stats.total_words : '—';
    const approved = chapters.filter((item) => item.status === 'approved').length;

    const currentChapter = pipeline && pipeline.chapter_n !== null && pipeline.chapter_n !== undefined
      ? Number(pipeline.chapter_n)
      : (chapters.length ? Math.max.apply(null, chapters.map((item) => Number(item.n) || 0)) : 0);
    let openCount = null;
    let overdue = null;
    if (Array.isArray(foreshadows)) {
      openCount = foreshadows.length;
      overdue = foreshadows.filter((item) => {
        const due = item.due_chapter;
        if (due !== null && due !== undefined && currentChapter > Number(due)) return true;
        return currentChapter - (Number(item.planted_chapter) || 0) >= 8;
      }).length;
    } else if (stats.foreshadow_open !== undefined && stats.foreshadow_open !== null) {
      openCount = stats.foreshadow_open;
    }

    const loops = quality && Array.isArray(quality.loops) ? quality.loops : [];
    const latestLoop = loops.length ? loops[0] : null;
    const qualityValue = latestLoop && typeof latestLoop.best_score === 'number'
      ? latestLoop.best_score.toFixed(2) : '未运行';
    const qualitySub = latestLoop
      ? '第 ' + displayText(latestLoop.chapter_n) + ' 章 · '
        + (LOOP_STATUS_LABELS[latestLoop.status] || displayText(latestLoop.status))
      : '质量环尚未运行';

    const usageTotal = chapterUsage && typeof chapterUsage.total_tokens === 'number'
      ? chapterUsage.total_tokens : null;
    const usageCalls = chapterUsage && Array.isArray(chapterUsage.groups)
      ? chapterUsage.groups.reduce((sum, group) => sum + (Number(group.calls) || 0), 0) : 0;
    const usageValue = usageTotal === null ? '未记录' : usageTotal;

    const tokenMap = {};
    if (runUsage && Array.isArray(runUsage.groups)) {
      for (const group of runUsage.groups) {
        tokenMap[group.key] = (Number(group.input_tokens) || 0)
          + (Number(group.output_tokens) || 0);
      }
    }

    const nextBtn = el('button', {
      class: 'btn btn-primary', type: 'button', text: '下一步', 'aria-label': '执行下一步'
    });
    nextBtn.addEventListener('click', handler(async () => {
      if (nextBtn.disabled) return;
      nextBtn.disabled = true;
      nextBtn.setAttribute('aria-busy', 'true');
      try {
        const out = await api('/api/projects/' + enc(pid) + '/autopilot');
        if (!out) return;
        if (out.action === 'handoff') {
          toast('已生成下一步交接单', 'success');
          location.hash = projectHash(pid, 'aistudio', 'aistudio');
        } else if (out.action === 'tool') {
          toast('可执行工具：' + displayText(out.tool), 'success');
        } else if (out.action === 'human') {
          const target = out.gate === 'fix_lint' ? 'quality' : 'automation';
          location.hash = projectHash(pid, target, target);
        } else if (out.action === 'done') {
          toast('全部已规划章节已完成', 'success');
        }
      } finally {
        nextBtn.disabled = false;
        nextBtn.removeAttribute('aria-busy');
      }
    }));

    // L0 警报：无报告时可就地运行一次检查。
    const lintHost = el('div');
    let latestLint = quality && quality.latest ? quality.latest : null;
    if (!latestLint && chapters.length) {
      // /quality 读取失败时仍可用章节列表的最后一章兜底运行 L0。
      latestLint = { chapter_n: Number(chapters[chapters.length - 1].n), lint: null };
    }
    function drawLint() {
      if (!latestLint) {
        lintHost.replaceChildren(emptyState('还没有章节正文',
          '先在创作台写一章，再回来做 L0 检查。',
          el('a', { class: 'btn', href: projectHash(pid, 'writing', 'studio'), text: '去创作台' })));
        return;
      }
      const n = latestLint.chapter_n;
      if (!latestLint.lint) {
        const run = el('button', { class: 'btn btn-primary', type: 'button', text: '运行检查' });
        run.addEventListener('click', handler(async () => {
          await withBusy(run, async () => {
            const fresh = await api('/api/projects/' + enc(pid) + '/lint?chapter_n='
              + enc(String(n)));
            latestLint = { chapter_n: n, lint: fresh };
            drawLint();
            toast('L0 检查已运行', 'success');
          });
        }));
        lintHost.replaceChildren(
          el('div', { class: 'inline-form' },
            el('span', { class: 'muted', text: '第 ' + n + ' 章' }),
            badge('未检查', 'muted')),
          emptyState('还没有 L0 报告', '运行一次检查，查看本章硬伤与提醒。', run));
        return;
      }
      const counts = latestLint.lint.counts || {};
      const blocks = Number(counts.block) || 0;
      const warns = Number(counts.warn) || 0;
      const nodes = [
        el('div', { class: 'inline-form' },
          el('span', { class: 'muted', text: '第 ' + n + ' 章' }),
          badge('阻断 ' + blocks, blocks ? 'danger' : 'ok'),
          badge('提醒 ' + warns, warns ? 'warn' : 'ok'),
          el('a', {
            class: 'btn btn-small', href: projectHash(pid, 'quality', 'quality'), text: '查看全部'
          }))
      ];
      if (latestLint.lint.blocked) {
        nodes.push(el('div', { class: 'notice notice-danger' },
          el('p', { text: '存在阻断级 L0 问题，建议先修复再启动质量环。' })));
      }
      nodes.push(lintFindingsView(latestLint.lint));
      lintHost.replaceChildren(...nodes);
    }
    drawLint();

    // 项目与变体（保留原有绑定入口）。
    const profileNames = profiles.map((item) => item.name);
    const profileOptions = [['', '不绑定变体']].concat(profileNames.map((name) => [name, name]));
    if (meta.profile && profileNames.indexOf(meta.profile) < 0) {
      profileOptions.push([meta.profile, meta.profile + '（目录缺失）']);
    }
    const profileSelect = selectEl(profileOptions, meta.profile || '');
    if (!profiles.length) profileSelect.disabled = true;
    const bindBtn = el('button', { class: 'btn', type: 'button', text: '绑定所选变体' });
    bindBtn.addEventListener('click', handler(async () => {
      await withBusy(bindBtn, async () => {
        await api('/api/projects/' + enc(pid) + '/config', {
          method: 'PUT',
          body: { key: 'profile', value: profileSelect.value || null }
        });
        toast(profileSelect.value ? '已绑定变体 ' + profileSelect.value : '已解绑变体', 'success');
        render();
      });
    }));
    const unbindBtn = el('button', {
      class: 'btn btn-danger', type: 'button', text: '解绑变体', disabled: !meta.profile
    });
    unbindBtn.addEventListener('click', handler(async () => {
      await withBusy(unbindBtn, async () => {
        await api('/api/projects/' + enc(pid) + '/config', {
          method: 'PUT',
          body: { key: 'profile', value: null }
        });
        toast('已解绑变体', 'success');
        render();
      });
    }));

    return [
      pageHead('驾驶舱', '今天写什么、卡在哪、质量如何、花了多少。', nextBtn),
      el('div', { class: 'dash-metrics' },
        dashMetric('进度', chapterCount + ' 章',
          '总字数 ' + displayText(totalWords) + ' · 定稿 ' + approved + ' 章',
          projectHash(pid, 'writing', 'chapters')),
        dashMetric('伏笔', overdue === null ? '—' : overdue + ' 条',
          openCount === null ? '伏笔数据读取失败' : '未回收 ' + openCount + ' 条',
          projectHash(pid, 'world', 'foreshadow')),
        dashMetric('质量', qualityValue, qualitySub,
          projectHash(pid, 'quality', 'quality'), typeof qualityValue === 'string'),
        dashMetric('用量', usageValue,
          usageTotal === null ? '尚无模型调用记录' : usageCalls + ' 次调用',
          projectHash(pid, 'automation', 'automation'), usageTotal === null)),
      el('div', { class: 'dash-grid' },
        el('div', { class: 'dash-col' },
          card('下一步', dashNextView(pid, next)),
          card('流水线', dashPipelineView(pid, chapters)),
          card('最近运行', dashRunsView(pid, runs, tokenMap))),
        el('div', { class: 'dash-col' },
          card('L0 警报', lintHost),
          card('项目与变体',
            kvList([
              ['项目 id', el('code', { text: pid })],
              ['类型', KIND_LABELS[project.kind] || project.kind],
              ['创建时间', formatTime(project.created_at)],
              ['当前变体', meta.profile || '未绑定']
            ]),
            el('div', { class: 'inline-form' },
              field('选择变体', profileSelect), bindBtn, unbindBtn),
            el('p', { class: 'hint', text: profiles.length
              ? '共 ' + profiles.length + ' 个可用变体。'
              : 'config/profiles/ 下没有可用变体。' }))))
    ];
  }

  // ---------------------------------------------------------------- 大纲
  async function pageOutline(pid) {
    const base = '/api/projects/' + enc(pid) + '/outline';
    const nodes = await api(base);
    const groups = { master: [], volume: [], chapter: [] };
    for (const node of (nodes || [])) {
      if (!groups[node.level]) groups[node.level] = [];
      groups[node.level].push(node);
    }

    const levelSel = selectEl(opts(LEVEL_LABELS), 'master');
    const idInput = el('input', { type: 'text', maxlength: 120, placeholder: '留空自动生成（master / v01 / c001）' });
    const parentInput = el('input', { type: 'text', maxlength: 120, placeholder: '留空自动挂载' });
    const titleInput = el('input', { type: 'text', maxlength: 300 });
    const statusSel = selectEl(opts(STATUS_LABELS), 'draft');
    const seqInput = el('input', { type: 'number', min: '0', step: '1', value: '0' });
    const chapterNInput = el('input', { type: 'number', min: '0', step: '1' });
    const startInput = el('input', { type: 'number', min: '0', step: '1' });
    const endInput = el('input', { type: 'number', min: '0', step: '1' });
    const bodyArea = el('textarea', { rows: '8', spellcheck: 'false' });
    const dataArea = el('textarea', { rows: '4', spellcheck: 'false', placeholder: '{"key":"value"}' });

    const chapterField = field('章号 chapter_n', chapterNInput, '仅章纲使用');
    const startField = field('起始章 start_chapter', startInput, '仅卷纲使用');
    const endField = field('结束章 end_chapter', endInput, '仅卷纲使用');

    function syncLevel() {
      const level = levelSel.value;
      chapterField.hidden = level !== 'chapter';
      startField.hidden = level !== 'volume';
      endField.hidden = level !== 'volume';
      parentInput.disabled = level === 'master';
      if (level === 'master') parentInput.value = '';
    }
    levelSel.addEventListener('change', syncLevel);
    syncLevel();

    const submitBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '保存大纲节点' });
    const resetBtn = el('button', { class: 'btn', type: 'button', text: '清空表单' });
    resetBtn.addEventListener('click', () => {
      levelSel.value = 'master';
      idInput.value = '';
      parentInput.value = '';
      titleInput.value = '';
      statusSel.value = 'draft';
      seqInput.value = '0';
      chapterNInput.value = '';
      startInput.value = '';
      endInput.value = '';
      bodyArea.value = '';
      dataArea.value = '';
      submitBtn.textContent = '保存大纲节点';
      syncLevel();
      levelSel.focus();
    });

    const form = el('form', { class: 'form-grid' },
      field('层级 level', levelSel),
      field('节点 id（可选）', idInput, '同一 id 再次保存会生成新版本'),
      field('上级 parent_id（可选）', parentInput),
      field('标题', titleInput),
      field('状态', statusSel),
      field('序号 seq', seqInput),
      chapterField,
      startField,
      endField,
      field('正文', bodyArea, '总纲/卷纲上限 20000 字符；章纲上限更短', 'field-wide'),
      field('数据 data（JSON 对象）', dataArea, '可选，留空表示空对象', 'field-wide'),
      el('div', { class: 'field field-wide form-actions' }, submitBtn, resetBtn));

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const level = levelSel.value;
      const payload = {
        level: level,
        status: statusSel.value,
        seq: Number(seqInput.value || 0),
        body: bodyArea.value
      };
      if (idInput.value.trim()) payload.id = idInput.value.trim();
      if (level !== 'master' && parentInput.value.trim()) payload.parent_id = parentInput.value.trim();
      if (titleInput.value.trim()) payload.title = titleInput.value.trim();
      if (level === 'chapter') {
        if (chapterNInput.value === '') {
          toast('章纲必须填写 chapter_n', 'error');
          chapterNInput.focus();
          return;
        }
        payload.chapter_n = Number(chapterNInput.value);
      }
      if (level === 'volume') {
        if (startInput.value === '' || endInput.value === '') {
          toast('卷纲必须填写起止章号', 'error');
          return;
        }
        payload.start_chapter = Number(startInput.value);
        payload.end_chapter = Number(endInput.value);
      }
      if (dataArea.value.trim()) {
        let parsed;
        try {
          parsed = JSON.parse(dataArea.value);
        } catch (error) {
          toast('data 必须是合法 JSON 对象', 'error');
          return;
        }
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
          toast('data 必须是 JSON 对象', 'error');
          return;
        }
        payload.data = parsed;
      }
      await withBusy(submitBtn, async () => {
        const result = await api(base, { method: 'POST', body: payload });
        toast('已保存节点 ' + result.id + '（v' + result.version + '）', 'success');
        render();
      });
    }));

    async function startEdit(node, button) {
      await withBusy(button, async () => {
        const detail = await api(base + '/' + enc(node.id));
        if (!detail) {
          toast('节点不存在', 'error');
          return;
        }
        levelSel.value = detail.level;
        syncLevel();
        idInput.value = detail.id;
        parentInput.value = detail.parent_id || '';
        titleInput.value = detail.title || '';
        statusSel.value = detail.status || 'draft';
        seqInput.value = detail.seq === null || detail.seq === undefined ? '0' : String(detail.seq);
        chapterNInput.value = detail.chapter_n === null || detail.chapter_n === undefined ? '' : String(detail.chapter_n);
        startInput.value = detail.start_chapter === null || detail.start_chapter === undefined ? '' : String(detail.start_chapter);
        endInput.value = detail.end_chapter === null || detail.end_chapter === undefined ? '' : String(detail.end_chapter);
        bodyArea.value = detail.body || '';
        const data = detail.data_json || {};
        dataArea.value = Object.keys(data).length ? JSON.stringify(data, null, 2) : '';
        submitBtn.textContent = '保存修订（生成新版本）';
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
        titleInput.focus({ preventScroll: true });
      });
    }

    async function removeNode(node, button) {
      const answer = prompt('请输入大纲节点 id「' + node.id + '」以确认删除：');
      if (answer === null) return;
      if (answer !== node.id) {
        toast('确认串不匹配，已取消删除', 'error');
        return;
      }
      await withBusy(button, async () => {
        await api(base + '/' + enc(node.id) + '?confirm=' + enc(node.id), { method: 'DELETE' });
        toast('已删除节点 ' + node.id, 'success');
        render();
      });
    }

    async function showVersions(node, button) {
      await withBusy(button, async () => {
        const versions = await api(base + '/' + enc(node.id) + '/versions');
        if (!Array.isArray(versions) || !versions.length) {
          toast('该节点还没有版本记录', 'error');
          return;
        }
        const versionOptions = versions.map((item) => [
          String(item.version),
          'v' + item.version + ' · ' + (STATUS_LABELS[item.status] || item.status) + ' · ' + item.body_chars + ' 字'
        ]);
        const aSel = selectEl(versionOptions, String(versions[0].version));
        const bSel = selectEl(versionOptions, String(versions[versions.length - 1].version));
        const output = el('div', { class: 'diff-box' });
        const compare = el('button', { class: 'btn', type: 'button', text: '对比所选版本' });
        if (versions.length < 2) compare.disabled = true;
        compare.addEventListener('click', handler(async () => {
          await withBusy(compare, async () => {
            const from = await api(base + '/' + enc(node.id) + '?version=' + enc(aSel.value));
            const to = await api(base + '/' + enc(node.id) + '?version=' + enc(bSel.value));
            const pairs = lineDiff((from && from.body) || '', (to && to.body) || '');
            output.replaceChildren(
              el('p', { class: 'hint', text: 'v' + aSel.value + ' → v' + bSel.value }),
              renderLineDiff(pairs));
          });
        }));
        const rows = versions.map((item) => [
          'v' + item.version,
          STATUS_LABELS[item.status] || item.status,
          String(item.body_chars) + ' 字',
          formatTime(item.created_at)
        ]);
        openModal({
          title: '「' + node.id + '」版本历史',
          wide: true,
          body: [
            dataTable(['版本', '状态', '篇幅', '时间'], rows, '无版本'),
            el('div', { class: 'inline-form' },
              field('起点版本', aSel), field('终点版本', bSel), compare),
            versions.length < 2
              ? el('p', { class: 'hint', text: '只有 1 个版本，修订一次后才能对比。' })
              : null,
            output
          ]
        });
      });
    }

    const sections = ['master', 'volume', 'chapter'].map((level) => {
      const items = groups[level] || [];
      const rows = items.map((node) => {
        const editBtn = el('button', { class: 'btn btn-small', type: 'button', text: '编辑' });
        editBtn.addEventListener('click', handler(() => startEdit(node, editBtn)));
        const versionBtn = el('button', { class: 'btn btn-small', type: 'button', text: '版本' });
        versionBtn.addEventListener('click', handler(() => showVersions(node, versionBtn)));
        const removeBtn = el('button', { class: 'btn btn-small btn-danger', type: 'button', text: '删除' });
        removeBtn.addEventListener('click', handler(() => removeNode(node, removeBtn)));
        let range = '—';
        if (level === 'chapter') range = String(node.chapter_n);
        if (level === 'volume') range = node.start_chapter + '–' + node.end_chapter;
        return [
          el('code', { text: node.id }),
          node.title || '（无标题）',
          badge(STATUS_LABELS[node.status] || node.status),
          String(node.seq === null || node.seq === undefined ? 0 : node.seq),
          range,
          String(node.body_chars || 0) + ' 字',
          node.parent_id ? el('code', { text: node.parent_id }) : '—',
          el('div', { class: 'row-actions' }, editBtn, versionBtn, removeBtn)
        ];
      });
      return card(LEVEL_LABELS[level] + '（' + items.length + '）',
        dataTable(['id', '标题', '状态', '序号', '章号/区间', '篇幅', '上级', '操作'], rows, '暂无' + LEVEL_LABELS[level]));
    });

    return [
      pageHead('大纲', '三级大纲：总纲 / 卷纲 / 章纲。同一 id 再次保存会生成新版本。'),
      card('新建 / 编辑节点', form),
      ...sections
    ];
  }

  // ---------------------------------------------------------------- 章节
  async function pageChapters(pid) {
    const list = await api('/api/projects/' + enc(pid) + '/chapters');
    let wordMap = {};
    try {
      const words = await api('/api/projects/' + enc(pid) + '/word-stats');
      for (const row of ((words && words.chapters) || [])) wordMap[row.n] = row;
    } catch (error) {
      wordMap = {};
    }
    const rows = (list || []).map((chapter) => [
      String(chapter.n),
      chapter.title,
      badge(STATUS_LABELS[chapter.status] || chapter.status),
      'v' + chapter.latest_version,
      wordMap[chapter.n] ? String(wordMap[chapter.n].chars) + ' 字' : '—',
      el('a', {
        class: 'btn btn-small',
        href: '#/p/' + enc(pid) + '/chapters/' + chapter.n,
        text: '编辑正文'
      })
    ]);
    const newLink = el('a', {
      class: 'btn btn-primary',
      href: '#/p/' + enc(pid) + '/chapters/new',
      text: '新建章节'
    });
    return [
      pageHead('章节', '每次保存都会生成新版本；旧版本仍可在编辑器中查看与对比。', newLink),
      card('章节列表（' + (list || []).length + '）',
        dataTable(['章号', '标题', '状态', '最新版本', '字数', '操作'], rows, '还没有章节，点击右上角「新建章节」。'))
    ];
  }

  async function pageChapterEditor(pid, rawN) {
    const isNew = rawN === undefined || rawN === 'new';
    let n = null;
    let chapter = null;
    if (!isNew) {
      n = Number(rawN);
      if (!Number.isInteger(n) || n < 0) throw new Error('章号不合法：' + rawN);
      chapter = await api('/api/projects/' + enc(pid) + '/chapters/' + n);
    }
    const base = '/api/projects/' + enc(pid) + '/chapters' + (n === null ? '' : '/' + n);

    const exists = !!chapter;
    const nInput = el('input', {
      type: 'number', min: '0', step: '1', required: true,
      value: n === null ? '' : String(n)
    });
    if (!isNew) nInput.disabled = true;
    const titleInput = el('input', { type: 'text', maxlength: 300, value: chapter ? chapter.title : '' });
    const statusSel = selectEl(opts(STATUS_LABELS), chapter ? chapter.status : 'draft');
    const area = el('textarea', { class: 'editor-area', rows: '18', spellcheck: 'false' });
    area.value = chapter ? chapter.content : '';
    const liveCount = el('p', { id: 'liveWordCount', class: 'hint' });
    function updateLiveCount() {
      const compact = area.value.replace(/\s+/g, '').length;
      const raw = area.value.length;
      const paragraphs = area.value.split(/\n\s*\n/).filter((part) => part.trim()).length;
      const approx = Math.floor(raw / 4);
      liveCount.textContent = '当前正文：' + compact + ' 字（去空白含标点）· '
        + raw + ' 字符 · ' + paragraphs + ' 段 · 约 ' + approx + ' tokens';
    }
    updateLiveCount();
    area.addEventListener('input', updateLiveCount);
    const dirty = el('p', {
      id: 'chapterDirty',
      class: 'hint',
      text: n === null
        ? '新章节：填写章号后保存。'
        : (exists ? '当前为最新版本 v' + chapter.version + '。' : '该章还没有版本，保存后创建 v1。')
    });
    let isDirty = false;
    let viewedVersion = exists ? chapter.version : null;
    let invalidatePatchPreview = () => {};
    let syncPatchState = () => {};

    function markDirty() {
      isDirty = true;
      dirty.textContent = '有未保存修改，保存后生成新版本。';
      invalidatePatchPreview();
      syncPatchState();
    }
    [titleInput, area].forEach((node) => node.addEventListener('input', markDirty));
    statusSel.addEventListener('change', markDirty);

    const saveBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '保存为新版本' });
    const backLink = el('a', { class: 'btn', href: '#/p/' + enc(pid) + '/chapters', text: '返回章节列表' });

    const form = el('form', { class: 'form-grid' },
      field('章号', nInput, isNew ? '新建章节的扁平章号' : '已保存章节的章号不可更改'),
      field('标题', titleInput),
      field('状态', statusSel),
      field('正文', area, '保存会写入新版本（version + 1）', 'field-wide'),
      el('div', { class: 'field field-wide' }, liveCount),
      el('div', { class: 'field field-wide form-actions' }, saveBtn, backLink),
      el('div', { class: 'field field-wide' }, dirty));

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      let chapterN = n;
      if (isNew) {
        chapterN = Number(nInput.value);
        if (!Number.isInteger(chapterN) || chapterN < 0) {
          toast('章号必须是非负整数', 'error');
          nInput.focus();
          return;
        }
      }
      if (!area.value.trim()) {
        toast('正文为空，请先填写内容', 'error');
        area.focus();
        return;
      }
      await withBusy(saveBtn, async () => {
        const result = await api('/api/projects/' + enc(pid) + '/chapters', {
          method: 'POST',
          body: {
            n: chapterN,
            title: titleInput.value.trim() || ('第' + chapterN + '章'),
            content: area.value,
            status: statusSel.value
          }
        });
        toast('已保存为 v' + result.version, 'success');
        if (isNew) location.hash = '#/p/' + enc(pid) + '/chapters/' + result.n;
        else render();
      });
    }));

    const nodes = [pageHead(isNew ? '新建章节' : '第 ' + n + ' 章', chapter ? chapter.title : '填写章号与正文后保存', backLink)];
    nodes.push(card('章节信息', form));

    // 版本
    const versionBox = el('div');
    if (isNew || !exists) {
      versionBox.append(el('p', { class: 'muted', text: '保存第一个版本后即可在此查看历史与差异。' }));
    } else {
      const versions = await api(base + '/versions');
      const list = Array.isArray(versions) ? versions : [];
      const versionOptions = list.map((item) => [
        String(item.version),
        'v' + item.version + '（' + item.words + ' 字 · ' + (STATUS_LABELS[item.status] || item.status) + '）'
      ]);
      const versionSel = selectEl(versionOptions, String(chapter.version));
      const loadBtn = el('button', { class: 'btn', type: 'button', text: '载入该版本' });
      loadBtn.addEventListener('click', handler(async () => {
        await withBusy(loadBtn, async () => {
          const data = await api(base + '?version=' + enc(versionSel.value));
          if (!data) {
            toast('该版本不存在', 'error');
            return;
          }
          titleInput.value = data.title;
          area.value = data.content;
          statusSel.value = data.status;
          isDirty = false;
          viewedVersion = data.version;
          const latest = Number(versionSel.value) === chapter.version;
          dirty.textContent = '正在查看 v' + data.version + (latest ? '（最新）' : '（历史版本，保存将生成新版本）');
          invalidatePatchPreview();
          syncPatchState();
          area.focus();
        });
      }));

      const aSel = selectEl(versionOptions, versionOptions.length ? versionOptions[0][0] : '');
      const bSel = selectEl(versionOptions, versionOptions.length ? versionOptions[versionOptions.length - 1][0] : '');
      const diffBtn = el('button', { class: 'btn', type: 'button', text: '对比版本' });
      const diffBox = el('div', { class: 'diff-box' });
      if (list.length < 2) diffBtn.disabled = true;
      diffBtn.addEventListener('click', handler(async () => {
        await withBusy(diffBtn, async () => {
          const result = await api(base + '/diff?a=' + enc(aSel.value) + '&b=' + enc(bSel.value));
          diffBox.replaceChildren(
            el('p', { class: 'hint', text: 'v' + result.from_version + ' → v' + result.to_version + ' · 新增 ' + result.added_lines + ' 行 / 删除 ' + result.removed_lines + ' 行' + (result.truncated ? '（已截断）' : '') }),
            renderUnifiedDiff(result.diff));
        });
      }));

      const rows = list.map((item) => [
        'v' + item.version,
        STATUS_LABELS[item.status] || item.status,
        String(item.words) + ' 字',
        formatTime(item.created_at)
      ]);

      versionBox.append(
        el('div', { class: 'inline-form' },
          field('版本下拉', versionSel), loadBtn),
        el('div', { class: 'inline-form' },
          field('起点版本', aSel), field('终点版本', bSel), diffBtn),
        ...(list.length < 2
          ? [el('p', { class: 'hint', text: '只有 1 个版本，保存一次修订后即可对比。' })]
          : []),
        dataTable(['版本', '状态', '篇幅', '时间'], rows, '无版本记录'),
        diffBox);
    }
    nodes.push(card('版本', versionBox));

    // 局部改稿：与 MCP 的 chapter_patch 共用同一条 API/Store 路径。
    // 手填「查找原文 → 替换为」并预览，确认后才生成新版本；不会模糊匹配。
    if (exists) {
      const patchFind = el('textarea', {
        id: 'patchFind', rows: '3', spellcheck: 'false',
        placeholder: '要改的原文，必须与正文逐字一致（含标点、换行）'
      });
      const patchReplace = el('textarea', {
        id: 'patchReplace', rows: '3', spellcheck: 'false',
        placeholder: '替换后的文字；留空表示删除这段原文'
      });
      const patchOccurrence = el('input', {
        id: 'patchOccurrence', type: 'number', min: '1', step: '1', value: '1'
      });
      const patchAll = el('input', { id: 'patchAll', type: 'checkbox' });
      const patchState = el('p', { id: 'patchState', class: 'hint' });
      const patchPreviewBox = el('div', { id: 'patchCompare', class: 'patch-preview' });
      let patchPreview = null;

      const takeFindBtn = el('button', { class: 'btn', type: 'button', text: '取选区到查找框' });
      const takeReplaceBtn = el('button', { class: 'btn', type: 'button', text: '取选区到替换框' });
      const previewBtn = el('button', { id: 'patchPreview', class: 'btn', type: 'button', text: '预览改动' });
      const applyBtn = el('button', { id: 'patchApply', class: 'btn btn-primary', type: 'button', text: '应用并生成新版本' });
      applyBtn.disabled = true;

      function canPatch() {
        return !isDirty && viewedVersion === chapter.version;
      }

      function invalidate() {
        patchPreview = null;
        patchPreviewBox.replaceChildren();
        applyBtn.disabled = true;
      }

      invalidatePatchPreview = invalidate;
      syncPatchState = function () {
        const ready = canPatch();
        previewBtn.disabled = !ready;
        applyBtn.disabled = !ready || !patchPreview;
        if (isDirty) {
          patchState.textContent = '正文有未保存修改：请先「保存为新版本」，再基于最新版做局部改稿，避免覆盖你刚写的内容。';
        } else if (viewedVersion !== chapter.version) {
          patchState.textContent = '正在查看历史版本 v' + viewedVersion + '；局部改稿只作用于最新版 v' + chapter.version + '，请先载入最新版。';
        } else {
          patchState.textContent = '将基于最新版 v' + chapter.version + ' 精确替换，应用后生成 v' + (chapter.version + 1) + '；旧版本仍可查看和对比。先预览、再应用。';
        }
        syncRevisionState();
      };

      function takeSelection(target) {
        const selected = area.value.slice(area.selectionStart, area.selectionEnd);
        if (!selected) {
          toast('请先在正文中选中一段文字', 'error');
          area.focus();
          return;
        }
        target.value = selected;
        invalidate();
        syncPatchState();
        target.focus();
      }
      takeFindBtn.addEventListener('click', () => takeSelection(patchFind));
      takeReplaceBtn.addEventListener('click', () => takeSelection(patchReplace));

      [patchFind, patchReplace, patchOccurrence].forEach((node) => {
        node.addEventListener('input', invalidate);
      });
      patchAll.addEventListener('change', () => {
        patchOccurrence.disabled = patchAll.checked;
        invalidate();
        syncPatchState();
      });

      function patchBody(preview) {
        const occurrence = Number(patchOccurrence.value);
        if (!Number.isInteger(occurrence) || occurrence < 1) {
          throw new Error('「第几处」必须是正整数');
        }
        return {
          find: patchFind.value,
          replace: patchReplace.value,
          occurrence: occurrence,
          all_matches: patchAll.checked,
          expected_version: chapter.version,
          preview: preview
        };
      }

      function renderPatchPreview(result) {
        const delta = result.delta_chars;
        const stats = '命中 ' + result.matches + ' 处；本次替换 ' + result.replaced
          + ' 处；' + result.chars_before + ' 字 → ' + result.chars_after
          + ' 字（' + (delta >= 0 ? '+' : '') + delta + '）';
        patchPreviewBox.replaceChildren(
          el('p', { class: 'hint', text: stats }),
          el('div', { class: 'patch-compare' },
            el('div', { class: 'patch-pane' },
              el('p', { class: 'patch-label', text: '改前（上下文）' }),
              el('pre', { class: 'patch-excerpt', text: result.before_excerpt })),
            el('div', { class: 'patch-pane' },
              el('p', { class: 'patch-label', text: '改后（上下文）' }),
              el('pre', { class: 'patch-excerpt', text: result.after_excerpt })),
            ),
          el('p', { class: 'hint', text: '确认无误后点「应用并生成新版本」；应用不会再弹确认框，但会保留旧版本可回退。' })
        );
      }

      previewBtn.addEventListener('click', handler(async () => {
        if (!canPatch()) {
          syncPatchState();
          toast('请先保存当前修改，或载入最新版本', 'error');
          return;
        }
        if (patchFind.value === '') {
          toast('请填写要查找的原文', 'error');
          patchFind.focus();
          return;
        }
        let body;
        try {
          body = patchBody(true);
        } catch (err) {
          toast(err.message, 'error');
          patchOccurrence.focus();
          return;
        }
        await withBusy(previewBtn, async () => {
          const result = await api(base + '/patch', { method: 'POST', body: body });
          patchPreview = JSON.parse(JSON.stringify(body));
          patchPreview.from_version = result.from_version;
          renderPatchPreview(result);
          applyBtn.disabled = false;
        });
      }));

      applyBtn.addEventListener('click', handler(async () => {
        if (!patchPreview || !canPatch()) {
          invalidate();
          syncPatchState();
          toast('请先预览，并确认当前正文没有未保存修改', 'error');
          return;
        }
        await withBusy(applyBtn, async () => {
          const body = Object.assign({}, patchPreview, { preview: false });
          const result = await api(base + '/patch', { method: 'POST', body: body });
          toast('局部改动已应用，生成 v' + result.to_version, 'success');
          render();
        });
      }));

      nodes.push(card('局部改稿（人手 / 模型共用）',
        el('p', { class: 'hint', text: '把正文里的一段原文精确替换成新文字，其余内容保持不动。可以手填，也可以先在正文里选中一段再点「取选区」。这里改的是已保存的最新版；应用前必须先预览。' }),
        el('div', { class: 'inline-form' },
          field('第几处', patchOccurrence),
          el('label', { class: 'check-line' }, patchAll, el('span', { text: '替换全部命中' })),
          takeFindBtn, takeReplaceBtn),
        el('div', { class: 'field-list' },
          field('查找原文（必填）', patchFind),
          field('替换为（可留空表示删除）', patchReplace)),
        el('div', { class: 'inline-form form-actions' }, previewBtn, applyBtn),
        patchState,
        patchPreviewBox));

      // 修订建议：规则体检，不调用模型；只有唯一可定位的超长段落会给补丁。
      const revisionRun = el('button', { id: 'revisionRun', class: 'btn btn-primary', type: 'button', text: '体检当前最新版' });
      const revisionState = el('p', { id: 'revisionState', class: 'hint' });
      const revisionBox = el('div', { id: 'revisionPlan', class: 'revision-list' });
      function syncRevisionState() {
        const ready = canPatch();
        revisionRun.disabled = !ready;
        if (isDirty) {
          revisionState.textContent = '正文有未保存修改：先保存为新版本，再按最新版体检。';
        } else if (viewedVersion !== chapter.version) {
          revisionState.textContent = '正在查看历史版本；体检只读最新版 v' + chapter.version + '。';
        } else {
          revisionState.textContent = '只做规则体检，不是文学评分；建议不会自动改正文。';
        }
      }
      function renderRevision(result) {
        const stats = result.stats || {};
        const metricPairs = [
          ['字数', stats.chars || 0],
          ['段落', stats.paragraphs || 0],
          ['句子', stats.sentences || 0],
          ['超长段落', stats.long_paragraphs || 0],
          ['可补丁', stats.patchable || 0]
        ];
        const out = [el('div', { class: 'metric-grid' }, metricPairs.map((pair) =>
          el('div', { class: 'metric' },
            el('span', { class: 'metric-value', text: String(pair[1]) }),
            el('span', { class: 'metric-label', text: pair[0] })))),
          el('p', { class: 'hint', text: ({
            green: '绿色 · 未发现明显结构问题',
            yellow: '黄色 · 有可改项',
            red: '红色 · 优先处理',
            gray: '灰色 · 结构信号不足'
          }[result.verdict] || result.verdict || '规则体检') + (result.note ? ' ' + result.note : '') })];
        const issues = result.issues || [];
        if (!issues.length) {
          out.push(emptyState('这一版没有触发规则项。'));
        }
        for (const issue of issues) {
          const tone = issue.severity === 'high' ? 'danger' : (issue.severity === 'medium' ? 'warn' : 'muted');
          const head = el('div', { class: 'revision-head' },
            badge({ high: '高', medium: '中', low: '低' }[issue.severity] || issue.severity, tone),
            el('strong', { text: issue.title }),
            el('code', { text: issue.id }));
          const body = el('div', { class: 'revision-body' },
            el('p', { class: 'hint', text: '证据' }),
            el('pre', { class: 'patch-excerpt', text: issue.evidence || '（无）' }),
            el('p', { class: 'hint', text: '为什么' }),
            el('p', { text: issue.why || '—' }),
            el('p', { class: 'hint', text: '怎么改' }),
            el('p', { text: issue.suggestion || '—' }));
          if (issue.patchable && issue.patch) {
            const fill = el('button', { class: 'btn btn-small', type: 'button', text: '填入局部改稿' });
            fill.addEventListener('click', () => {
              patchFind.value = issue.patch.find;
              patchReplace.value = issue.patch.replace;
              patchOccurrence.value = String(issue.patch.occurrence || 1);
              patchAll.checked = false;
              patchOccurrence.disabled = false;
              invalidate();
              syncPatchState();
              toast('已填入查找/替换框；请先预览，再应用。', 'success');
              patchFind.focus();
              patchFind.scrollIntoView({ behavior: 'smooth', block: 'center' });
            });
            body.append(el('div', { class: 'form-actions' }, fill));
          }
          out.push(el('article', { class: 'revision-issue' }, head, body));
        }
        revisionBox.replaceChildren(...out);
      }
      revisionRun.addEventListener('click', handler(async () => {
        if (!canPatch()) {
          syncRevisionState();
          toast('请先保存当前修改，或载入最新版本', 'error');
          return;
        }
        await withBusy(revisionRun, async () => {
          const result = await api(base + '/revision-plan', {
            method: 'POST', body: { max_items: 8 }
          });
          renderRevision(result);
        });
      }));
      syncRevisionState();
      nodes.push(card('修订建议（规则体检）',
        el('p', { class: 'hint', text: '从结构、段落长度、重复句与章节篇幅里找可执行问题；每项给证据、原因和改法。只有唯一可定位的超长段落会生成“拆段”补丁，填入上方局部改稿后仍需预览确认。' }),
        el('div', { class: 'inline-form form-actions' }, revisionRun),
        revisionState,
        revisionBox));
      syncPatchState();
    }

    // 润色 / 生成
    const instruction = el('input', {
      type: 'text',
      value: '润色以下文字，保持原意与文风，使表达更凝练。',
      maxlength: 300
    });
    const polishBtn = el('button', { class: 'btn', type: 'button', text: '润色选中' });
    const wholeBtn = el('button', { class: 'btn', type: 'button', text: '按指令处理整章' });
    const llmHint = el('p', { class: 'hint', text: '结果会先在弹窗中展示，点「替换」或「插入」后才写入正文，且不会自动保存。' });

    async function askLLM(mode, button) {
      const start = area.selectionStart;
      const end = area.selectionEnd;
      const selected = area.value.slice(start, end);
      if (mode === 'selection' && !selected.trim()) {
        toast('请先在正文中选中一段文字', 'error');
        area.focus();
        return;
      }
      if (mode === 'whole' && !area.value.trim()) {
        toast('正文为空，没有可处理的内容', 'error');
        area.focus();
        return;
      }
      const source = mode === 'selection' ? selected : area.value;
      const prompt = (instruction.value.trim() || '润色以下文字。')
        + '\n\n' + (mode === 'selection' ? '【选中片段】\n' : '【整章正文】\n') + source;
      await withBusy(button, async () => {
        const result = await api('/api/llm/generate', {
          method: 'POST',
          body: {
            prompt: prompt,
            system: '你是资深的中文长篇小说编辑，只输出处理后的正文，不要解释。'
          }
        });
        openLLMResult(result, mode, start, end);
      });
    }

    function openLLMResult(result, mode, start, end) {
      const text = (result && result.text) || '';
      const usage = result && result.usage
        ? (result.usage.total_tokens !== undefined ? result.usage.total_tokens : '')
        : '';
      const info = el('p', { class: 'hint', text: '模型：' + ((result && result.model) || '—')
        + ' · 服务：' + ((result && result.provider) || '—')
        + (usage !== '' ? ' · tokens：' + usage : '') });
      const output = el('pre', { class: 'llm-output', text: text || '（模型返回空文本）' });
      const closeBtn = el('button', { class: 'btn', type: 'button', text: '关闭' });
      closeBtn.addEventListener('click', () => closeModal());
      const insertBtn = el('button', { class: 'btn', type: 'button', text: '插入' });
      const replaceBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '替换' });
      if (!text) {
        insertBtn.disabled = true;
        replaceBtn.disabled = true;
      }
      insertBtn.addEventListener('click', () => {
        const position = mode === 'selection' ? end : area.value.length;
        area.value = area.value.slice(0, position) + text + area.value.slice(position);
        markDirty();
        closeModal();
        area.focus();
        toast('已插入编辑器，请记得保存', 'success');
      });
      replaceBtn.addEventListener('click', () => {
        if (mode === 'selection') {
          area.value = area.value.slice(0, start) + text + area.value.slice(end);
          area.selectionStart = start;
          area.selectionEnd = start + text.length;
        } else {
          area.value = text;
        }
        markDirty();
        closeModal();
        area.focus();
        toast('已替换编辑器内容，请记得保存', 'success');
      });
      openModal({
        title: '模型结果',
        wide: true,
        body: [info, output],
        actions: [closeBtn, insertBtn, replaceBtn]
      });
    }

    polishBtn.addEventListener('click', handler(() => askLLM('selection', polishBtn)));
    wholeBtn.addEventListener('click', handler(() => askLLM('whole', wholeBtn)));

    nodes.push(card('润色 / 生成',
      el('div', { class: 'inline-form' }, field('指令', instruction), polishBtn, wholeBtn),
      llmHint));
    return nodes;
  }

  // ---------------------------------------------------------------- 素材与知识库
  function materialCard(pid, material, showSnippet, reload, evidence, pack) {
    const data = material.data_json && typeof material.data_json === 'object' ? material.data_json : {};
    const categoryKey = material.category || data.category || '';
    const categoryMeta = pack && Array.isArray(pack.categories)
      ? pack.categories.find((item) => item.key === categoryKey) : null;
    const categoryLabel = categoryMeta ? categoryMeta.name : categoryKey;
    const useHint = material.use_hint || data.use_hint || '';
    const synonymTerms = Array.isArray(material.synonym_terms) ? material.synonym_terms : [];

    const head = el('div', { class: 'material-head' },
      el('h3', { class: 'material-title', text: material.title || '（无标题）' }),
      badge(MATERIAL_KIND_LABELS[material.kind] || material.kind || '素材'),
      categoryLabel ? badge(categoryLabel, 'muted') : null,
      material.source_kind ? badge(SOURCE_KIND_LABELS[material.source_kind] || material.source_kind) : null,
      evidence ? badge(evidence.level + ' ' + evidence.label,
        evidence.level === 'E0' ? 'warn' : (evidence.level === 'E3' ? 'ok' : null)) : null);
    const cite = el('button', { class: 'btn btn-small', type: 'button', text: '登记引证' });
    const edit = el('button', { class: 'btn btn-small', type: 'button', text: '补正文 / 证据' });
    const remove = el('button', { class: 'btn btn-small btn-danger', type: 'button', text: '删除' });
    head.append(cite, edit, remove);

    const metaBits = [el('span', { text: '来源：' + (material.source || '未标注') })];
    if (material.chapter !== null && material.chapter !== undefined) {
      metaBits.push(el('span', { text: '第 ' + material.chapter + ' 章' }));
    }
    if (material.source_kind) {
      metaBits.push(el('span', { text: '类型：' + (SOURCE_KIND_LABELS[material.source_kind] || material.source_kind) }));
    }
    metaBits.push(el('code', { text: material.id }));
    const meta = el('div', { class: 'material-meta' }, metaBits);

    const body = el('div');
    if (showSnippet) {
      const snippet = el('p', { class: 'snippet' });
      snippet.innerHTML = highlighted(material.snippet || '');
      body.append(snippet);
    } else {
      const content = material.content || '';
      const shown = content.length > 320 ? content.slice(0, 320) + '…' : content;
      body.append(el('p', { class: 'snippet', text: shown || '（无正文）' }));
    }
    if (evidence) {
      body.append(el('p', { class: 'hint', text: evidence.reason + '；下一步：' + evidence.next_action }));
    }

    const hints = [];
    if (synonymTerms.length) {
      hints.push(el('p', { class: 'hint', text: '同义词召回：' + synonymTerms.join('、') }));
    }
    if (useHint) {
      hints.push(el('p', { class: 'hint', text: '怎么用：' + useHint }));
    }
    if (hints.length) body.append(...hints);

    const tags = (material.tags || []).length
      ? el('div', { class: 'tags' }, material.tags.map((tag) => el('span', { class: 'tag', text: tag })))
      : null;

    cite.addEventListener('click', () => {
      const claimDefault = (material.title || (material.snippet || '').replace(/<[^>]+>/g, '') || '').trim();
      const chapterInput = el('input', { type: 'number', min: '1', step: '1', placeholder: '留空表示不限章' });
      const claimInput = el('textarea', { rows: '3' });
      claimInput.value = claimDefault;
      const status = el('p', { class: 'hint', text: '来源固定为素材 id ' + material.id + '；登记后可在引证接口验真。' });
      const save = el('button', { class: 'btn btn-primary', type: 'button', text: '登记引证' });
      save.addEventListener('click', handler(async () => {
        if (!claimInput.value.trim()) { toast('请填写引证断言', 'error'); claimInput.focus(); return; }
        await withBusy(save, async () => {
          const payload = { claim: claimInput.value.trim(), source: material.id };
          if (chapterInput.value !== '') payload.chapter_n = Number(chapterInput.value);
          const out = await api('/api/projects/' + enc(pid) + '/citations', { method: 'POST', body: payload });
          status.textContent = '已登记引证 citation_id：' + out.id;
          toast('已登记引证 ' + out.id, 'success');
        });
      }));
      openModal({
        title: '登记引证 · ' + (material.title || material.id),
        body: [
          el('p', { class: 'hint', text: '把素材里的结论登记为可回溯的引证；断言可稍后在同一条记录上验真。' }),
          field('断言', claimInput),
          field('章号（可选）', chapterInput),
          status
        ],
        actions: [save]
      });
    });

    edit.addEventListener('click', () => {
      const data = material.data_json && typeof material.data_json === 'object' ? material.data_json : {};
      const titleInput = el('input', { type: 'text', maxlength: 300, value: material.title || '' });
      const contentInput = el('textarea', { rows: 12, spellcheck: 'false' });
      contentInput.value = material.content || '';
      const sourceInput = selectEl([
        ['', '未标记正文来源'], ['user_paste', '作者粘贴正文'], ['ima_export', 'IMA 客户端导出'],
        ['file_import', '本地文件导入']
      ], data.content_source || (material.source_kind === 'ima' ? 'user_paste' : ''));
      const verifiedInput = selectEl([
        ['', '未记录核验状态'], ['unverified', '尚未核原文'], ['verified', '已回原文核对']
      ], data.verification_status || '');
      const corroboratedInput = el('textarea', { rows: 3,
        placeholder: '可选：独立交叉来源，每行一个 URL / 文件名 / 出处' });
      corroboratedInput.value = Array.isArray(data.corroborated_by)
        ? data.corroborated_by.join('\n') : (data.corroborated_by || '');
      const save = el('button', { class: 'btn btn-primary', type: 'button', text: '保存并重新审计' });
      save.addEventListener('click', handler(async () => {
        if (!titleInput.value.trim()) { toast('标题不能为空', 'error'); return; }
        await withBusy(save, async () => {
          const merged = Object.assign({}, data, {
            content_source: sourceInput.value,
            verification_status: verifiedInput.value,
            corroborated_by: corroboratedInput.value.split('\n').map((x) => x.trim()).filter(Boolean)
          });
          if (contentInput.value.trim() && data.catalog_only) merged.catalog_only = false;
          await api('/api/projects/' + enc(pid) + '/materials/' + enc(material.id), {
            method: 'PATCH',
            body: { title: titleInput.value.trim(), content: contentInput.value, data: merged }
          });
          closeModal();
          toast('素材已更新；证据等级按可核查程度重新计算', 'success');
          reload();
        });
      }));
      openModal({
        title: '补正文 / 证据 · ' + (material.title || material.id), wide: true,
        body: [
          el('p', { class: 'hint', text: '原始 source 不在此修改，避免把题录来源伪造成正文来源。E2 需要正文或原文核验记录；E3 还需要独立交叉来源。' }),
          field('标题', titleInput), field('正文', contentInput),
          el('div', { class: 'inline-form' }, field('正文来源', sourceInput), field('核验状态', verifiedInput)),
          field('独立交叉来源', corroboratedInput)
        ],
        actions: [save]
      });
    });

    remove.addEventListener('click', handler(async () => {
      const answer = prompt('请输入素材 id「' + material.id + '」以确认删除：');
      if (answer === null) return;
      if (answer !== material.id) {
        toast('确认串不匹配，已取消删除', 'error');
        return;
      }
      await withBusy(remove, async () => {
        await api('/api/projects/' + enc(pid) + '/materials/' + enc(material.id) + '?confirm=' + enc(material.id), { method: 'DELETE' });
        toast('已删除素材 ' + material.id, 'success');
        reload();
      });
    }));

    return el('article', { class: 'material' }, head, meta, tags, body);
  }

  async function pageMaterials(pid) {
    const base = '/api/projects/' + enc(pid);
    let lastParams = new URLSearchParams();
    let activeCategory = '';
    let categoryPack = { categories: [], synonyms: {} };
    try {
      const got = await api(base + '/materials/categories', { quiet: true });
      if (got && Array.isArray(got.categories)) categoryPack = got;
    } catch (error) {
      if (!(error instanceof ApiError)) reportError(error);
    }
    const listBox = el('div', { class: 'list-box' }, el('p', { class: 'loading', text: '加载中…' }));
    const auditBox = el('div', {}, el('p', { class: 'loading', text: '读取证据审计…' }));

    async function load(params) {
      lastParams = params || new URLSearchParams();
      const qs = lastParams.toString();
      listBox.replaceChildren(el('p', { class: 'loading', text: '加载中…' }));
      try {
        const results = await Promise.all([
          api(base + '/materials' + (qs ? '?' + qs : '')),
          api(base + '/materials/audit')
        ]);
        const data = results[0];
        const audit = results[1] || { items: [], counts: {} };
        const items = Array.isArray(data) ? data : [];
        const searching = !!lastParams.get('query');
        const evidenceById = new Map((audit.items || []).map((item) => [item.id, item]));
        const counts = audit.counts || {};
        const needs = (audit.items || []).filter((item) => item.level === 'E0' || item.level === 'E1').slice(0, 6);
        auditBox.replaceChildren(
          el('div', { class: 'inline-form' },
            ['E0', 'E1', 'E2', 'E3'].map((level) => badge(level + ' ' + (counts[level] || 0),
              level === 'E0' && counts[level] ? 'warn' : (level === 'E3' && counts[level] ? 'ok' : null))),
            el('span', { class: 'muted', text: 'E2 及以上 ' + (audit.e2_plus || 0) + ' 条' })),
          el('p', { class: 'hint', text: audit.audit_scope || '等级只表示可核查程度。' }),
          dataTable(['素材', '等级', '下一步'], needs.map((item) => [
            item.title || item.id, item.level + ' ' + item.label, item.next_action
          ]), '没有待补正文或待核原文的素材'));
        if (!items.length) {
          listBox.replaceChildren(emptyState(searching ? '没有匹配的素材' : '还没有素材，可在下方新建或导入。'));
          return;
        }
        listBox.replaceChildren(...items.map((item) => materialCard(
          pid, item, searching, () => load(lastParams), evidenceById.get(item.id),
          categoryPack)));
      } catch (error) {
        if (!(error instanceof ApiError)) reportError(error);
        listBox.replaceChildren(emptyState('素材加载失败'));
        auditBox.replaceChildren(emptyState('证据审计读取失败'));
      }
    }

    const queryInput = el('input', { type: 'search', placeholder: '关键词，如：铜钥匙' });
    const kindFilter = selectEl(opts(MATERIAL_KIND_LABELS, '全部类型'), '');
    const tagFilter = el('input', { type: 'text', placeholder: '标签', maxlength: 40 });
    const searchBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '检索' });
    const clearBtn = el('button', { class: 'btn', type: 'button', text: '清空条件' });
    const searchForm = el('form', { class: 'inline-form' },
      field('关键词', queryInput),
      field('类型', kindFilter),
      field('标签', tagFilter),
      searchBtn, clearBtn);

    const chipsBox = el('div', { class: 'chips' });
    function buildParams() {
      const params = new URLSearchParams();
      if (queryInput.value.trim()) params.set('query', queryInput.value.trim());
      if (kindFilter.value) params.set('kind', kindFilter.value);
      if (tagFilter.value.trim()) params.set('tag', tagFilter.value.trim());
      if (activeCategory) params.set('category', activeCategory);
      return params;
    }
    function drawChips() {
      const entries = [{ key: '', name: '全部', hint: '不按素材地图过滤。' }]
        .concat(categoryPack.categories || []);
      chipsBox.replaceChildren(...entries.map((item) => {
        const chip = el('button', {
          type: 'button',
          class: 'chip' + (activeCategory === (item.key || '') ? ' is-active' : ''),
          text: item.name || item.key,
          title: item.hint || ''
        });
        chip.addEventListener('click', handler(async () => {
          activeCategory = item.key || '';
          drawChips();
          await load(buildParams());
        }));
        return chip;
      }));
    }
    drawChips();

    clearBtn.addEventListener('click', handler(async () => {
      queryInput.value = '';
      kindFilter.value = '';
      tagFilter.value = '';
      activeCategory = '';
      drawChips();
      await load(new URLSearchParams());
    }));
    searchForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      await load(buildParams());
    }));

    // 新建素材
    const mTitle = el('input', { type: 'text', maxlength: 300, required: true });
    const mKind = selectEl(opts(MATERIAL_KIND_LABELS), 'reference');
    const mContent = el('textarea', { rows: '5', spellcheck: 'false' });
    const mSource = el('input', { type: 'text', maxlength: 500, placeholder: '出处 / 文件名 / 链接说明' });
    const mSourceKind = selectEl(opts(SOURCE_KIND_LABELS), 'other');
    const mTags = el('input', { type: 'text', maxlength: 200, placeholder: '多个标签用逗号分隔' });
    const mChapter = el('input', { type: 'number', min: '0', step: '1' });
    const addBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '登记素材' });
    const addForm = el('form', { class: 'form-grid' },
      field('标题', mTitle),
      field('类型', mKind),
      field('来源', mSource),
      field('来源类型', mSourceKind),
      field('标签', mTags),
      field('关联章号（可选）', mChapter),
      field('正文', mContent, '单条素材上限 20000 字符', 'field-wide'),
      el('div', { class: 'field field-wide form-actions' }, addBtn));
    addForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      if (!mTitle.value.trim()) {
        toast('请填写素材标题', 'error');
        mTitle.focus();
        return;
      }
      const tags = mTags.value.split(',').map((item) => item.trim()).filter(Boolean);
      const body = {
        title: mTitle.value.trim(),
        kind: mKind.value,
        content: mContent.value,
        source: mSource.value.trim(),
        source_kind: mSourceKind.value,
        tags: tags
      };
      if (mChapter.value !== '') body.chapter = Number(mChapter.value);
      await withBusy(addBtn, async () => {
        await api(base + '/materials', { method: 'POST', body: body });
        toast('素材已登记', 'success');
        addForm.reset();
        mKind.value = 'reference';
        mSourceKind.value = 'other';
        load(lastParams);
      });
    }));

    // 导入
    const importTitle = el('input', { type: 'text', maxlength: 300, placeholder: '素材标题' });
    const importText = el('textarea', { rows: '6', spellcheck: 'false', placeholder: '粘贴要导入的文本（Markdown / 纯文本）' });
    const importStatus = el('p', { class: 'hint', text: '导出的长文会按标题与长度自动分块。' });
    const importBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '导入文本' });
    const fileInput = el('input', { type: 'file', accept: '.md,.txt' });

    async function runImport(extra, trigger) {
      if (!importText.value.trim()) {
        toast('请粘贴文本或选择文件', 'error');
        importText.focus();
        return;
      }
      await withBusy(trigger || importBtn, async () => {
        const body = {
          title: importTitle.value.trim() || '未命名素材',
          content: importText.value,
          kind: 'reference'
        };
        if (extra) {
          if (extra.source) body.source = extra.source;
          if (extra.source_kind) body.source_kind = extra.source_kind;
        } else {
          body.source = 'webui-paste';
          body.source_kind = 'other';
        }
        const result = await api(base + '/kb/import', { method: 'POST', body: body });
        importStatus.textContent = '已导入 ' + result.count + ' 条素材。';
        toast('已导入 ' + result.count + ' 条素材', 'success');
        importText.value = '';
        importTitle.value = '';
        fileInput.value = '';
        load(lastParams);
      });
    }

    importBtn.addEventListener('click', handler(() => runImport(null, importBtn)));
    fileInput.addEventListener('change', handler(async () => {
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      const text = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error('文件读取失败'));
        reader.readAsText(file, 'utf-8');
      });
      importTitle.value = file.name.replace(/\.[^.]+$/, '');
      importText.value = text;
      importStatus.textContent = '已读取 ' + text.length + ' 字符，正在导入…';
      await runImport({ source: file.name, source_kind: 'file' }, importBtn);
    }));

    const importCard = card('导入素材（粘贴文本或选择文件）',
      el('div', { class: 'form-grid' },
        field('标题', importTitle, null, 'field-wide'),
        field('文本', importText, '文件只在浏览器内读取，不会上传文件本体', 'field-wide'),
        field('或选择本地文件（.md / .txt）', fileInput, '选择后会读取文本并立即导入', 'field-wide')),
      el('div', { class: 'form-actions' }, importBtn),
      importStatus);

    // IMA
    const imaBox = el('div', {}, el('p', { class: 'loading', text: '检查 IMA 配置…' }));
    async function initIma() {
      imaBox.replaceChildren(el('p', { class: 'loading', text: '检查 IMA 配置…' }));
      try {
        const status = await api('/api/ima/status', { quiet: true });
        if (!status.configured) {
          const enableBtn = el('button', { class: 'btn', type: 'button', text: '启用 IMA（使用本机凭证）' });
          enableBtn.addEventListener('click', handler(async () => {
            await withBusy(enableBtn, async () => {
              await api('/api/settings', { method: 'PUT', body: { ima: { enabled: true } } });
              toast('IMA 已启用，正在重新检测凭证', 'success');
              await initIma();
            });
          }));
          imaBox.replaceChildren(...[
            el('p', { class: 'muted', text: status.note || '未配置 IMA；可继续使用本地知识库' }),
            status.channel === 'disabled' ? el('div', { class: 'inline-form' },
              enableBtn,
              el('span', { class: 'hint', text: '启用后会读取 ~/.config/ima 里的 client_id / api_key；不会把密钥写进页面。' })) : null,
            el('p', { class: 'hint' }, '也可以到', el('a', { href: '#/settings', text: '设置页' }), '填写 IMA 的接口地址与密钥。')
          ].filter(Boolean));
          return;
        }

        let kbs = [];
        try {
          const kbData = await api('/api/ima/kbs', { quiet: true });
          kbs = Array.isArray(kbData.items) ? kbData.items : [];
        } catch (error) { kbs = []; }
        const kbSelect = selectEl([['', '全部 / 已配置库']].concat(
          kbs.map((kb) => [kb.id, kb.name || kb.id])), '');
        const q = el('input', { type: 'search', placeholder: '在 IMA 知识库中检索（订阅库只返回标题）' });
        const go = el('button', { class: 'btn', type: 'button', text: '检索 IMA' });
        const results = el('div', { class: 'stack' });
        const searchForm = el('div', { class: 'inline-form' },
          field('知识库', kbSelect), field('关键词', q), go);

        async function importItem(item, content) {
          const result = await api('/api/ima/import', {
            method: 'POST',
            body: { project: pid, id: item.id, title: item.title,
              kb_id: item.kb_id || null,
              tags: ['ima'], content: content }
          });
          if (!result.configured) {
            toast(result.note || 'IMA 未配置', 'error');
            return;
          }
          toast(result.catalog_only ? '已存题录；正文请用“补正文”或客户端导出'
            : '已导入正文素材', 'success');
          load(lastParams);
        }

        function resultCard(item) {
          const importOne = el('button', { class: 'btn btn-small', type: 'button', text: '题录入库' });
          const pasteOne = el('button', { class: 'btn btn-small', type: 'button', text: '补正文' });
          importOne.addEventListener('click', handler(async () => {
            await withBusy(importOne, () => importItem(item, null));
          }));
          pasteOne.addEventListener('click', () => {
            const bodyInput = el('textarea', { rows: 14,
              placeholder: '从 IMA 客户端复制正文粘贴到这里；保存后来源仍是 ima://，但正文归你主动提供。' });
            const save = el('button', { class: 'btn btn-primary', type: 'button', text: '保存正文' });
            const clipBtn = el('button', { class: 'btn', type: 'button', text: '从剪贴板粘贴' });
            clipBtn.addEventListener('click', handler(async () => {
              try {
                const text = await navigator.clipboard.readText();
                if (!text || !text.trim()) { toast('剪贴板为空', 'error'); return; }
                bodyInput.value = text;
                toast('已从剪贴板读取正文', 'success');
              } catch (error) {
                toast('浏览器未授权剪贴板；请手动粘贴', 'error');
              }
            }));
            save.addEventListener('click', handler(async () => {
              if (!bodyInput.value.trim()) { toast('正文为空', 'error'); return; }
              await withBusy(save, async () => {
                await importItem(item, bodyInput.value);
                closeModal();
              });
            }));
            openModal({ title: '补全正文 · ' + (item.title || item.id), wide: true,
              body: [el('p', { class: 'hint', text: 'IMA 订阅库正文受客户端权限保护，OpenAPI 只返回 220030；这里接收你主动复制的内容。' }), bodyInput],
              actions: [clipBtn, save] });
          });
          return el('div', { class: 'material' },
            el('div', { class: 'material-head' },
              el('h3', { class: 'material-title', text: item.title || item.id }),
              el('div', { class: 'row-actions' }, importOne, pasteOne)),
            el('div', { class: 'material-meta' },
              el('span', { text: '库：' + (item.kb_id || '—') }),
              el('code', { text: item.id })),
            el('p', { class: 'snippet', text: item.snippet || '（订阅库只返回标题；正文需客户端导出或粘贴）' }));
        }

        go.addEventListener('click', handler(async () => {
          const query = q.value.trim();
          if (!query) { toast('请输入 IMA 检索关键词', 'error'); q.focus(); return; }
          await withBusy(go, async () => {
            results.replaceChildren(el('p', { class: 'loading', text: '检索中…' }));
            const data = await api('/api/ima/search', { method: 'POST',
              body: { query: query, kb_id: kbSelect.value || null, limit: 20 } });
            if (!data.configured) {
              results.replaceChildren(el('p', { class: 'muted', text: data.note || 'IMA 未配置' }));
              return;
            }
            const items = Array.isArray(data.items) ? data.items : [];
            results.replaceChildren(items.length
              ? el('div', { class: 'stack' }, items.map(resultCard))
              : emptyState('IMA 中没有匹配的条目'));
          });
        }));

        // ---- 写入：URL 批量导入 / 文件上传 ----
        const urlArea = el('textarea', {
          rows: '3', spellcheck: 'false',
          placeholder: '每行一个 http / https 链接，一次最多 50 条'
        });
        const urlBtn = el('button', { class: 'btn', type: 'button', text: '导入链接' });
        const urlStatus = el('p', { class: 'hint' });
        const urlRow = el('div', { class: 'upload-row' },
          el('div', { class: 'field field-wide' },
            el('label', { text: '网页链接（每行一个）' }),
            urlArea),
          el('div', { class: 'form-actions' }, urlBtn),
          urlStatus);
        urlBtn.addEventListener('click', handler(async () => {
          const urls = urlArea.value.split(/\s+/).map((item) => item.trim())
            .filter(Boolean);
          if (!urls.length) {
            toast('请先粘贴至少一个链接', 'error');
            urlArea.focus();
            return;
          }
          await withBusy(urlBtn, async () => {
            try {
              const out = await api('/api/projects/' + enc(pid) + '/ima/import-urls', {
                method: 'POST',
                body: { urls: urls, kb_id: kbSelect.value || null },
                quiet: true
              });
              urlStatus.textContent = '已提交 ' + (out.count || urls.length)
                + ' 个链接到 IMA。';
              toast('IMA 链接已导入', 'success');
              urlArea.value = '';
              load(lastParams);
            } catch (error) {
              if (error instanceof ApiError) {
                const payload = error.payload || {};
                const reason = payload.error || payload.reason || error.message;
                urlStatus.textContent = reason;
                toast(reason, 'error');
              } else {
                reportError(error);
              }
            }
          });
        }));

        const MAX_IMA_UPLOAD = 10 * 1024 * 1024;
        const uploadAccept = '.md,.txt,.pdf,.docx,.epub';
        const uploadInput = el('input', { type: 'file', accept: uploadAccept });
        const uploadBtn = el('button', { class: 'btn', type: 'button', text: '上传到 IMA' });
        const uploadStatus = el('p', { class: 'hint',
          text: '单文件 ≤10MB；服务端写临时文件后立即删除。' });
        const uploadRow = el('div', { class: 'upload-row' },
          el('div', { class: 'field field-wide' },
            el('label', { text: '本地文件（' + uploadAccept + '）' }),
            uploadInput),
          el('div', { class: 'form-actions' }, uploadBtn),
          uploadStatus);
        uploadBtn.addEventListener('click', handler(async () => {
          const file = uploadInput.files && uploadInput.files[0];
          if (!file) {
            toast('请选择要上传的文件', 'error');
            return;
          }
          if (file.size > MAX_IMA_UPLOAD) {
            toast('文件超过 10MB，请压缩或拆分后再传', 'error');
            return;
          }
          const suffix = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
          if (uploadAccept.split(',').indexOf(suffix) < 0) {
            toast('仅支持 ' + uploadAccept, 'error');
            return;
          }
          const base64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => {
              const result = String(reader.result || '');
              const comma = result.indexOf(',');
              resolve(comma >= 0 ? result.slice(comma + 1) : result);
            };
            reader.onerror = () => reject(new Error('文件读取失败'));
            reader.readAsDataURL(file);
          });
          await withBusy(uploadBtn, async () => {
            try {
              const out = await api('/api/projects/' + enc(pid) + '/ima/upload', {
                method: 'POST',
                body: { filename: file.name, content_base64: base64,
                  kb_id: kbSelect.value || null },
                quiet: true
              });
              if (out.degraded) {
                const reason = out.reason || '需要 Node/COS 上传脚本';
                uploadStatus.textContent = reason
                  + (out.hint ? '：' + out.hint : '');
                toast('IMA 上传未完成：' + reason, 'error');
                return;
              }
              uploadStatus.textContent = '已上传：' + (out.name || file.name);
              toast('已上传到 IMA', 'success');
              uploadInput.value = '';
              load(lastParams);
            } catch (error) {
              if (error instanceof ApiError) {
                const payload = error.payload || {};
                const reason = payload.degraded
                  ? (payload.reason || '需要 Node/COS 上传脚本')
                  : (payload.error || error.message);
                uploadStatus.textContent = reason;
                toast(reason, 'error');
              } else {
                reportError(error);
              }
            }
          });
        }));

        const scanDir = el('input', { type: 'text', placeholder: '留空使用 data/ima_exports' });
        const scanBtn = el('button', { class: 'btn', type: 'button', text: '扫描导出目录' });
        const scanStatus = el('div', { class: 'hint' });
        const guideBox = el('div', { class: 'notice' }, el('p', { class: 'loading', text: '读取导出向导…' }));
        scanBtn.addEventListener('click', handler(async () => {
          await withBusy(scanBtn, async () => {
            const out = await api('/api/ima/exports/scan', { method: 'POST',
              body: { project: pid, dir: scanDir.value.trim() || null } });
            const warn = (out.warnings || []).map((x) => el('li', { text: x }));
            scanStatus.replaceChildren(...[
              el('p', { text: '扫描 ' + out.scanned + ' 个文件，导入 ' + out.count + ' 条，跳过重复 ' + out.skipped + ' 条。' }),
              warn.length ? el('ul', { class: 'plain-list' }, warn) : null
            ].filter(Boolean));
            toast('IMA 导出目录扫描完成', 'success');
            load(lastParams);
          });
        }));
        api('/api/ima/exports/guide', { quiet: true }).then((guide) => {
          guideBox.replaceChildren(
            el('strong', { text: '订阅库正文的正路：客户端导出 → 本页扫描' }),
            el('p', { class: 'hint', text: guide.why_not_api || '' }),
            el('ol', { class: 'plain-list' }, (guide.steps || []).map((step) => el('li', { text: step }))),
            el('p', { class: 'hint', text: '支持：' + (guide.supported || []).join(' / ') + '；不支持：' + (guide.unsupported || []).join(' / ') }));
        }).catch(() => guideBox.replaceChildren(el('p', { class: 'hint', text: '导出向导读取失败' })));

        imaBox.replaceChildren(
          el('div', { class: 'inline-form' },
            badge('IMA OpenAPI 已连接', 'ok'),
            el('span', { class: 'hint', text: '订阅库正文受客户端权限保护；本页不绕过 220030。' })),
          searchForm, results,
          el('div', { class: 'divider' }),
          el('h3', { text: '导入到 IMA 知识库' }),
          urlRow, uploadRow,
          el('div', { class: 'divider' }),
          el('h3', { text: '从 IMA 客户端导出目录导入' }),
          el('div', { class: 'inline-form' }, field('目录', scanDir), scanBtn),
          scanStatus, guideBox);
      } catch (error) {
        if (!(error instanceof ApiError)) reportError(error);
        imaBox.replaceChildren(emptyState('IMA 状态读取失败'));
      }
    }

    const initial = new URLSearchParams();
    await load(initial);
    initIma();

    return [
      pageHead('素材与知识库', '检索命中会返回高亮片段；证据等级只表示可核查程度，不给内容打质量分。'),
      card('检索', searchForm, chipsBox),
      card('素材证据审计 · E0–E3', auditBox),
      card('检索结果 / 全部素材', listBox),
      card('新建素材', addForm),
      importCard,
      card('IMA 知识库', imaBox)
    ];
  }

  // ---------------------------------------------------------------- 伏笔
  async function pageForeshadow(pid) {
    const base = '/api/projects/' + enc(pid) + '/foreshadow';
    const items = await api(base);
    const list = Array.isArray(items) ? items : [];

    const textInput = el('input', { type: 'text', maxlength: 2000, required: true, placeholder: '如：铜钥匙的来源' });
    const plantedInput = el('input', { type: 'number', min: '0', step: '1', required: true });
    const dueInput = el('input', { type: 'number', min: '0', step: '1' });
    const addBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '登记伏笔' });
    const addForm = el('form', { class: 'inline-form' },
      field('伏笔内容', textInput),
      field('埋设章号', plantedInput),
      field('计划回收章（可选）', dueInput),
      addBtn);
    addForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      if (!textInput.value.trim() || plantedInput.value === '') {
        toast('伏笔内容与埋设章号必填', 'error');
        return;
      }
      const body = { text: textInput.value.trim(), planted_chapter: Number(plantedInput.value) };
      if (dueInput.value !== '') body.due_chapter = Number(dueInput.value);
      await withBusy(addBtn, async () => {
        await api(base, { method: 'POST', body: body });
        toast('伏笔已登记', 'success');
        addForm.reset();
        render();
      });
    }));

    const filterSel = selectEl(opts(FORESHADOW_STATUS_LABELS, '全部状态'), '');
    const tableBox = el('div');

    function buildTable() {
      const status = filterSel.value;
      const filtered = status ? list.filter((item) => item.status === status) : list;
      const rows = filtered.map((item) => {
        const chapterInput = el('input', { type: 'number', min: '0', step: '1', 'aria-label': '回收章号' });
        chapterInput.value = String(item.resolved_chapter === null || item.resolved_chapter === undefined ? '' : item.resolved_chapter);
        const statusSelect = selectEl([['resolved', '已回收'], ['dropped', '已放弃']], item.status === 'open' ? 'resolved' : item.status);
        const action = el('button', { class: 'btn btn-small', type: 'button', text: '回收' });
        action.addEventListener('click', handler(async () => {
          if (chapterInput.value === '') {
            toast('请填写回收章号', 'error');
            chapterInput.focus();
            return;
          }
          await withBusy(action, async () => {
            await api(base + '/' + enc(item.id), {
              method: 'PUT',
              body: { chapter: Number(chapterInput.value), status: statusSelect.value }
            });
            toast('伏笔状态已更新', 'success');
            render();
          });
        }));
        const tone = item.status === 'open' ? 'warn' : (item.status === 'resolved' ? 'ok' : null);
        return [
          el('code', { text: item.id }),
          item.text,
          String(item.planted_chapter),
          item.due_chapter === null || item.due_chapter === undefined ? '—' : String(item.due_chapter),
          badge(FORESHADOW_STATUS_LABELS[item.status] || item.status, tone),
          item.resolved_chapter === null || item.resolved_chapter === undefined ? '—' : String(item.resolved_chapter),
          el('div', { class: 'row-actions' }, chapterInput, statusSelect, action)
        ];
      });
      tableBox.replaceChildren(dataTable(
        ['id', '内容', '埋设章', '计划回收', '状态', '实际回收章', '操作'],
        rows, status ? '没有该状态的伏笔' : '还没有伏笔记录'));
    }
    filterSel.addEventListener('change', buildTable);
    buildTable();

    return [
      pageHead('伏笔', '登记埋下的线索，并在回收章记录状态。'),
      card('登记伏笔', addForm),
      card('伏笔台账', el('div', { class: 'inline-form' }, field('筛选状态', filterSel)), tableBox)
    ];
  }

  // ---------------------------------------------------------------- 事实
  async function pageFacts(pid) {
    const base = '/api/projects/' + enc(pid) + '/facts';
    const items = await api(base);
    const list = Array.isArray(items) ? items : [];

    const claimInput = el('input', { type: 'text', maxlength: 2000, required: true, placeholder: '待核事实，如：主角生于霜降' });
    const sourceInput = el('input', { type: 'text', maxlength: 2000, placeholder: '来源线索（可选）' });
    const chapterInput = el('input', { type: 'number', min: '0', step: '1' });
    const addBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '登记事实' });
    const addForm = el('form', { class: 'inline-form' },
      field('陈述', claimInput),
      field('来源', sourceInput),
      field('章号', chapterInput),
      addBtn);
    addForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      if (!claimInput.value.trim()) {
        toast('请填写待核事实', 'error');
        claimInput.focus();
        return;
      }
      const body = { claim: claimInput.value.trim() };
      if (sourceInput.value.trim()) body.source = sourceInput.value.trim();
      if (chapterInput.value !== '') body.chapter = Number(chapterInput.value);
      await withBusy(addBtn, async () => {
        await api(base, { method: 'POST', body: body });
        toast('事实已登记', 'success');
        addForm.reset();
        render();
      });
    }));

    const rows = list.map((item) => {
      const statusSel = selectEl(opts(FACT_STATUS_LABELS), item.status);
      const noteInput = el('input', { type: 'text', maxlength: 2000, value: item.note || '', 'aria-label': '备注' });
      const updateBtn = el('button', { class: 'btn btn-small', type: 'button', text: '更新' });
      updateBtn.addEventListener('click', handler(async () => {
        await withBusy(updateBtn, async () => {
          await api(base + '/' + enc(item.id), {
            method: 'PUT',
            body: { status: statusSel.value, note: noteInput.value }
          });
          toast('事实状态已更新', 'success');
          render();
        });
      }));
      const verifyHost = el('div', { class: 'verify-host' });
      const verifyBtn = el('button', { class: 'btn btn-small', type: 'button', text: '验真' });
      verifyBtn.addEventListener('click', handler(async () => {
        await withBusy(verifyBtn, async () => {
          const out = await api(base + '/' + enc(item.id) + '/verify',
            { method: 'POST', body: {} });
          const citation = out.citation || {};
          const check = out.verification || {};
          const score = typeof check.score === 'number' ? check.score.toFixed(2) : '—';
          verifyHost.replaceChildren(
            badge(citation.verified ? '已验真' : '未验真', citation.verified ? 'ok' : 'warn'),
            el('p', { class: 'evidence-summary', text: 'score ' + score + '；' + (check.evidence || '无证据片段') }));
          toast(citation.verified ? '验真通过' : '未能在来源中找到该断言',
            citation.verified ? 'success' : 'error');
        });
      }));
      return [
        el('code', { text: item.id }),
        item.claim,
        item.source || '—',
        item.chapter === null || item.chapter === undefined ? '—' : String(item.chapter),
        badge(FACT_STATUS_LABELS[item.status] || item.status,
          item.status === 'verified' ? 'ok' : (item.status === 'pending' ? 'warn' : null)),
        el('div', { class: 'row-actions' }, statusSel, noteInput, updateBtn),
        el('div', { class: 'verify-cell' }, verifyBtn, verifyHost)
      ];
    });

    return [
      pageHead('待核事实', '登记需要核实的设定与细节，逐条改状态；「验真」只登记引证并核对来源，不改事实状态。'),
      card('登记事实', addForm),
      card('事实台账（' + list.length + '）',
        dataTable(['id', '陈述', '来源', '章号', '状态', '改状态 / 备注', '验真'], rows,
          '还没有事实条目；登记一条待核事实后即可验真'))
    ];
  }

  // ---------------------------------------------------------------- 文风
  async function pageStyle(pid) {
    const base = '/api/projects/' + enc(pid) + '/style';
    const style = await api(base);
    const raw = style && typeof style === 'object' ? style : {};
    const keys = Object.keys(raw).filter((key) => raw[key] !== null && raw[key] !== undefined).sort();
    const rows = [];
    const rowBox = el('div');

    if (!keys.length) {
      rowBox.append(emptyState('还没有文风设置，可在下方新增键。'));
    } else {
      for (const key of keys) {
        const value = raw[key];
        const input = el('input', {
          type: 'text',
          value: typeof value === 'string' ? value : JSON.stringify(value),
          'aria-label': '键 ' + key + ' 的值'
        });
        const remove = el('button', { class: 'btn btn-small btn-danger', type: 'button', text: '删除' });
        remove.addEventListener('click', handler(async () => {
          const answer = prompt('请输入文风键「' + key + '」以确认删除：');
          if (answer === null) return;
          if (answer !== key) {
            toast('确认串不匹配，已取消', 'error');
            return;
          }
          await withBusy(remove, async () => {
            await api(base, { method: 'PUT', body: { key: key, value: null } });
            toast('已置空该文风键（接口无独立删除路由）', 'success');
            render();
          });
        }));
        rows.push({ key: key, input: input });
        rowBox.append(el('div', { class: 'kv-row' },
          el('code', { class: 'kv-key', text: key }),
          input,
          remove));
      }
    }

    const saveAll = el('button', { class: 'btn btn-primary', type: 'button', text: '保存全部改动' });
    if (!rows.length) saveAll.disabled = true;
    saveAll.addEventListener('click', handler(async () => {
      await withBusy(saveAll, async () => {
        let saved = 0;
        const failed = [];
        for (const row of rows) {
          try {
            await api(base, { method: 'PUT', body: { key: row.key, value: parseLoose(row.input.value).value }, quiet: true });
            saved += 1;
          } catch (error) {
            failed.push(row.key);
          }
        }
        toast('已保存 ' + saved + ' 项文风设置' + (failed.length ? '；失败：' + failed.join('、') : ''),
          failed.length ? 'error' : 'success');
        render();
      });
    }));

    const newKey = el('input', { type: 'text', maxlength: 80, placeholder: '如：视角、语速' });
    const newValue = el('input', { type: 'text', placeholder: '值（可填 JSON）' });
    const addBtn = el('button', { class: 'btn', type: 'submit', text: '新增键' });
    const addForm = el('form', { class: 'inline-form' },
      field('键', newKey), field('值', newValue), addBtn);
    addForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const key = newKey.value.trim();
      if (!key) {
        toast('请填写文风键名', 'error');
        newKey.focus();
        return;
      }
      await withBusy(addBtn, async () => {
        await api(base, { method: 'PUT', body: { key: key, value: parseLoose(newValue.value).value } });
        toast('已写入文风键 ' + key, 'success');
        render();
      });
    }));

    return [
      pageHead('文风', '键值形式保存叙述视角、句式、语速等风格约束。'),
      card('文风设置', rowBox,
        el('div', { class: 'form-actions' }, saveAll),
        el('p', { class: 'hint', text: '值按 JSON 解析；解析失败则按纯文本保存。删除以写入 null 实现（后端无独立删除路由）。' })),
      card('新增键', addForm)
    ];
  }

  // ---------------------------------------------------------------- 质量（质量环 + 文风画像）
  const QUALITY_DIMENSIONS = [
    ['blank_space', '留白'], ['rhythm', '节奏'], ['attitude', '叙述态度'],
    ['sensory', '感官颗粒'], ['character', '人物一致'], ['plot', '情节推进'],
    ['dialogue', '对话潜台词']
  ];
  const LOOP_STATUS_LABELS = { done: '完成', blocked: '阻断', failed: '失败', running: '运行中' };
  const LOOP_STATUS_TONES = { done: 'ok', blocked: 'danger', failed: 'danger', running: 'warn' };
  const LOOP_EXIT_LABELS = {
    threshold: '达到阈值', no_improvement: '无提升退出',
    max_rounds: '轮数用尽', lint_blocked: 'L0 阻断'
  };
  const STYLE_DIMENSION_LABELS = {
    vocabulary: '词汇', syntax: '句式', paragraph: '段落', rhetoric: '修辞', emotion: '情绪',
    pov: '视角', tone: '语气', format: '格式', thinking: '思维'
  };

  function qualityScoreBars(scores) {
    const grid = el('div', { class: 'quality-bars' });
    for (const pair of QUALITY_DIMENSIONS) {
      const raw = scores ? scores[pair[0]] : undefined;
      const value = typeof raw === 'number' ? raw : null;
      const pct = value === null ? 0 : Math.max(0, Math.min(100, value * 10));
      grid.append(el('div', { class: 'quality-bar-row' },
        el('span', { class: 'quality-bar-label', text: pair[1] }),
        el('span', { class: 'quality-bar-track' },
          el('span', { class: 'quality-bar-fill', style: 'width:' + pct + '%' })),
        el('span', { class: 'quality-bar-value', text: value === null ? '—' : value.toFixed(1) })));
    }
    return grid;
  }

  function citationStatusBadge(item) {
    if (!item) return null;
    const ref = item.citation;
    const hasRef = (item.citation_id !== undefined && item.citation_id !== null)
      || (ref !== undefined && ref !== null);
    if (!hasRef) return null;
    let verified = false;
    if (ref && typeof ref === 'object') verified = Boolean(ref.verified);
    else if (typeof ref === 'boolean') verified = ref;
    return badge(verified ? '引证已验真' : '引证未验真', verified ? 'ok' : 'warn');
  }

  function qualityViolations(list) {
    if (!list || !list.length) return el('p', { class: 'hint', text: '本轮没有列出违规。' });
    const box = el('div', { class: 'quality-violations' });
    for (const item of list) {
      box.append(el('div', { class: 'quality-violation' },
        el('div', { class: 'finding-head' }, badge(item.rule || 'rule', 'warn'),
          citationStatusBadge(item)),
        item.evidence ? el('p', { class: 'finding-quote', text: item.evidence }) : null,
        el('p', { class: 'hint', text: item.suggestion || '' })));
    }
    return box;
  }

  function qualityRoundsFromMetrics(metrics) {
    const map = new Map();
    for (const row of (metrics || [])) {
      const round = Number(row.round);
      if (!map.has(round)) {
        map.set(round, { round: round, overall: null, scores: {}, violations: [] });
      }
      const item = map.get(round);
      if (typeof row.overall === 'number') item.overall = row.overall;
      if (row.dimension) item.scores[row.dimension] = row.score;
      if (!item.violations.length && Array.isArray(row.violations) && row.violations.length) {
        item.violations = row.violations;
      }
    }
    return Array.from(map.values()).sort((a, b) => a.round - b.round);
  }

  function qualityLoopView(result) {
    if (!result) return emptyState('质量环结果会显示在这里。');
    if (result.status === 'blocked' && result.lint) {
      const list = el('div', { class: 'findings' });
      for (const finding of (result.lint.findings || [])) {
        if (finding.level !== 'block') continue;
        list.append(el('div', { class: 'finding finding-red' },
          el('div', { class: 'finding-head' },
            badge(finding.rule || 'rule', 'danger'),
            badge('阻断', 'danger'),
            citationStatusBadge(finding),
            finding.locator ? el('code', { text: finding.locator }) : null),
          finding.evidence ? el('p', { class: 'finding-quote', text: finding.evidence }) : null,
          el('p', { class: 'hint', text: finding.suggestion || '' })));
      }
      return el('div', {},
        el('div', { class: 'automation-run-head' },
          badge('L0 阻断', 'danger'),
          el('strong', { text: 'loop ' + (result.loop_id || '') }),
          el('span', { class: 'muted', text: '第 ' + result.chapter_n + ' 章' })),
        el('div', { class: 'notice notice-danger' },
          el('p', { text: '先修 L0 硬伤，再启动质量环；本次未消耗模型调用。' })),
        list);
    }

    const rounds = result.rounds || [];
    const rows = rounds.map((round) => {
      const cells = ['第 ' + round.round + ' 轮',
        typeof round.overall === 'number' ? round.overall.toFixed(2) : '—'];
      for (const pair of QUALITY_DIMENSIONS) {
        const value = round.scores ? round.scores[pair[0]] : undefined;
        cells.push(typeof value === 'number' ? value.toFixed(1) : '—');
      }
      return cells;
    });
    const headers = ['轮次', 'overall'].concat(QUALITY_DIMENSIONS.map((pair) => pair[1]));
    const roundBlocks = el('div', { class: 'quality-rounds' });
    for (const round of rounds) {
      roundBlocks.append(el('div', { class: 'quality-round' },
        el('div', { class: 'automation-run-head' },
          badge('第 ' + round.round + ' 轮', 'muted'),
          el('span', { class: 'muted', text: 'overall ' + (typeof round.overall === 'number' ? round.overall.toFixed(2) : '—') })),
        qualityScoreBars(round.scores),
        qualityViolations(round.violations)));
    }

    const best = result.best_score === null || result.best_score === undefined
      ? null : Number(result.best_score).toFixed(2);
    return el('div', {},
      el('div', { class: 'automation-run-head' },
        badge(LOOP_STATUS_LABELS[result.status] || result.status || '完成', LOOP_STATUS_TONES[result.status] || 'ok'),
        el('strong', { text: 'loop ' + (result.loop_id || '') }),
        el('span', { class: 'muted', text: '第 ' + result.chapter_n + ' 章' }),
        best === null ? null : el('span', { class: 'muted', text: '最佳分 ' + best }),
        result.exit_reason ? badge(LOOP_EXIT_LABELS[result.exit_reason] || result.exit_reason, 'muted') : null,
        result.applied ? badge('已写入 v' + result.version, 'ok') : badge('未写正文', 'muted')),
      dataTable(headers, rows, '本次没有评分轮次'),
      roundBlocks);
  }

  async function pageQuality(pid) {
    const base = '/api/projects/' + enc(pid) + '/quality';
    const styleBase = '/api/projects/' + enc(pid) + '/style';
    let data = await api(base);
    let loops = Array.isArray(data.loops) ? data.loops : [];
    let profiles = Array.isArray(data.profiles) ? data.profiles : [];
    let latest = data.latest || null;

    const latestHost = el('div');
    const resultHost = el('div', { class: 'report-host' }, emptyState('质量环结果会显示在这里。'));
    const metaHost = el('div', {}, emptyState('点击「元循环建议」查看通过率与改进建议。'));
    const profileHost = el('div');
    const historyHost = el('div');

    function drawLatest() {
      if (!latest) {
        latestHost.replaceChildren(emptyState(
          '还没有章节正文',
          '先在创作台写一章，再回来做 L0 检查。',
          el('a', { class: 'btn', href: projectHash(pid, 'writing', 'studio'), text: '去创作台' })));
        return;
      }
      const chapterText = latest.chapter_n === null || latest.chapter_n === undefined
        ? '最新章' : '第 ' + latest.chapter_n + ' 章';
      if (!latest.lint) {
        const run = el('button', { class: 'btn btn-primary', type: 'button', text: '运行 L0 检查' });
        run.addEventListener('click', handler(async () => {
          await withBusy(run, async () => {
            const fresh = await api(base);
            latest = fresh.latest || null;
            drawLatest();
            toast('已刷新 L0 检查', 'success');
          });
        }));
        latestHost.replaceChildren(
          el('div', { class: 'inline-form' },
            el('span', { class: 'muted', text: chapterText }),
            badge('未检查', 'muted')),
          emptyState('还没有 L0 报告', '运行一次 L0 检查，查看本章硬伤与提醒。', run));
        return;
      }
      const counts = latest.lint.counts || {};
      const blocks = Number(counts.block) || 0;
      const warns = Number(counts.warn) || 0;
      const head = el('div', { class: 'inline-form' },
        el('span', { class: 'muted', text: chapterText }),
        badge('阻断 ' + blocks, blocks ? 'danger' : 'ok'),
        badge('提醒 ' + warns, warns ? 'warn' : 'ok'));
      if (latest.lint.blocked) {
        latestHost.replaceChildren(head,
          el('div', { class: 'notice notice-danger' },
            el('p', { text: '存在阻断级 L0 问题，建议先修复再启动质量环。' })));
      } else {
        latestHost.replaceChildren(head);
      }
    }

    function qualityDimLabel(key) {
      const hit = QUALITY_DIMENSIONS.find((pair) => pair[0] === key);
      return hit ? hit[1] : (STYLE_DIMENSION_LABELS[key] || key);
    }

    function describeChanges(clean) {
      const parts = [];
      if (clean && clean.threshold !== null && clean.threshold !== undefined) {
        parts.push('阈值 → ' + clean.threshold);
      }
      const weights = (clean && clean.weights) || {};
      const keys = Object.keys(weights);
      if (keys.length) parts.push(keys.length + ' 项权重');
      const banned = (clean && clean.banned_add) || [];
      if (banned.length) parts.push(banned.length + ' 个禁用词');
      return parts.length ? parts.join('、') : '没有可应用的变更';
    }

    function renderMetaError(host, error) {
      if (error instanceof ApiError) {
        const reason = (error.payload && error.payload.error) || error.message;
        host.replaceChildren(el('div', { class: 'notice notice-danger' },
          el('strong', { text: '应用失败' }), el('p', { text: reason })));
      } else {
        reportError(error);
        host.replaceChildren(el('div', { class: 'notice notice-danger' },
          el('p', { text: '应用失败，请稍后重试。' })));
      }
    }

    function drawMeta(out) {
      const proposals = (out && out.proposals) || [];
      const passRate = typeof out.pass_rate === 'number'
        ? Math.round(out.pass_rate * 1000) / 10 + '%' : '—';
      const avg = typeof out.avg_score === 'number' ? out.avg_score.toFixed(2) : '—';
      const violations = (out && out.top_violations) || [];
      const changes = (out && out.changes) || {};
      const current = (out && out.current) || {};

      const changeRows = [];
      if (changes.threshold !== null && changes.threshold !== undefined) {
        changeRows.push(['阈值',
          current.threshold === undefined ? '—' : String(current.threshold),
          String(changes.threshold)]);
      }
      const weights = changes.weights || {};
      for (const key of Object.keys(weights)) {
        const before = (current.weights || {})[key];
        changeRows.push([qualityDimLabel(key),
          before === undefined ? '—' : String(before), String(weights[key])]);
      }
      const banned = Array.isArray(changes.banned_add) ? changes.banned_add : [];
      const hasChanges = changeRows.length > 0 || banned.length > 0;

      let changeBox = null;
      if (hasChanges) {
        const previewHost = el('div', { class: 'change-preview' });
        const previewBtn = el('button', {
          class: 'btn btn-small', type: 'button', text: '预览应用'
        });
        const confirmBtn = el('button', {
          class: 'btn btn-primary btn-small', type: 'button', text: '确认应用'
        });
        previewBtn.addEventListener('click', handler(async () => {
          await withBusy(previewBtn, async () => {
            try {
              const result = await api(base + '/meta/apply', {
                method: 'POST',
                body: { changes: changes, confirm: false },
                quiet: true
              });
              previewHost.replaceChildren(el('p', { class: 'hint',
                text: '预览通过：' + describeChanges(result.changes) + '（尚未写入）' }));
            } catch (error) {
              renderMetaError(previewHost, error);
            }
          });
        }));
        confirmBtn.addEventListener('click', handler(async () => {
          if (!window.confirm('确认把以上调整写入项目质量配置？写入后会记录审计。')) return;
          await withBusy(confirmBtn, async () => {
            try {
              const result = await api(base + '/meta/apply', {
                method: 'POST',
                body: { changes: changes, confirm: true },
                quiet: true
              });
              toast(result.applied ? '质量配置已更新' : '没有写入任何变更', 'success');
              previewHost.replaceChildren();
              await refresh();
              await loadMeta();
            } catch (error) {
              renderMetaError(previewHost, error);
            }
          });
        }));
        changeBox = el('div', { class: 'change-table' },
          el('h3', { text: '可应用的调整' }),
          dataTable(['维度', '当前', '建议'], changeRows,
            '没有阈值或权重变化'),
          banned.length
            ? el('p', { class: 'hint', text: '新增禁用词：' + banned.join('、') })
            : null,
          el('div', { class: 'form-actions' }, previewBtn, confirmBtn),
          previewHost);
      }

      metaHost.replaceChildren(
        el('div', { class: 'stat-grid' },
          el('div', { class: 'stat' },
            el('div', { class: 'stat-value', text: passRate }),
            el('div', { class: 'stat-label', text: '通过率' })),
          el('div', { class: 'stat' },
            el('div', { class: 'stat-value', text: avg }),
            el('div', { class: 'stat-label', text: '均分' })),
          el('div', { class: 'stat' },
            el('div', { class: 'stat-value', text: String(((out && out.runs) || []).length) }),
            el('div', { class: 'stat-label', text: '样本轮次' }))),
        el('div', { class: 'divider' }),
        dataTable(['高频违规', '次数'],
          violations.map((item) => [item.rule, String(item.count)]), '还没有违规记录'),
        proposals.length
          ? el('div', { class: 'notice' },
            el('strong', { text: '改进建议' }),
            el('ul', {}, proposals.map((item) => el('li', { text: item }))))
          : emptyState('暂时没有改进建议。'),
        hasChanges ? el('div', { class: 'divider' }) : null,
        changeBox);
    }

    async function loadMeta() {
      const out = await api(base + '/meta', { method: 'POST', body: { window: 10 } });
      drawMeta(out);
      return out;
    }

    function drawProfiles() {
      if (!profiles.length) {
        profileHost.replaceChildren(emptyState('还没有文风画像；先蒸馏一份。'));
        return;
      }
      const rows = profiles.map((item) => {
        const apply = el('button', { class: 'btn btn-small', type: 'button', text: '应用' });
        apply.addEventListener('click', handler(async () => {
          await withBusy(apply, async () => {
            await api(styleBase + '/profiles/' + enc(item.id) + '/apply',
              { method: 'POST', body: {} });
            toast('已应用文风画像：' + item.name, 'success');
          });
        }));
        const dims = item.dimensions || {};
        const summary = Object.keys(dims).slice(0, 3)
          .map((key) => (STYLE_DIMENSION_LABELS[key] || key) + '：' + dims[key])
          .join('；');
        return [
          el('code', { text: item.id }),
          item.name,
          formatTime(item.created_at),
          el('span', { class: 'hint', text: summary || '—' }),
          el('div', { class: 'row-actions' }, apply)
        ];
      });
      profileHost.replaceChildren(
        dataTable(['id', '名称', '创建时间', '维度摘要', '操作'], rows, '还没有文风画像'));
    }

    function drawHistory() {
      if (!loops.length) {
        historyHost.replaceChildren(emptyState('还没有质量环记录'));
        return;
      }
      const rows = loops.map((loop) => {
        const link = el('button', { class: 'linklike', type: 'button', text: loop.id });
        link.addEventListener('click', handler(async () => {
          const detail = await api(base + '/loops/' + enc(loop.id));
          const meta = (detail && detail.loop) || {};
          resultHost.replaceChildren(qualityLoopView({
            loop_id: meta.id,
            status: meta.status,
            chapter_n: meta.chapter_n,
            best_score: meta.best_score,
            exit_reason: meta.exit_reason,
            rounds: qualityRoundsFromMetrics(detail && detail.metrics)
          }));
          toast('已加载质量环 ' + loop.id, 'success');
        }));
        return [
          link,
          loop.chapter_n === null || loop.chapter_n === undefined ? '—' : '第 ' + loop.chapter_n + ' 章',
          badge(LOOP_STATUS_LABELS[loop.status] || loop.status,
            LOOP_STATUS_TONES[loop.status] || null),
          loop.best_score === null || loop.best_score === undefined
            ? '—' : Number(loop.best_score).toFixed(2),
          loop.exit_reason ? (LOOP_EXIT_LABELS[loop.exit_reason] || loop.exit_reason) : '—',
          formatTime(loop.created_at)
        ];
      });
      historyHost.replaceChildren(dataTable(
        ['loop_id', '章节', '状态', '最佳分', '退出原因', '时间'], rows, '还没有质量环记录'));
    }

    async function refresh() {
      const fresh = await api(base);
      loops = Array.isArray(fresh.loops) ? fresh.loops : [];
      profiles = Array.isArray(fresh.profiles) ? fresh.profiles : [];
      latest = fresh.latest || null;
      drawLatest();
      drawHistory();
      drawProfiles();
      syncBlindProfileOptions();
    }

    // ---- 质量环 ----
    const chapterInput = el('input', {
      type: 'number', min: '0', step: '1', placeholder: '默认最新章',
      value: latest && latest.chapter_n !== null && latest.chapter_n !== undefined
        ? String(latest.chapter_n) : ''
    });
    const roundsInput = el('input', { type: 'number', min: '1', max: '5', step: '1', value: '2' });
    const thresholdInput = el('input', { type: 'number', min: '0', max: '10', step: '0.1', value: '8.5' });
    const applyInput = el('input', { type: 'checkbox' });
    const startBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '开始质量环' });
    const loopForm = el('form', { class: 'form-grid' },
      field('章号', chapterInput, '留空使用最新章'),
      field('最大轮数', roundsInput, '1–5，默认 2'),
      field('阈值', thresholdInput, '0–10，默认 8.5'),
      checkboxField('应用最佳稿', applyInput, '勾选后才把最佳文本写成新版本'),
      el('div', { class: 'field field-wide form-actions' }, startBtn));
    loopForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const body = {
        max_rounds: Number(roundsInput.value) || 2,
        threshold: thresholdInput.value === '' ? 8.5 : Number(thresholdInput.value),
        apply: Boolean(applyInput.checked)
      };
      if (chapterInput.value !== '') body.chapter_n = Number(chapterInput.value);
      await withBusy(startBtn, async () => {
        const out = await api(base + '/loop', { method: 'POST', body: body });
        resultHost.replaceChildren(qualityLoopView(out));
        if (out.status === 'blocked') {
          toast('L0 阻断：先修硬伤，再启动质量环', 'error');
        } else if (out.applied) {
          toast('质量环完成，已写入第 ' + out.chapter_n + ' 章 v' + out.version, 'success');
        } else {
          toast('质量环完成：' + (LOOP_EXIT_LABELS[out.exit_reason] || out.exit_reason), 'success');
        }
        await refresh();
      });
    }));

    const metaBtn = el('button', { class: 'btn', type: 'button', text: '元循环建议' });
    metaBtn.addEventListener('click', handler(async () => {
      await withBusy(metaBtn, async () => {
        await loadMeta();
        toast('已生成元循环建议', 'success');
      });
    }));

    // ---- 文风画像 ----
    const chaptersInput = el('input', { type: 'text', placeholder: '如：1,2,3' });
    const textsArea = el('textarea', {
      rows: '6', spellcheck: 'false',
      placeholder: '粘贴样本文本；多篇之间用单独一行 --- 分隔'
    });
    const nameInput = el('input', { type: 'text', maxlength: '200', value: '默认画像' });
    const distillBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '蒸馏文风' });
    const distillForm = el('form', { class: 'form-grid' },
      field('章号', chaptersInput, '逗号分隔；与粘贴文本可同时使用'),
      field('画像名', nameInput),
      field('粘贴文本', textsArea,
        '至少 3 篇 / 2000 字；需要先在「设置」里配置模型', 'field-wide'),
      el('div', { class: 'field field-wide form-actions' }, distillBtn));
    distillForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const body = { name: nameInput.value.trim() || '默认画像' };
      const ns = chaptersInput.value.split(/[,，\s]+/).map((part) => part.trim())
        .filter(Boolean).map((part) => Number(part))
        .filter((value) => Number.isFinite(value));
      if (ns.length) body.chapter_ns = ns;
      const chunks = textsArea.value.split(/\n\s*---\s*\n/)
        .map((part) => part.trim()).filter(Boolean);
      if (chunks.length) body.texts = chunks;
      if (!ns.length && !chunks.length) {
        toast('请填写章号或粘贴文本', 'error');
        chaptersInput.focus();
        return;
      }
      await withBusy(distillBtn, async () => {
        const out = await api(styleBase + '/distill', { method: 'POST', body: body });
        toast('已生成文风画像：' + out.name, 'success');
        for (const warning of (out.warnings || [])) toast(warning);
        await refresh();
      });
    }));

    // ---- 文风盲测 ----
    const blindProfile = el('select');
    const blindRef = el('textarea', {
      rows: '5', spellcheck: 'false', placeholder: '粘贴你的目标文风样本'
    });
    const blindCand = el('textarea', {
      rows: '5', spellcheck: 'false', placeholder: '粘贴待检查稿'
    });
    const blindThreshold = el('input', {
      type: 'number', min: '50', max: '100', step: '1', value: '75'
    });
    const blindResult = el('div', { class: 'report-host' },
      emptyState('参考=你的样本，候选=待检查稿'));
    const blindBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '运行盲测' });
    const blindForm = el('form', { class: 'form-grid' },
      field('文风画像（可选）', blindProfile, '用档案里的禁用词扣分；留空则用项目禁用词'),
      field('阈值', blindThreshold, '50–100，默认 75'),
      field('参考样本', blindRef, '至少 100 字', 'field-wide'),
      field('候选样本', blindCand, '至少 100 字', 'field-wide'),
      el('div', { class: 'field field-wide form-actions' }, blindBtn));

    function syncBlindProfileOptions() {
      const current = blindProfile.value;
      const options = [el('option', { value: '', text: '（不使用档案）' })];
      for (const item of profiles) {
        options.push(el('option', {
          value: item.id, text: item.name + '（' + item.id + '）'
        }));
      }
      blindProfile.replaceChildren(...options);
      if (current && profiles.some((item) => item.id === current)) {
        blindProfile.value = current;
      }
    }

    function drawBlind(out) {
      const verdict = out.passed ? badge('通过', 'ok') : badge('未通过', 'danger');
      const penalty = out.penalty ? '，扣 ' + out.penalty + ' 分' : '';
      const rows = (out.dimensions || []).map((dim) => [
        dim.label,
        String(dim.reference),
        String(dim.candidate),
        (Number(dim.similarity) * 100).toFixed(1) + '%'
      ]);
      blindResult.replaceChildren(
        el('div', { class: 'inline-form' },
          el('strong', { text: '总分 ' + out.score }),
          verdict,
          el('span', { class: 'muted', text: '基线 ' + out.base_score + ' / 阈值 ' + out.threshold })),
        el('div', { class: 'inline-form' },
          el('span', { class: 'muted', text: '禁用词命中 ' + out.banned_hits + ' 次' + penalty }),
          el('span', { class: 'muted', text: '样本 参考 ' + out.samples.reference + ' / 候选 ' + out.samples.candidate })),
        dataTable(['维度', '参考', '候选', '相似度'], rows, '没有维度数据'),
        el('p', { class: 'hint', text: out.note }));
    }

    blindForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const body = { threshold: blindThreshold.value === '' ? 75 : Number(blindThreshold.value) };
      if (blindProfile.value) body.profile_id = blindProfile.value;
      if (blindRef.value.trim()) body.reference_texts = [blindRef.value];
      if (blindCand.value.trim()) body.candidate_texts = [blindCand.value];
      await withBusy(blindBtn, async () => {
        const out = await api(styleBase + '/blind-test', { method: 'POST', body: body });
        drawBlind(out);
        toast(out.passed ? '盲测通过：' + out.score : '盲测未通过：' + out.score,
          out.passed ? 'success' : 'error');
      });
    }));

    syncBlindProfileOptions();

    drawLatest();
    drawHistory();
    drawProfiles();

    return [
      pageHead('质量', '先修 L0 硬伤，再跑质量环；文风画像可一键写回项目。'),
      el('div', { class: 'quality-grid' },
        el('div', { class: 'quality-col' },
          card('最新章 L0', latestHost),
          card('质量环', loopForm, resultHost),
          card('最近质量环', historyHost)),
        el('div', { class: 'quality-col' },
          card('文风画像', distillForm, profileHost),
          card('文风盲测', blindForm, blindResult),
          card('元循环建议', metaHost, el('div', { class: 'form-actions' }, metaBtn))))
    ];
  }

  // ---------------------------------------------------------------- 摘要
  async function pageSummaries(pid) {
    const base = '/api/projects/' + enc(pid) + '/summaries';
    const items = await api(base);
    const list = Array.isArray(items) ? items : [];

    const chapterInput = el('input', { type: 'number', min: '0', step: '1', required: true });
    const summaryArea = el('textarea', { rows: '5', spellcheck: 'false', placeholder: '80–150 字的本章摘要' });
    const counter = el('p', { class: 'hint', text: '0 字（建议 80–150 字）' });
    summaryArea.addEventListener('input', () => {
      const length = summaryArea.value.trim().length;
      counter.textContent = length + ' 字（建议 80–150 字）';
      counter.className = 'hint' + (length && (length < 80 || length > 150) ? ' warn' : '');
    });
    const saveBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '保存摘要' });
    const deleteBtn = el('button', { class: 'btn btn-danger', type: 'button', text: '删除该章摘要' });

    saveBtn.addEventListener('click', handler(async () => {
      if (chapterInput.value === '') {
        toast('请填写章号', 'error');
        chapterInput.focus();
        return;
      }
      const n = Number(chapterInput.value);
      await withBusy(saveBtn, async () => {
        const result = await api(base + '/' + n, {
          method: 'PUT',
          body: { summary: summaryArea.value }
        });
        toast(result.deleted ? '已清除该章摘要' : '已保存第 ' + n + ' 章摘要', 'success');
        render();
      });
    }));

    deleteBtn.addEventListener('click', handler(async () => {
      if (chapterInput.value === '') {
        toast('请填写章号', 'error');
        return;
      }
      const n = String(Number(chapterInput.value));
      const answer = prompt('请输入章号「' + n + '」以确认删除摘要：');
      if (answer === null) return;
      if (answer !== n) {
        toast('确认串不匹配，已取消删除', 'error');
        return;
      }
      await withBusy(deleteBtn, async () => {
        await api(base + '/' + n + '?confirm=' + enc(n), { method: 'DELETE' });
        toast('已删除第 ' + n + ' 章摘要', 'success');
        chapterInput.value = '';
        summaryArea.value = '';
        counter.textContent = '0 字（建议 80–150 字）';
        render();
      });
    }));

    const rows = list.map((item) => {
      const editBtn = el('button', { class: 'btn btn-small', type: 'button', text: '编辑' });
      editBtn.addEventListener('click', () => {
        chapterInput.value = String(item.chapter_n);
        summaryArea.value = item.summary;
        summaryArea.dispatchEvent(new Event('input'));
        summaryArea.focus();
      });
      return [
        '第 ' + item.chapter_n + ' 章',
        item.summary,
        formatTime(item.updated_at),
        el('div', { class: 'row-actions' }, editBtn)
      ];
    });

    return [
      pageHead('章节摘要', '摘要用于下一章衔接，替代整章正文以省 token。'),
      card('按章编辑摘要', el('div', { class: 'form-grid' },
        field('章号', chapterInput),
        field('摘要', summaryArea, '建议 80–150 字；留空保存等于清除', 'field-wide')),
        counter,
        el('div', { class: 'form-actions' }, saveBtn, deleteBtn)),
      card('已有摘要（' + list.length + '）',
        dataTable(['章', '摘要', '更新时间', '操作'], rows, '还没有章节摘要'))
    ];
  }

  // ---------------------------------------------------------------- 故事状态
  async function pageState(pid) {
    const base = '/api/projects/' + enc(pid) + '/story-state';
    const items = await api(base);
    const list = Array.isArray(items) ? items : [];
    const map = new Map(list.map((item) => [item.key, item]));

    const keyInput = el('input', { type: 'text', maxlength: 80, list: 'state-keys', placeholder: 'arc-status' });
    const typeSel = selectEl([['text', '文本'], ['json', 'JSON']], 'text');
    const valueArea = el('textarea', { rows: '5', spellcheck: 'false' });
    const saveBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '保存状态' });
    const datalist = el('datalist', { id: 'state-keys' },
      FIXED_STATE_KEYS.map((key) => el('option', { value: key })));

    const quick = el('div', { class: 'row-actions' },
      FIXED_STATE_KEYS.map((key) => {
        const btn = el('button', { class: 'btn btn-small', type: 'button', text: key });
        btn.addEventListener('click', () => {
          keyInput.value = key;
          const existing = map.get(key);
          if (existing) {
            if (typeof existing.value === 'string') {
              typeSel.value = 'text';
              valueArea.value = existing.value;
            } else {
              typeSel.value = 'json';
              valueArea.value = JSON.stringify(existing.value, null, 2);
            }
          } else {
            typeSel.value = 'text';
            valueArea.value = '';
          }
          valueArea.focus();
        });
        return btn;
      }));

    const form = el('form', { class: 'form-grid' },
      field('键', keyInput, '固定四键：' + FIXED_STATE_KEYS.join(' / ')),
      field('值类型', typeSel),
      field('值', valueArea, 'JSON 类型请填写合法 JSON', 'field-wide'),
      el('div', { class: 'field field-wide' }, quick),
      el('div', { class: 'field field-wide form-actions' }, saveBtn),
      datalist);

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const key = keyInput.value.trim();
      if (!key) {
        toast('请填写状态键', 'error');
        keyInput.focus();
        return;
      }
      let value;
      if (typeSel.value === 'json') {
        try {
          value = JSON.parse(valueArea.value);
        } catch (error) {
          toast('JSON 解析失败，请检查格式', 'error');
          valueArea.focus();
          return;
        }
      } else {
        value = valueArea.value;
      }
      await withBusy(saveBtn, async () => {
        await api(base + '/' + enc(key), { method: 'PUT', body: { value: value } });
        toast('已保存故事状态 ' + key, 'success');
        render();
      });
    }));

    const rows = list.map((item) => {
      const editBtn = el('button', { class: 'btn btn-small', type: 'button', text: '编辑' });
      editBtn.addEventListener('click', () => {
        keyInput.value = item.key;
        if (typeof item.value === 'string') {
          typeSel.value = 'text';
          valueArea.value = item.value;
        } else {
          typeSel.value = 'json';
          valueArea.value = JSON.stringify(item.value, null, 2);
        }
        keyInput.focus();
      });
      const remove = el('button', { class: 'btn btn-small btn-danger', type: 'button', text: '删除' });
      remove.addEventListener('click', handler(async () => {
        const answer = prompt('请输入状态键「' + item.key + '」以确认删除：');
        if (answer === null) return;
        if (answer !== item.key) {
          toast('确认串不匹配，已取消删除', 'error');
          return;
        }
        await withBusy(remove, async () => {
          await api(base + '/' + enc(item.key) + '?confirm=' + enc(item.key), { method: 'DELETE' });
          toast('已删除状态 ' + item.key, 'success');
          render();
        });
      }));
      const shown = typeof item.value === 'string'
        ? el('span', { text: item.value })
        : el('pre', { class: 'code-block', text: jsonText(item.value) });
      return [
        el('code', { text: item.key }),
        shown,
        formatTime(item.updated_at),
        el('div', { class: 'row-actions' }, editBtn, remove)
      ];
    });

    return [
      pageHead('故事状态', '固定四键 + 自定义键；值可以是文本或 JSON。'),
      card('写入 / 更新状态', form),
      card('已有状态（' + list.length + '）',
        dataTable(['键', '值', '更新时间', '操作'], rows, '还没有故事状态'))
    ];
  }

  // ---------------------------------------------------------------- 人物与设定
  function splitList(value) {
    return String(value || '').split(/[,，、\n]/).map((s) => s.trim()).filter(Boolean);
  }

  function entityKindLabel(kind) {
    return ({ character: '人物', world: '世界观', org: '组织', item: '道具', rule: '规则' })[kind] || kind;
  }

  const PRIORITY_LABELS = { always: '常驻', high: '重要', normal: '普通' };

  function priorityBadge(priority) {
    const key = PRIORITY_LABELS[priority] ? priority : 'normal';
    return el('span', { class: 'priority-badge priority-' + key,
      text: PRIORITY_LABELS[key] });
  }

  function anchorChips(anchors) {
    const list = Array.isArray(anchors) ? anchors.filter((a) => typeof a === 'string' && a) : [];
    if (!list.length) return el('span', { class: 'hint', text: '未设置锚点' });
    return el('div', { class: 'anchor-chips' },
      list.map((anchor) => el('span', { class: 'anchor-chip', text: anchor })));
  }

  function voiceSummary(item) {
    const voice = (item && item.voice) || {};
    const refs = Array.isArray(voice.self_ref) ? voice.self_ref : [];
    const tics = Array.isArray(voice.verbal_tics) ? voice.verbal_tics : [];
    const parts = [];
    if (refs.length) parts.push('自称 ' + refs.join('/'));
    if (tics.length) parts.push('口癖 ' + tics.join('/'));
    if (voice.sentence_max) parts.push('句长≤' + voice.sentence_max);
    return parts.join('；') || '—';
  }

  async function pageBible(pid) {
    const base = '/api/projects/' + enc(pid) + '/bible';
    const list = await api(base);
    const items = Array.isArray(list) ? list : [];
    const chars = items.filter((item) => item.kind === 'character');
    let editing = null;

    const kindSel = selectEl([
      ['character', '人物'], ['world', '世界观'], ['org', '组织'],
      ['item', '道具'], ['rule', '规则']
    ], 'character');
    const nameInput = el('input', { type: 'text', maxlength: 120, required: true, placeholder: '如：沈秋' });
    const roleInput = el('input', { type: 'text', maxlength: 200, placeholder: '如：主角 / 反派 / 引路人' });
    const summaryInput = el('textarea', { rows: 3, placeholder: '一句话设定、欲望、缺陷、秘密……' });
    const selfRefInput = el('input', { type: 'text', placeholder: '我，本人，老子（逗号分隔）' });
    const ticsInput = el('input', { type: 'text', placeholder: '哼，罢了，有意思（逗号分隔）' });
    const bannedInput = el('input', { type: 'text', placeholder: '阁下，您，随便（逗号分隔）' });
    const sentenceMaxInput = el('input', { type: 'number', min: '1', max: '200', step: '1', placeholder: '如：18' });
    const registerInput = el('input', { type: 'text', placeholder: '如：短句、冷幽默、书面腔' });
    const saveBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '保存人物卡' });
    const cancelBtn = el('button', { class: 'btn', type: 'button', text: '清空表单', hidden: true });

    function loadEditor(item) {
      editing = item || null;
      const voice = (item && item.voice) || {};
      kindSel.value = item ? item.kind : 'character';
      nameInput.value = item ? item.name : '';
      roleInput.value = (item && item.role) || '';
      summaryInput.value = (item && item.summary) || '';
      selfRefInput.value = Array.isArray(voice.self_ref) ? voice.self_ref.join('，') : '';
      ticsInput.value = Array.isArray(voice.verbal_tics) ? voice.verbal_tics.join('，') : '';
      bannedInput.value = Array.isArray(voice.banned_words) ? voice.banned_words.join('，') : '';
      sentenceMaxInput.value = voice.sentence_max ? String(voice.sentence_max) : '';
      registerInput.value = voice.register || '';
      saveBtn.textContent = editing ? '保存修改' : '保存人物卡';
      cancelBtn.hidden = !editing;
      if (editing) nameInput.focus();
    }

    cancelBtn.addEventListener('click', () => loadEditor(null));
    const form = el('form', { class: 'stack' },
      el('div', { class: 'form-grid' },
        field('类型', kindSel, '人物卡建议填语言指纹；世界观/组织/道具可只填摘要。'),
        field('名称', nameInput),
        field('定位', roleInput),
        field('句长上限', sentenceMaxInput, '超过这个字数的句子会被 OOC 检查标黄。'),
        field('自称', selfRefInput, '角色会怎么称呼自己。'),
        field('口癖', ticsInput, '遮住名字也能认出是谁。'),
        field('禁用词', bannedInput, '角色绝不会说的词。'),
        field('语域', registerInput, '短句、冷幽默、书面腔……')),
      field('摘要', summaryInput),
      el('div', { class: 'form-actions' }, saveBtn, cancelBtn));

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const name = nameInput.value.trim();
      if (!name) {
        toast('请填写名称', 'error');
        nameInput.focus();
        return;
      }
      const data = editing ? Object.assign({}, editing) : {};
      delete data.id; delete data.kind; delete data.name;
      data.role = roleInput.value.trim();
      data.summary = summaryInput.value.trim();
      const voice = {};
      const refs = splitList(selfRefInput.value);
      const tics = splitList(ticsInput.value);
      const banned = splitList(bannedInput.value);
      if (refs.length) voice.self_ref = refs;
      if (tics.length) voice.verbal_tics = tics;
      if (banned.length) voice.banned_words = banned;
      if (sentenceMaxInput.value) voice.sentence_max = Number(sentenceMaxInput.value);
      if (registerInput.value.trim()) voice.register = registerInput.value.trim();
      if (Object.keys(voice).length) data.voice = voice; else delete data.voice;
      await withBusy(saveBtn, async () => {
        await api(base, { method: 'POST', body: { kind: kindSel.value, name: name, data: data } });
        toast('已保存「' + name + '」', 'success');
        render();
      });
    }));

    function openMetaEditor(item) {
      const anchorsInput = el('input', { type: 'text',
        placeholder: '别名 / 地名 / 关键词（逗号分隔）' });
      anchorsInput.value = Array.isArray(item.anchors) ? item.anchors.join('，') : '';
      const prioritySel = selectEl([
        ['normal', '普通（命中才带入）'],
        ['high', '重要（优先带入）'],
        ['always', '常驻（每章都带入）']
      ], item.priority || 'normal');
      const firstInput = el('input', { type: 'number', min: '1', step: '1',
        placeholder: '如：3' });
      if (item.first_seen_chapter) firstInput.value = String(item.first_seen_chapter);
      const save = el('button', { class: 'btn btn-primary', type: 'button', text: '保存' });
      save.addEventListener('click', handler(async () => {
        await withBusy(save, async () => {
          const body = { anchors: splitList(anchorsInput.value), priority: prioritySel.value };
          if (firstInput.value.trim()) body.first_seen_chapter = Number(firstInput.value);
          await api(base + '/' + enc(item.kind) + '/' + enc(item.name) + '/meta',
            { method: 'PUT', body: body });
          toast('已更新「' + item.name + '」的激活设置', 'success');
          closeModal();
          render();
        });
      }));
      openModal({
        title: '锚点 / 优先级 · ' + item.name,
        body: el('div', { class: 'stack' },
          field('锚点', anchorsInput, '细纲或章节里出现这些词时，本条设定会被激活。'),
          field('优先级', prioritySel, '常驻条目每章都会进入输入包，谨慎使用。'),
          field('首次出现章', firstInput, '用于判断「近章出现」，留空则不改。')),
        actions: [save]
      });
    }

    const rows = items.map((item) => {
      const editBtn = el('button', { class: 'btn btn-small', type: 'button', text: '编辑' });
      editBtn.addEventListener('click', () => loadEditor(item));
      const metaBtn = el('button', { class: 'btn btn-small', type: 'button', text: '锚点/优先级' });
      metaBtn.addEventListener('click', () => openMetaEditor(item));
      const jsonBtn = el('button', { class: 'btn btn-small', type: 'button', text: 'JSON' });
      jsonBtn.addEventListener('click', () => openModal({
        title: item.name, wide: true,
        body: el('pre', { class: 'code-block', text: jsonText(item) })
      }));
      return [
        el('strong', { text: item.name }),
        entityKindLabel(item.kind),
        item.role || '—',
        item.kind === 'character' ? voiceSummary(item) : (item.summary || '—'),
        priorityBadge(item.priority),
        anchorChips(item.anchors),
        el('div', { class: 'row-actions' }, editBtn, metaBtn, jsonBtn)
      ];
    });

    return [
      pageHead('人物与设定', '把人物、世界观、组织、道具写成可检索的卡片；人物卡的语言指纹会直接用于 OOC 检查。'),
      card(editing ? '编辑卡片' : '新建卡片', form),
      card('已有条目（' + items.length + '）',
        dataTable(['名称', '类型', '定位', '语言指纹 / 摘要', '优先级', '锚点', '操作'],
          rows, '还没有人物或设定卡'),
        el('p', { class: 'hint', text: '优先级与锚点决定这一章会带入哪些设定；常驻条目每章都会进入输入包。' })),
      card('怎么用', el('ul', { class: 'plain-list' },
        el('li', { text: '人物卡至少填“自称 + 口癖 + 禁用词”，OOC 检查才有依据。' }),
        el('li', { text: '同一人物只保留一张卡；改名后用旧卡检查会提示找不到。' }),
        el('li', { text: '设定卡不参与 OOC 检查，但会进入输入包与检索。' })))
    ];
  }

  // ---------------------------------------------------------------- 叙事工作台
  function reportView(report) {
    if (!report || !report.verdict) return emptyState('还没有检查结果');
    const tone = { red: 'danger', yellow: 'warn', gray: 'muted', green: 'ok' }[report.verdict] || null;
    const kindMap = {
      voice: { red: '红色 · 建议改写', yellow: '黄色 · 建议复核', gray: '灰色 · 留意', green: '绿色 · 未命中' },
      structure: { red: '红色 · 结构风险', yellow: '黄色 · 建议调整', gray: '灰色 · 可优化', green: '绿色 · 结构稳' },
      voice_cross: { red: '红色 · 疑似串声', yellow: '黄色 · 归属不稳', gray: '灰色 · 留意', green: '绿色 · 未发现串声' },
      ooc: { red: '红色 · 建议改写', yellow: '黄色 · 建议复核', gray: '灰色 · 留意', green: '绿色 · 未命中' }
    };
    const labelMap = kindMap[report.kind] || { red: '红色 · 建议阻断导出', yellow: '黄色 · 建议复核', gray: '灰色 · 留意', green: '绿色 · 未命中' };
    const label = labelMap[report.verdict] || report.verdict;
    const layerLabels = { word: '词层', semantic: '语义层', event: '事件层',
      voice: '语言指纹', structure: '结构', ooc: 'OOC' };
    const catLabels = { voice: '语言', behavior: '行为', relation: '关系', ability: '能力' };
    const findings = (report.findings || []).map((item) => el('div', { class: 'finding finding-' + item.level },
      el('div', { class: 'finding-head' },
        badge(layerLabels[item.layer] || item.layer, item.level === 'red' ? 'danger' : (item.level === 'yellow' ? 'warn' : null)),
        item.category ? badge(catLabels[item.category] || item.category, item.level === 'red' ? 'danger' : null) : null,
        el('code', { class: 'finding-quote', text: item.quote })),
      el('p', { class: 'finding-why', text: item.why }),
      (item.alternatives && item.alternatives.length)
        ? el('ul', { class: 'plain-list' }, item.alternatives.map((alt) => el('li', { text: alt })))
        : null));
    const counts = report.counts || {};
    return el('div', { class: 'report' },
      el('div', { class: 'report-head' },
        badge(label, tone),
        el('span', { class: 'muted', text: '红 ' + (counts.red || 0) + ' · 黄 ' + (counts.yellow || 0) + ' · 灰 ' + (counts.gray || 0) + ' · ' + (report.checked_chars || (report.metrics && report.metrics.chars) || 0) + ' 字' })),
      report.export_blocked ? el('div', { class: 'notice notice-danger' },
        el('strong', { text: '导出已被阻断' }),
        el('p', { text: '最近一次红色预检会阻止这一章导出；改写命中项后重新预检并保存报告即可解锁。' })) : null,
      report.disclaimer ? el('p', { class: 'hint', text: report.disclaimer }) : null,
      findings.length ? el('div', { class: 'findings' }, findings) : emptyState('没有命中项'));
  }

  async function copyText(text) {
    if (!text) { toast('没有可复制的内容', 'error'); return; }
    try {
      await navigator.clipboard.writeText(text);
      toast('已复制到剪贴板', 'success');
    } catch (error) {
      const ta = el('textarea', { value: text });
      document.body.append(ta);
      ta.select();
      try { document.execCommand('copy'); toast('已复制到剪贴板', 'success'); }
      catch (e2) { toast('复制失败，请手动选择文本', 'error'); }
      ta.remove();
    }
  }

  function downloadText(filename, text) {
    const blob = new Blob([text], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = el('a', { href: url, download: filename });
    document.body.append(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function workbenchChapterLoader(ctx, textarea, select) {
    const btn = el('button', { class: 'btn btn-small', type: 'button', text: '载入章节' });
    btn.addEventListener('click', handler(async () => {
      if (!select.value) { toast('先选一章', 'error'); return; }
      await withBusy(btn, async () => {
        const ch = await api('/api/projects/' + enc(ctx.pid) + '/chapters/' + enc(select.value));
        textarea.value = (ch && ch.content) || '';
        toast('已载入第 ' + select.value + ' 章', 'success');
      });
    }));
    return btn;
  }

  // ---------------------------------------------------------------- 写作助手
  async function pageAssistant(pid) {
    const [stats, audit, chapters] = await Promise.all([
      api('/api/projects/' + enc(pid) + '/word-stats'),
      api('/api/projects/' + enc(pid) + '/token-audit?lookback=10'),
      api('/api/projects/' + enc(pid) + '/chapters')
    ]);
    const ctx = {
      pid: pid,
      stats: stats || {},
      audit: audit || {},
      chapters: Array.isArray(chapters) ? chapters : []
    };
    ctx.reloadTokens = async function () {
      ctx.audit = await api('/api/projects/' + enc(pid) + '/token-audit?lookback=10');
      active = 'tokens';
      draw();
    };
    let active = 'stats';
    const tabBar = el('div', { class: 'tab-bar' });
    const host = el('div', { class: 'tab-panel' });
    const tabs = [['stats', '字数统计'], ['tokens', '省 token'], ['inspiration', '灵感访谈']];
    const builders = {
      stats: assistantStats,
      tokens: assistantTokens,
      inspiration: assistantInspiration
    };
    function draw() {
      tabBar.replaceChildren(...tabs.map(([key, label]) => {
        const btn = el('button', { class: 'tab' + (active === key ? ' is-active' : ''),
          type: 'button', text: label });
        btn.addEventListener('click', () => { active = key; draw(); });
        return btn;
      }));
      host.replaceChildren(builders[active](ctx));
    }
    draw();
    return [
      pageHead('写作助手', '先量篇幅，再省 token，最后用问题把灵感问出来。三个工具都只读现有数据；只有你确认后才写摘要或保存灵感卡。'),
      tabBar,
      host
    ];
  }

  function assistantStats(ctx) {
    const totals = (ctx.stats && ctx.stats.totals) || {};
    const rows = ((ctx.stats && ctx.stats.chapters) || []).map((row) => [
      String(row.n),
      row.title,
      'v' + row.version,
      String(row.chars) + ' 字',
      String(row.paragraphs),
      String(row.sentences),
      String(row.approx_tokens),
      row.summary_chars ? String(row.summary_chars) + ' 字' : '未写'
    ]);
    const longest = ctx.stats && ctx.stats.longest;
    const shortest = ctx.stats && ctx.stats.shortest;
    return card('项目字数',
      statGrid({
        '字数（去空白含标点）': totals.chars || 0,
        '原始字符': totals.raw_chars || 0,
        '章节': totals.chapters || 0,
        '平均每章': totals.avg_chars || 0,
        '约 tokens': totals.approx_tokens || 0,
        '阅读分钟': totals.reading_minutes || 0,
        '摘要覆盖率': Math.round((totals.summary_coverage || 0) * 100) + '%'
      }),
      el('p', { class: 'hint', text: (ctx.stats && ctx.stats.note) || '' }),
      longest && shortest
        ? el('p', { class: 'hint', text: '最长：第 ' + longest.n + ' 章《' + longest.title + '》' + longest.chars + ' 字；最短：第 ' + shortest.n + ' 章《' + shortest.title + '》' + shortest.chars + ' 字。' })
        : null,
      dataTable(['章', '标题', '版本', '字数', '段落', '句数', '约 tokens', '摘要'], rows, '还没有章节'));
  }

  function assistantTokens(ctx) {
    const win = (ctx.audit && ctx.audit.window) || {};
    const totals = (ctx.audit && ctx.audit.totals) || {};
    const rows = (win.chapters || []).map((row) => [
      String(row.n),
      row.title,
      String(row.chars) + ' 字',
      String(row.approx_tokens),
      row.covered ? String(row.summary_chars) + ' 字' : '缺摘要',
      row.covered ? '可用' : '建议补'
    ]);
    const bulk = el('button', { class: 'btn btn-primary', type: 'button', text: '补齐缺失摘要（保存）' });
    bulk.disabled = !totals.missing;
    bulk.addEventListener('click', handler(async () => {
      await withBusy(bulk, async () => {
        const result = await api('/api/projects/' + enc(ctx.pid) + '/digests', {
          method: 'POST', body: { save: true, max_chars: 600 }
        });
        toast('已补齐 ' + (result.saved || 0) + ' 章摘要，预计省 ' + (result.saved_tokens || 0) + ' tokens', 'success');
        await ctx.reloadTokens();
      });
    }));
    const suggestions = (ctx.audit && ctx.audit.suggestions) || [];
    const suggestionBox = suggestions.length
      ? el('div', { class: 'notice' },
        el('strong', { text: '建议' }),
        el('ul', {}, suggestions.map((text) => el('li', { text: text }))))
      : null;
    return card('省 token 账',
      el('p', { class: 'hint', text: '用摘要替代整章正文喂给模型，是长篇写作里最稳的省 token 方式。缺摘要章节会按同一套抽取规则实算预览；短章抽不出更短摘要时会如实显示“可省 0”。' }),
      statGrid({
        '最近窗口': win.chapters ? win.chapters.length + ' 章' : '0 章',
        '全量 tokens': win.full_tokens || 0,
        '摘要 tokens': win.summary_tokens || 0,
        '可省 tokens': win.savings_tokens || 0,
        '摘要覆盖': Math.round((win.coverage || 0) * 100) + '%'
      }),
      el('div', { class: 'inline-form form-actions' }, bulk),
      el('p', { class: 'hint', text: '项目共 ' + (totals.chapters || 0) + ' 章；已覆盖 ' + (totals.covered || 0) + ' 章；缺摘要 ' + (totals.missing || 0) + ' 章。' }),
      dataTable(['章', '标题', '全量字数', '约 tokens', '摘要', '状态'], rows, '还没有章节'),
      suggestionBox);
  }

  function assistantInspiration(ctx) {
    const themeInput = el('input', { type: 'text', maxlength: '2000', placeholder: '例如：雾港、一封没有寄出的信、姐妹之间的隐瞒' });
    const chapterSel = selectEl(
      [['', '下一章（自动）']].concat(ctx.chapters.map((c) => [String(c.n), '第' + c.n + '章 ' + (c.title || '')])),
      '');
    const countSel = selectEl([['4', '4 题'], ['6', '6 题'], ['8', '8 题']], '6');
    const start = el('button', { class: 'btn btn-primary', type: 'button', text: '开始提问' });
    const stateBox = el('div', { id: 'inspirationState', class: 'revision-list' });
    let questions = [];
    let index = 0;
    let answers = [];
    let theme = '';

    function chapterValue() {
      return chapterSel.value === '' ? null : Number(chapterSel.value);
    }

    function renderQuestion() {
      if (index >= questions.length) {
        renderFinish();
        return;
      }
      const q = questions[index];
      const answer = el('textarea', { rows: '4', placeholder: q.placeholder || '写下具体动作、地点、代价；答不出可跳过。' });
      const skip = el('button', { class: 'btn', type: 'button', text: '跳过' });
      const next = el('button', { class: 'btn btn-primary', type: 'button', text: index === questions.length - 1 ? '完成答题' : '下一题' });
      skip.addEventListener('click', () => {
        index += 1;
        renderQuestion();
      });
      next.addEventListener('click', () => {
        const text = answer.value.trim();
        if (text) answers.push({ id: q.id, category: q.category, question: q.question, answer: text });
        index += 1;
        renderQuestion();
      });
      stateBox.replaceChildren(
        el('p', { class: 'hint', text: '第 ' + (index + 1) + ' / ' + questions.length + ' 题 · ' + (q.label || q.category) }),
        el('h3', { text: q.question }),
        el('p', { class: 'hint', text: '为什么问：' + (q.why || '—') }),
        el('div', { class: 'field field-wide' }, answer),
        el('div', { class: 'inline-form form-actions' }, skip, next)
      );
      answer.focus();
    }

    function renderFinish() {
      const make = el('button', { class: 'btn btn-primary', type: 'button', text: '生成并保存灵感卡' });
      const again = el('button', { class: 'btn', type: 'button', text: '重新出题' });
      make.disabled = !answers.length;
      make.addEventListener('click', handler(async () => {
        await withBusy(make, async () => {
          const result = await api('/api/projects/' + enc(ctx.pid) + '/inspiration/synthesize', {
            method: 'POST',
            body: {
              chapter_n: chapterValue(), theme: theme,
              answers: answers, save: true
            }
          });
          renderIdeas(result);
          toast('已保存 ' + (result.saved || []).length + ' 张灵感卡', 'success');
        });
      }));
      again.addEventListener('click', () => {
        questions = [];
        index = 0;
        answers = [];
        stateBox.replaceChildren();
      });
      stateBox.replaceChildren(
        el('p', { class: 'hint', text: '已回答 ' + answers.length + ' 题。可以生成灵感卡；也可以点「重新出题」换一组问题。' }),
        el('div', { class: 'inline-form form-actions' }, make, again)
      );
    }

    function ideaContent(content) {
      const box = el('div', { class: 'idea-content' });
      for (const raw of String(content || '').split('\n')) {
        const line = raw.trim();
        if (!line) continue;
        const match = line.match(/^\*\*([^*]+)\*\*(.*)$/);
        const p = el('p');
        if (match) {
          p.append(el('strong', { text: match[1] }), el('span', { text: match[2] }));
        } else {
          p.textContent = line;
        }
        box.append(p);
      }
      return box;
    }

    function renderIdeas(result) {
      const ideas = result.ideas || [];
      stateBox.replaceChildren(
        el('p', { class: 'hint', text: '已保存 ' + (result.saved || []).length + ' 张；可在「素材与知识库」按类型「灵感」查看。' }),
        ...ideas.map((idea) => el('article', { class: 'revision-issue' },
          el('div', { class: 'revision-head' }, badge('灵感', 'ok'), el('strong', { text: idea.title })),
          ideaContent(idea.content),
          idea.saved_id ? el('p', { class: 'hint', text: '素材 id：' + idea.saved_id }) : null)));
    }

    start.addEventListener('click', handler(async () => {
      await withBusy(start, async () => {
        theme = themeInput.value.trim();
        const result = await api('/api/projects/' + enc(ctx.pid) + '/inspiration/questions', {
          method: 'POST',
          body: { chapter_n: chapterValue(), theme: theme, count: Number(countSel.value) }
        });
        questions = result.questions || [];
        index = 0;
        answers = [];
        if (!questions.length) {
          stateBox.replaceChildren(emptyState('没有生成问题，请稍后再试。'));
          return;
        }
        renderQuestion();
      });
    }));

    return card('灵感访谈',
      el('p', { class: 'hint', text: '先由系统按项目人物、章节、未收伏笔和主题出题，你逐题作答；系统把回答组合成「目标×阻力」「秘密与关系反转」「可复用意象」「中段失败与章末钩子」四类灵感卡。问题是规则模板，不调用模型；答案越具体越可用。' }),
      el('div', { class: 'form-grid' },
        field('主题（可选）', themeInput, '留空则只按当前项目与章节出题', 'field-wide'),
        field('关联章节', chapterSel),
        field('问题数量', countSel),
        el('div', { class: 'field field-wide form-actions' }, start)),
      stateBox);
  }

  async function pageWorkbench(pid) {
    const meta = await api('/api/projects/' + enc(pid) + '/workbench');
    const chapters = await api('/api/projects/' + enc(pid) + '/chapters');
    const chars = await api('/api/projects/' + enc(pid) + '/bible?kind=character');
    const ctx = {
      pid: pid,
      base: '/api/projects/' + enc(pid) + '/workbench',
      chapters: Array.isArray(chapters) ? chapters : [],
      chars: Array.isArray(chars) ? chars : [],
      roles: (meta && meta.roles) || [],
      tricks: (meta && meta.tricks) || [],
      rules: (meta && meta.rules) || {},
      chapterOptions: [['', '手动粘贴 / 不关联章节']].concat(
        (Array.isArray(chapters) ? chapters : []).map((c) => [String(c.n), '第' + c.n + '章 ' + (c.title || '')])),
      charOptions: [['', '选择人物卡']].concat(
        (Array.isArray(chars) ? chars : []).map((c) => [c.name, c.name + (c.role ? ' · ' + c.role : '')]))
    };
    let active = 'compliance';
    const tabBar = el('div', { class: 'tab-bar' });
    const panelHost = el('div', { class: 'tab-panel' });
    const builders = {
      compliance: workbenchCompliance,
      voice: workbenchVoice,
      crossvoice: workbenchCrossVoice,
      ooc: workbenchOoc,
      expectation: workbenchExpectation,
      roles: workbenchRoles,
      chain: workbenchReviewChain,
      tricks: workbenchTricks,
      declaration: workbenchDeclaration
    };
    const tabs = [['compliance', '合规预检'], ['voice', '语言指纹'],
      ['crossvoice', '全章串声'], ['ooc', '行为/关系/能力'],
      ['expectation', '预期账本'], ['roles', '角色会诊'], ['chain', '审稿链'], ['tricks', '妙招'],
      ['declaration', 'AI 声明']];
    function draw() {
      tabBar.replaceChildren(...tabs.map(([key, label]) => {
        const btn = el('button', { class: 'tab' + (active === key ? ' is-active' : ''),
          type: 'button', text: label });
        btn.addEventListener('click', () => { active = key; draw(); });
        return btn;
      }));
      panelHost.replaceChildren(builders[active](ctx));
    }
    draw();
    return [
      pageHead('叙事工作台', '合规预检、语言指纹、全章串声、OOC 三类补全、预期账本、角色会诊、妙招与 AI 声明。所有检查只提示，不替你改正文。'),
      tabBar,
      panelHost
    ];
  }

  function workbenchCompliance(ctx) {
    const select = selectEl(ctx.chapterOptions, '');
    const textarea = el('textarea', { rows: 12, placeholder: '把本章正文粘进来；也可以先选章节再点“载入章节”。' });
    const loadBtn = workbenchChapterLoader(ctx, textarea, select);
    const checkBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '开始预检' });
    const result = el('div', { class: 'report-host' }, emptyState('预检结果会显示在这里。'));
    checkBtn.addEventListener('click', handler(async () => {
      if (!textarea.value.trim()) { toast('请先粘贴正文或载入章节', 'error'); textarea.focus(); return; }
      await withBusy(checkBtn, async () => {
        const out = await api(ctx.base + '/compliance', {
          method: 'POST',
          body: { text: textarea.value, chapter_n: select.value ? Number(select.value) : 0, save: true }
        });
        result.replaceChildren(reportView(out));
        toast(out.export_blocked ? '红色：导出会被阻断' : '预检完成', out.export_blocked ? 'error' : 'success');
      });
    }));
    return card('合规预检 · 三层扫描',
      el('div', { class: 'inline-form' }, field('关联章节', select), loadBtn, checkBtn),
      warningsBox([
        '规则版本 v' + (ctx.rules.version || '?') + '：' + (ctx.rules.name || '通用规则'),
        '词层 → 语义层 → 事件层（主体 + 行为 + 规模/组织）。红色报告会阻断该章导出。',
        ctx.rules.disclaimer || '本规则只做风险提示，不替代平台审核。'
      ], '规则说明'),
      textarea,
      result);
  }

  function workbenchVoice(ctx) {
    const select = selectEl(ctx.charOptions, '');
    const chapterSel = selectEl(ctx.chapterOptions, '');
    const textarea = el('textarea', { rows: 10, placeholder: '粘贴这个角色的一段对白或独白。' });
    const loadBtn = workbenchChapterLoader(ctx, textarea, chapterSel);
    const checkBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '检查 OOC' });
    const result = el('div', { class: 'report-host' }, emptyState('检查结果会显示在这里。'));
    checkBtn.addEventListener('click', handler(async () => {
      if (!select.value) { toast('先选一张人物卡', 'error'); return; }
      if (!textarea.value.trim()) { toast('请先粘贴文本', 'error'); textarea.focus(); return; }
      await withBusy(checkBtn, async () => {
        const out = await api(ctx.base + '/voice', {
          method: 'POST',
          body: { character: select.value, text: textarea.value,
            chapter_n: chapterSel.value ? Number(chapterSel.value) : 0, save: true }
        });
        result.replaceChildren(reportView(out));
        toast('OOC 检查完成', 'success');
      });
    }));
    const noChars = !ctx.chars.length;
    return card('OOC 检查 · 语言指纹',
      noChars ? el('div', { class: 'notice' },
        el('strong', { text: '还没有人物卡' }),
        el('p', { text: '先到“人物与设定”里建一张人物卡，填上自称、口癖、禁用词和句长上限。' }),
        el('a', { class: 'btn', href: '#/p/' + enc(ctx.pid) + '/bible', text: '去建卡' })) : null,
      el('div', { class: 'inline-form' }, field('人物卡', select), field('关联章节', chapterSel), loadBtn, checkBtn),
      textarea,
      result);
  }

  function workbenchCrossVoice(ctx) {
    const chapterSel = selectEl(ctx.chapterOptions, '');
    const textarea = el('textarea', { rows: 12, placeholder: '粘贴整章正文；系统会按引号和“角色：台词”切分，逐句比对所有人物的语言指纹。' });
    const loadBtn = workbenchChapterLoader(ctx, textarea, chapterSel);
    const checkBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '全章串声检查' });
    const result = el('div', { class: 'report-host' }, emptyState('串声结果会显示在这里。'));
    checkBtn.addEventListener('click', handler(async () => {
      if (!textarea.value.trim()) { toast('请先粘贴正文或载入章节', 'error'); textarea.focus(); return; }
      await withBusy(checkBtn, async () => {
        const out = await api('/api/projects/' + enc(ctx.pid) + '/voice-cross', {
          method: 'POST',
          body: { text: textarea.value,
            chapter_n: chapterSel.value ? Number(chapterSel.value) : 0, save: true }
        });
        const s = out.summary || {};
        const tone = out.verdict === 'red' ? 'danger' : (out.verdict === 'yellow' ? 'warn' : 'ok');
        const rows = (out.segments || []).map((seg) => [
          String(seg.line),
          seg.text,
          seg.label || '—',
          el('strong', { text: seg.predicted }),
          seg.margin >= 4 ? badge('高 ' + seg.margin, 'ok')
            : (seg.margin >= 2 ? badge('中 ' + seg.margin, 'warn') : badge('低 ' + seg.margin, 'danger')),
          (seg.issues || []).join('；') || (seg.mismatch ? '疑似串声' : '—')
        ]);
        result.replaceChildren(
          el('div', { class: 'report-head' },
            badge(out.verdict === 'red' ? '红 · 有疑似串声' : (out.verdict === 'yellow' ? '黄 · 归属不稳' : '绿 · 未发现串声'), tone),
            el('span', { class: 'muted', text: '共 ' + (s.total || 0) + ' 句 · 串声 ' + (s.mismatch || 0)
              + ' · 低置信 ' + (s.uncertain || 0) + ' · 高置信 ' + (s.high_confidence || 0) })),
          dataTable(['行', '台词', '标注', '更像', '分差', '问题'], rows, '没有识别到对白'),
          el('p', { class: 'hint', text: out.note || '' }));
        toast('串声检查完成', out.verdict === 'red' ? 'error' : 'success');
      });
    }));
    return card('全章串声 · 多角色交叉',
      el('p', { class: 'muted', text: '不是让模型“感觉像谁”，而是逐句比对自称、口癖、禁用词、句长与语域；分差小于 2 会标为低置信。' }),
      el('div', { class: 'inline-form' }, field('关联章节', chapterSel), loadBtn, checkBtn),
      textarea, result);
  }

  function workbenchOoc(ctx) {
    const select = selectEl(ctx.charOptions, '');
    const chapterSel = selectEl(ctx.chapterOptions, '');
    const textarea = el('textarea', { rows: 11, placeholder: '粘贴这个角色在本章的动作、对白与关系互动。' });
    const loadBtn = workbenchChapterLoader(ctx, textarea, chapterSel);
    const checkBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '检查行为 / 关系 / 能力' });
    const result = el('div', { class: 'report-host' }, emptyState('OOC 结果会显示在这里。'));
    checkBtn.addEventListener('click', handler(async () => {
      if (!select.value) { toast('先选一张人物卡', 'error'); return; }
      if (!textarea.value.trim()) { toast('请先粘贴文本', 'error'); textarea.focus(); return; }
      await withBusy(checkBtn, async () => {
        const out = await api('/api/projects/' + enc(ctx.pid) + '/ooc', {
          method: 'POST',
          body: { character: select.value, text: textarea.value,
            chapter_n: chapterSel.value ? Number(chapterSel.value) : 0, save: true }
        });
        const cats = out.categories || {};
        result.replaceChildren(
          el('div', { class: 'inline-form' },
            badge('语言 ' + (cats.voice || 0), cats.voice ? 'warn' : null),
            badge('行为 ' + (cats.behavior || 0), cats.behavior ? 'danger' : null),
            badge('关系 ' + (cats.relation || 0), cats.relation ? 'danger' : null),
            badge('能力 ' + (cats.ability || 0), cats.ability ? 'danger' : null)),
          reportView(out));
        toast('OOC 三类检查完成', 'success');
      });
    }));
    return card('OOC 补全 · 行为 / 关系 / 能力',
      el('div', { class: 'notice' },
        el('p', { text: '规则来自人物卡：data.ooc.behavior_rules 行为红线、data.relations[].forbidden 关系红线、data.ooc.ability_limits 能力边界；找不到规则就不下结论。' }),
        el('p', { class: 'hint', text: '示例：{"ooc":{"behavior_rules":["冷笑"],"ability_limits":[{"pattern":"飞檐走壁","why":"不会武功"}]},"relations":[{"target":"周砚","forbidden":["拔枪"]}]}' })),
      el('div', { class: 'inline-form' }, field('人物卡', select), field('关联章节', chapterSel), loadBtn, checkBtn),
      textarea, result);
  }

  function workbenchExpectation(ctx) {
    const maxChapter = ctx.chapters.length ? Math.max.apply(null, ctx.chapters.map((c) => Number(c.n) || 0)) : 0;
    const chapterInput = el('input', { type: 'number', min: '0', max: '100000', step: '1', value: String(maxChapter + 1) });
    const stateSelect = selectEl(opts(EXPECTATION_LABELS), 'delay');
    const noteInput = el('input', { type: 'text', maxlength: '2000', placeholder: '可选：补充作者决策或旧式备注' });
    const setupInput = el('input', { type: 'text', maxlength: '2000', placeholder: '读者被引导期待什么？' });
    const triggerInput = el('input', { type: 'text', maxlength: '2000', placeholder: '出现什么条件后才允许兑现？' });
    const payoffInput = el('input', { type: 'text', maxlength: '2000', placeholder: '真正完成承诺的可观察事件' });
    const evidenceInput = el('input', { type: 'text', maxlength: '2000', placeholder: '章节、正文位置或作者确认依据' });
    const addBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '登记一条' });
    const statsHost = el('div', { class: 'report-host' }, el('p', { class: 'loading', text: '读取预期账本…' }));

    async function refreshStats() {
      const out = await api('/api/projects/' + enc(ctx.pid) + '/expectations');
      const stats = out.stats || {};
      const counts = stats.counts || {};
      const streak = stats.streak || {};
      const audit = out.audit || { items: [], stats: {} };
      const issueById = new Map((audit.items || []).map((item) => [item.id, item.issues || []]));
      const rows = (stats.latest || []).map((row) => {
        const issues = issueById.get(row.id) || [];
        return [
          '第' + row.chapter_n + '章',
          EXPECTATION_LABELS[row.state] || row.state,
          row.setup || row.note || '—',
          row.trigger || '—',
          row.payoff_evidence || '—',
          issues.length ? issues.map((issue) => issue.message).join('；') : '记录完整'
        ];
      });
      statsHost.replaceChildren(...[
        el('div', { class: 'inline-form' },
          Object.keys(EXPECTATION_LABELS).map((key) => badge(
            EXPECTATION_LABELS[key] + ' ' + (counts[key] || 0),
            key === 'violate' && counts[key] ? 'danger' : (key === 'delay' && counts[key] ? 'warn' : null))),
          el('span', { class: 'muted', text: '连续 ' + (streak.state ? (EXPECTATION_LABELS[streak.state] || streak.state) + ' × ' + streak.length : '—') }),
          badge('待补证据 ' + ((audit.stats || {}).needs_evidence || 0),
            (audit.stats || {}).needs_evidence ? 'warn' : 'ok')),
        warningsBox(stats.warnings, '预期账本提醒'),
        el('p', { class: 'hint', text: audit.audit_scope || '只检查记录完整性。' }),
        dataTable(['章', '状态', '承诺', '触发条件', '兑现证据', '记录检查'], rows, '还没有登记预期')
      ].filter(Boolean));
    }

    addBtn.addEventListener('click', handler(async () => {
      if (chapterInput.value === '') { toast('请填章号', 'error'); return; }
      await withBusy(addBtn, async () => {
        await api('/api/projects/' + enc(ctx.pid) + '/expectations', {
          method: 'POST',
          body: {
            chapter_n: Number(chapterInput.value), state: stateSelect.value,
            note: noteInput.value.trim(), setup: setupInput.value.trim(),
            trigger: triggerInput.value.trim(), payoff: payoffInput.value.trim(),
            payoff_evidence: evidenceInput.value.trim()
          }
        });
        for (const input of [noteInput, setupInput, triggerInput, payoffInput, evidenceInput]) input.value = '';
        toast('已登记第 ' + chapterInput.value + ' 章的预期', 'success');
        await refreshStats();
      });
    }));

    refreshStats().catch(reportError);
    return card('预期账本 · 承诺—触发—兑现',
      el('div', { class: 'notice' },
        el('p', { text: '四态仍是满足 / 延迟 / 换维 / 违背；新增字段把“制造承诺、允许兑现的条件、真正兑现的事件与证据”分开。' }),
        el('p', { class: 'hint', text: '审计只检查记录缺口：满足但无兑现证据、延迟但无触发条件会被标记；不会仅凭语义相近自动关闭伏笔。' })),
      el('div', { class: 'form-grid' },
        field('章号', chapterInput), field('状态', stateSelect),
        field('承诺 / 读者预期', setupInput), field('备注', noteInput),
        field('触发条件', triggerInput), field('兑现事件', payoffInput),
        field('兑现证据', evidenceInput, null, 'field-wide')),
      el('div', { class: 'form-actions' }, addBtn),
      statsHost);
  }

  function workbenchRoles(ctx) {
    const selected = new Set(ctx.roles.length ? [ctx.roles[0].id] : []);
    const grid = el('div', { class: 'role-grid' });
    for (const role of ctx.roles) {
      const box = el('button', {
        class: 'role-card' + (selected.has(role.id) ? ' is-active' : ''),
        type: 'button'
      },
        el('strong', { text: role.name }),
        el('span', { class: 'role-tagline', text: role.tagline }),
        el('span', { class: 'role-focus', text: role.focus }));
      box.addEventListener('click', () => {
        if (selected.has(role.id)) selected.delete(role.id); else selected.add(role.id);
        box.classList.toggle('is-active', selected.has(role.id));
      });
      grid.append(box);
    }
    const textarea = el('textarea', { rows: 9, placeholder: '粘贴要会诊的章节或片段。' });
    const contextInput = el('input', { type: 'text', placeholder: '可选：这一章在全书的位置、当前冲突、需要保守的设定' });
    const runBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '生成会诊提示词' });
    const output = el('textarea', { rows: 12, readonly: true, placeholder: '生成后可直接复制给任意模型。' });
    const copyBtn = el('button', { class: 'btn', type: 'button', text: '复制提示词' });
    copyBtn.addEventListener('click', () => copyText(output.value));
    runBtn.addEventListener('click', handler(async () => {
      if (!selected.size) { toast('至少选一个角色', 'error'); return; }
      if (!textarea.value.trim()) { toast('请先粘贴文本', 'error'); textarea.focus(); return; }
      await withBusy(runBtn, async () => {
        const parts = [];
        for (const id of selected) {
          const out = await api(ctx.base + '/role-prompt', {
            method: 'POST', body: { role: id, text: textarea.value, context: contextInput.value }
          });
          parts.push(out.prompt);
        }
        output.value = parts.join('\n\n---\n\n');
        toast('已生成 ' + selected.size + ' 个角色的会诊提示词', 'success');
      });
    }));
    return card('角色会诊 · 8 个审稿视角',
      el('p', { class: 'muted', text: '每个角色只回答自己的问题；先判断，再给证据，最后给最小改法。' }),
      grid,
      field('项目上下文', contextInput),
      textarea,
      el('div', { class: 'form-actions' }, runBtn, copyBtn),
      output);
  }

  function workbenchReviewChain(ctx) {
    const roleSelect = selectEl(ctx.roles.map((role) => [role.id, role.name]),
      ctx.roles.length ? ctx.roles[0].id : 'structure-editor');
    const chapterSel = selectEl(ctx.chapterOptions, '');
    const contextInput = el('input', { type: 'text', placeholder: '可选：本章位置、必须保守的设定、已知资料边界' });
    const textarea = el('textarea', { rows: 10, placeholder: '粘贴要审稿的章节或片段；也可以载入章节。' });
    const loadBtn = workbenchChapterLoader(ctx, textarea, chapterSel);
    const runBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '生成四步审稿链' });
    const copyBtn = el('button', { class: 'btn', type: 'button', text: '复制完整提示链' });
    const stepsHost = el('div', { class: 'stack' }, emptyState('生成后会显示每一步的目标、停止条件和交接字段。'));
    const output = el('textarea', { rows: 14, readonly: true, placeholder: '这里只生成提示词，不调用外部模型，也不会自动改正文。' });
    copyBtn.addEventListener('click', () => copyText(output.value));
    runBtn.addEventListener('click', handler(async () => {
      if (!textarea.value.trim()) { toast('请先粘贴文本', 'error'); textarea.focus(); return; }
      await withBusy(runBtn, async () => {
        const out = await api(ctx.base + '/review-chain', {
          method: 'POST',
          body: { role: roleSelect.value, text: textarea.value, context: contextInput.value }
        });
        output.value = out.prompt || '';
        stepsHost.replaceChildren(...(out.steps || []).map((step) => {
          const copyStep = el('button', { class: 'btn btn-small', type: 'button', text: '复制本步' });
          copyStep.addEventListener('click', () => copyText(step.prompt || ''));
          return el('section', { class: 'notice' },
            el('div', { class: 'material-head' }, el('strong', { text: step.name }), copyStep),
            el('p', { text: step.goal }),
            el('p', { class: 'hint', text: '输出：' + step.output }),
            el('p', { class: 'hint', text: '停止：' + step.stop }),
            el('p', { class: 'hint', text: '交接：' + step.handoff }));
        }));
        toast('四步审稿链已生成；请逐段检查交接物', 'success');
      });
    }));
    return card('审稿提示链 · 证据 → 审稿 → 最小修订 → 自检',
      el('div', { class: 'notice' },
        el('p', { text: '每一步只承担一个目标；上一步的结构化输出是下一步的输入。缺证据就停止或标“需作者确认”，不硬写结论。' }),
        el('p', { class: 'hint', text: '本功能不调用外部模型、不自动改写正文；最终是否采用补丁由作者决定。' })),
      el('div', { class: 'inline-form' }, field('审稿角色', roleSelect), field('载入章节', chapterSel), loadBtn),
      field('项目上下文', contextInput), textarea,
      el('div', { class: 'form-actions' }, runBtn, copyBtn),
      stepsHost, output);
  }

  function workbenchTricks(ctx) {
    let selected = ctx.tricks.length ? ctx.tricks[0].id : '';
    const grid = el('div', { class: 'trick-grid' });
    const cards = [];
    const info = el('div', { class: 'notice' }, emptyState('选一个妙招'));
    function refreshInfo() {
      const trick = ctx.tricks.find((t) => t.id === selected);
      if (!trick) { info.replaceChildren(emptyState('选一个妙招')); return; }
      info.replaceChildren(el('strong', { text: trick.name + ' · ' + trick.use }),
        el('ol', { class: 'plain-list' }, trick.steps.map((s) => el('li', { text: s }))),
        el('p', { class: 'hint', text: '产出：' + trick.output }));
    }
    for (const trick of ctx.tricks) {
      const box = el('button', {
        class: 'trick-card' + (trick.id === selected ? ' is-active' : ''),
        type: 'button'
      }, el('strong', { text: trick.name }), el('span', { class: 'role-tagline', text: trick.use }));
      box.dataset.id = trick.id;
      box.addEventListener('click', () => {
        selected = trick.id;
        for (const c of cards) c.classList.toggle('is-active', c.dataset.id === selected);
        refreshInfo();
      });
      cards.push(box);
      grid.append(box);
    }
    refreshInfo();
    const textarea = el('textarea', { rows: 8, placeholder: '粘贴要处理的那一段。' });
    const runBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '生成妙招提示词' });
    const output = el('textarea', { rows: 10, readonly: true, placeholder: '生成后可直接复制给任意模型。' });
    const copyBtn = el('button', { class: 'btn', type: 'button', text: '复制提示词' });
    copyBtn.addEventListener('click', () => copyText(output.value));
    runBtn.addEventListener('click', handler(async () => {
      if (!selected) { toast('先选一个妙招', 'error'); return; }
      if (!textarea.value.trim()) { toast('请先粘贴文本', 'error'); textarea.focus(); return; }
      await withBusy(runBtn, async () => {
        const out = await api(ctx.base + '/trick-prompt', {
          method: 'POST', body: { trick: selected, text: textarea.value }
        });
        output.value = out.prompt;
        toast('已生成「' + out.trick.name + '」提示词', 'success');
      });
    }));
    return card('妙招 · 12 个写作动作',
      grid, info, textarea,
      el('div', { class: 'form-actions' }, runBtn, copyBtn), output);
  }

  function workbenchDeclaration(ctx) {
    const runBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '生成 AI 使用声明' });
    const output = el('textarea', { rows: 16, readonly: true, placeholder: '生成后可复制或下载为 Markdown。' });
    const copyBtn = el('button', { class: 'btn', type: 'button', text: '复制' });
    const downloadBtn = el('button', { class: 'btn', type: 'button', text: '下载 .md' });
    copyBtn.addEventListener('click', () => copyText(output.value));
    downloadBtn.addEventListener('click', () => {
      if (!output.value) { toast('先生成声明', 'error'); return; }
      downloadText('AI使用声明-' + ctx.pid + '.md', output.value);
    });
    runBtn.addEventListener('click', handler(async () => {
      await withBusy(runBtn, async () => {
        const out = await api(ctx.base + '/ai-declaration');
        output.value = out.markdown || '';
        toast('已按项目统计生成声明', 'success');
      });
    }));
    return card('AI 使用声明',
      el('p', { class: 'muted', text: '按项目当前章节数、字数、素材数、审校记录生成草稿；发布前请按平台最新规则复核。' }),
      el('div', { class: 'form-actions' }, runBtn, copyBtn, downloadBtn),
      output);
  }

  // ---------------------------------------------------------------- 创作台 / 故事图谱
  function downloadApi(path, fallbackName) {
    return fetch(path, { headers: { 'X-Longform-Token': session.token } })
      .then(async (response) => {
        if (!response.ok) {
          let message = '下载失败（HTTP ' + response.status + '）';
          try {
            const data = await response.json();
            if (data && data.error) message = data.error;
          } catch (error) { /* 非 JSON 错误体 */ }
          throw new ApiError(message, response.status);
        }
        const disposition = response.headers.get('Content-Disposition') || '';
        const match = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
        const filename = match ? decodeURIComponent(match[1]) : fallbackName;
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const a = el('a', { href: url, download: filename });
        document.body.append(a);
        a.click();
        a.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast('已下载 ' + filename, 'success');
      });
  }

  function chapterStatusBadge(status) {
    const map = { draft: ['草稿', null], revise: ['修订', 'warn'],
      approved: ['通过', 'ok'], blocked: ['阻塞', 'danger'] };
    const pair = map[status] || [status || '草稿', null];
    return badge(pair[0], pair[1]);
  }

  function riskDot(risk, blocked) {
    const tone = blocked || risk === 'red' ? 'red' : (risk === 'yellow' ? 'yellow'
      : (risk === 'green' ? 'green' : 'gray'));
    return el('span', { class: 'risk-dot risk-' + tone, title: risk || '未预检' });
  }

  function structureView(out) {
    if (!out || !out.metrics) return emptyState('结构体检结果会显示在这里');
    const metrics = out.metrics;
    const pairs = [
      ['字数', metrics.chars],
      ['段落', metrics.paragraphs],
      ['句子', metrics.sentences],
      ['对白占比', Math.round((metrics.dialogue_ratio || 0) * 100) + '%'],
      ['平均句长', metrics.avg_sentence_len],
      ['超长句', metrics.long_sentences],
      ['场景标记', metrics.scene_markers],
      ['章末钩子', metrics.hook_score + '/3'],
      ['主题句', metrics.theme_hits],
      ['重复开头', metrics.repeated_openings]
    ];
    const bars = (out.segments || []).map((seg) => el('div', {
      class: 'emotion-bar',
      title: '第 ' + seg.index + ' 段 · ' + seg.score,
      style: 'height:' + Math.max(4, seg.score) + '%'
    }));
    const tone = { red: 'danger', yellow: 'warn', gray: 'muted', green: 'ok' }[out.verdict] || null;
    return el('div', { class: 'report' },
      el('div', { class: 'report-head' },
        badge('结构 · ' + ({ red: '红', yellow: '黄', gray: '灰', green: '绿' }[out.verdict] || out.verdict), tone),
        el('span', { class: 'muted', text: '情绪曲线按文本位置切 10 段' })),
      el('div', { class: 'metric-grid' }, pairs.map((pair) => el('div', { class: 'metric' },
        el('span', { class: 'metric-value', text: String(pair[1]) }),
        el('span', { class: 'metric-label', text: pair[0] })))),
      el('div', { class: 'emotion-chart' }, bars),
      reportView(out));
  }

  function pipelineView(pipe, pid) {
    if (!pipe || !pipe.steps) return emptyState('流水线状态读取失败');
    const hrefFor = (step) => {
      if (step.section === 'studio') return '#/p/' + enc(pid) + '/studio/' + enc(pipe.chapter_n);
      if (step.section === 'outline') return '#/p/' + enc(pid) + '/outline';
      if (step.section === 'workbench') return '#/p/' + enc(pid) + '/workbench';
      return '#/p/' + enc(pid) + '/studio/' + enc(pipe.chapter_n);
    };
    return el('div', { class: 'pipeline' },
      el('div', { class: 'pipeline-head' },
        el('strong', { text: '第 ' + pipe.chapter_n + ' 章 · 完成 ' + pipe.percent + '%' }),
        badge(pipe.next_step ? '下一步：' + pipe.next_step : '全部完成', pipe.next_step ? 'warn' : 'ok')),
      el('ol', { class: 'pipeline-steps' }, pipe.steps.map((step) => {
        const cls = 'pipeline-step ' + (step.done ? 'is-done' : (step.status === 'blocked' ? 'is-blocked' : ''));
        return el('li', { class: cls },
          el('span', { class: 'pipeline-dot' }),
          el('div', { class: 'pipeline-body' },
            el('a', { href: hrefFor(step), text: step.label }),
            el('span', { class: 'pipeline-detail', text: step.detail })));
      })));
  }

  // ---------------------------------------------------------------- 自动化驾驶舱
  const RUN_STATUS_LABELS = {
    pending: '排队', running: '运行中', awaiting_human: '等待人工',
    blocked: '阻断', failed: '失败', done: '完成', cancelled: '已取消'
  };
  const RUN_STATUS_TONES = {
    pending: 'muted', running: 'warn', awaiting_human: 'warn',
    blocked: 'danger', failed: 'danger', done: 'ok', cancelled: 'muted'
  };
  const RUN_STEP_ORDER = [
    ['plan', '取章纲'], ['assemble', '组输入'], ['draft', '写正文'],
    ['lint', 'L0 检查'], ['summary', '写摘要'], ['commit', '定稿']
  ];
  const RUN_STEP_LABELS = {
    pending: '未开始', running: '进行中', done: '完成',
    blocked: '阻断', failed: '失败', skipped: '跳过'
  };

  function runStatusBadge(status) {
    return badge(RUN_STATUS_LABELS[status] || status, RUN_STATUS_TONES[status] || null);
  }

  async function pageAutomation(pid) {
    const base = '/api/projects/' + enc(pid);
    let data = await api(base + '/automation');
    let runs = (data.runs || []).filter((run) => run.kind !== 'batch');
    let batches = Array.isArray(data.batches) ? data.batches : [];
    let batchStates = {};
    let runUsage = { groups: [] };
    let nextAction = null;
    let pendingHandoffs = [];

    async function loadExtras() {
      const out = await Promise.allSettled([
        api(base + '/usage?by=run', { quiet: true }),
        api(base + '/autopilot', { quiet: true }),
        api(base + '/handoffs?status=pending', { quiet: true })
      ]);
      runUsage = (out[0].status === 'fulfilled' && out[0].value) ? out[0].value : { groups: [] };
      nextAction = (out[1].status === 'fulfilled') ? out[1].value : null;
      const listed = (out[2].status === 'fulfilled' && out[2].value
        && Array.isArray(out[2].value.handoffs)) ? out[2].value.handoffs : [];
      pendingHandoffs = listed;
    }
    await loadExtras();
    let selectedId = runs.length ? runs[0].id : null;
    let detail = null;
    let lintData = (data.latest && data.latest.lint) || null;

    async function loadDetail(id) {
      detail = id ? await api('/api/runs/' + enc(id)) : null;
    }
    if (selectedId) await loadDetail(selectedId);

    async function loadBatchStates() {
      const next = {};
      await Promise.all(batches.map(async (batch) => {
        try {
          const status = await api('/api/batches/' + enc(batch.id), { quiet: true });
          let childDetail = null;
          const cid = status && status.run && status.run.target
            ? status.run.target.current_child : null;
          if (cid) {
            try {
              childDetail = await api('/api/runs/' + enc(cid), { quiet: true });
            } catch (error) {
              if (!(error instanceof ApiError)) reportError(error);
            }
          }
          next[batch.id] = { status: status, childDetail: childDetail };
        } catch (error) {
          if (!(error instanceof ApiError)) reportError(error);
          next[batch.id] = null;
        }
      }));
      batchStates = next;
    }
    await loadBatchStates();

    const statusHost = el('div');
    const detailHost = el('div');
    const lintHost = el('div');
    const historyHost = el('div');
    const batchHost = el('div');

    function runTokenMap() {
      const map = {};
      for (const group of (runUsage.groups || [])) {
        map[group.key] = (group.input_tokens || 0) + (group.output_tokens || 0);
      }
      return map;
    }

    function statCell(label, value) {
      return el('div', { class: 'stat' },
        el('div', { class: 'stat-value', text: String(value) }),
        el('div', { class: 'stat-label', text: label }));
    }

    function stageLabel() {
      const pipe = data.pipeline || {};
      const next = (pipe.steps || []).find((step) => step.id === pipe.next_step);
      if (next) return next.label || next.id;
      return (pipe.steps || []).length ? '全部就绪' : '—';
    }

    function drawStatus() {
      const pipe = data.pipeline || {};
      const usage = data.usage || {};
      const count = (names) => runs.filter((run) => names.indexOf(run.status) >= 0).length;
      const nextLine = el('div', { class: 'auto-status-next' },
        nextAction ? nextActionBadge(nextAction.action) : badge('未读取', 'muted'),
        el('span', { class: 'muted', text: nextAction && nextAction.why
          ? nextAction.why : '暂时无法读取下一步，可稍后刷新。' }),
        pendingHandoffs.length
          ? el('a', {
            class: 'btn btn-small', href: projectHash(pid, 'aistudio', 'aistudio'),
            text: '待提交 ' + pendingHandoffs.length + ' 张 · 去 AI Studio 提交'
          })
          : badge('无待提交', 'ok'));
      statusHost.replaceChildren(
        nextLine,
        el('div', { class: 'stat-grid' },
          statCell('当前阶段', stageLabel()),
          statCell('下一章', pipe.chapter_n ? '第' + pipe.chapter_n + '章' : '—'),
          statCell('运行中', count(['pending', 'running'])),
          statCell('等待人工', count(['awaiting_human'])),
          statCell('阻断 / 失败', count(['blocked', 'failed'])),
          statCell('累计 tokens', usage.total_tokens || 0)));
    }

    async function decide(decision, message) {
      await api('/api/runs/' + enc(selectedId) + '/resume',
        { method: 'POST', body: { decision: decision } });
      toast(message, 'success');
      await refreshAll(selectedId);
    }

    function assembleTraceNode(runDetail) {
      const steps = {};
      for (const row of ((runDetail && runDetail.steps) || [])) steps[row.name] = row;
      const traceRow = steps['assemble'];
      const trace = traceRow && traceRow.output && traceRow.output.trace;
      if (!trace) return null;
      const activated = Array.isArray(trace.activated) ? trace.activated : [];
      const dropped = Array.isArray(trace.dropped) ? trace.dropped : [];
      const ratio = Math.round((Number((trace.budget || {}).ratio) || 0) * 100);
      const names = activated.slice(0, 3).map((item) => item.name || item.id)
        .filter(Boolean);
      return el('details', { class: 'run-trace' },
        el('summary', { class: 'run-trace-line',
          text: '设定激活 ' + activated.length + ' / 丢弃 ' + dropped.length
            + ' · 预算 ' + ratio + '%' }),
        names.length
          ? el('p', { class: 'hint', text: '激活：' + names.join('、') })
          : null);
    }

    function drawDetail() {
      if (!selectedId || !detail || !detail.run) {
        detailHost.replaceChildren(emptyState(data.latest
          ? '还没有运行记录；用上方表单开始一次自动化运行。'
          : '先建项目与章纲'));
        return;
      }
      const run = detail.run;
      const target = run.target || {};
      const usage = detail.usage || {};
      const steps = {};
      for (const row of (detail.steps || [])) steps[row.name] = row;

      const timeline = el('ol', { class: 'run-steps' });
      for (const pair of RUN_STEP_ORDER) {
        const row = steps[pair[0]];
        const status = row ? row.status : 'pending';
        const tone = status === 'done' ? ' is-done'
          : (status === 'blocked' ? ' is-blocked'
            : (status === 'failed' ? ' is-failed'
              : (status === 'running' ? ' is-running' : '')));
        timeline.append(el('li', { class: 'run-step' + tone },
          el('span', { class: 'run-step-dot' }),
          el('div', { class: 'run-step-body' },
            el('strong', { text: pair[1] }),
            el('span', { class: 'run-step-status', text: RUN_STEP_LABELS[status] || status }),
            row && row.error ? el('p', { class: 'hint', text: row.error }) : null)));
      }

      const traceBox = assembleTraceNode(detail);

      const usageGrid = el('div', { class: 'stat-grid auto-usage' },
        statCell('本次 tokens', usage.total_tokens || 0),
        statCell('输入', usage.total_input_tokens || 0),
        statCell('输出', usage.total_output_tokens || 0),
        statCell('缓存', usage.total_cached_tokens || 0));

      const actions = el('div', { class: 'inline-form form-actions' });
      const refresh = el('button', { class: 'btn btn-small', type: 'button', text: '刷新' });
      refresh.addEventListener('click', handler(async () => {
        await withBusy(refresh, () => refreshAll(selectedId));
      }));
      actions.append(refresh);
      if (detail.handoff && detail.handoff.status === 'pending') {
        actions.prepend(el('a', {
          class: 'btn btn-primary btn-small',
          href: projectHash(pid, 'aistudio', 'aistudio'),
          text: '去 AI Studio 提交'
        }));
      }
      if (run.status === 'awaiting_human') {
        const approve = el('button', { class: 'btn btn-primary btn-small', type: 'button', text: '批准定稿' });
        const reject = el('button', { class: 'btn btn-danger btn-small', type: 'button', text: '驳回' });
        approve.addEventListener('click', handler(async () => {
          await withBusy(approve, () => decide('approve', '已批准定稿'));
        }));
        reject.addEventListener('click', handler(async () => {
          await withBusy(reject, () => decide('reject', '已驳回'));
        }));
        actions.prepend(approve, reject);
      }
      if (['pending', 'running', 'awaiting_human', 'blocked', 'failed'].indexOf(run.status) >= 0) {
        const cancel = el('button', { class: 'btn btn-small', type: 'button', text: '取消运行' });
        cancel.addEventListener('click', handler(async () => {
          await withBusy(cancel, async () => {
            await api('/api/runs/' + enc(selectedId) + '/cancel', { method: 'POST', body: {} });
            toast('运行已取消', 'success');
            await refreshAll(selectedId);
          });
        }));
        actions.append(cancel);
      }

      const nodes = [
        el('div', { class: 'automation-run-head' },
          runStatusBadge(run.status),
          el('strong', { text: run.id }),
          el('span', { class: 'muted', text: '目标：' + (target.chapter_n ? '第' + target.chapter_n + '章 ' : '') + (target.title || '') }),
          el('span', { class: 'muted', text: 'tokens ' + (usage.total_tokens || 0) }),
          el('span', { class: 'muted', text: formatTime(run.created_at) }))
      ];
      if (run.error) {
        nodes.push(el('div', { class: 'notice notice-danger' },
          el('strong', { text: '错误' }), el('p', { text: run.error })));
      }
      if (detail.handoff) {
        nodes.push(el('p', { class: 'hint', text: '交接单 ' + displayText(detail.handoff.id)
          + ' · ' + displayText(detail.handoff.kind) + ' · ' + displayText(detail.handoff.status) }));
      }
      nodes.push(timeline);
      if (traceBox) nodes.push(traceBox);
      nodes.push(usageGrid, actions);
      detailHost.replaceChildren(...nodes);
    }

    function drawLint() {
      if (!lintData) {
        lintHost.replaceChildren(emptyState('先建项目与章纲'));
        return;
      }
      const findings = lintData.findings || [];
      const counts = lintData.counts || {};
      const recheck = el('button', { class: 'btn btn-small', type: 'button', text: '重新检查' });
      recheck.addEventListener('click', handler(async () => {
        await withBusy(recheck, async () => {
          lintData = await api(base + '/lint?chapter_n=' + enc(String(lintData.chapter_n)));
          toast('L0 检查已刷新', 'success');
          drawLint();
        });
      }));
      const list = el('div', { class: 'findings' });
      for (const finding of findings) {
        const block = finding.level === 'block';
        list.append(el('div', { class: 'finding ' + (block ? 'finding-red' : 'finding-yellow') },
          el('div', { class: 'finding-head' },
            badge(finding.rule || 'rule', block ? 'danger' : 'warn'),
            badge(block ? '阻断' : '提醒', block ? 'danger' : 'warn'),
            finding.locator ? el('code', { text: finding.locator }) : null),
          finding.evidence ? el('p', { class: 'finding-quote', text: finding.evidence }) : null,
          el('p', { class: 'hint', text: finding.suggestion || '' })));
      }
      if (!findings.length) list.append(emptyState('本章 L0 检查通过，没有发现硬伤。'));
      lintHost.replaceChildren(
        el('div', { class: 'inline-form' },
          badge('阻断 ' + (counts.block || 0), counts.block ? 'danger' : 'ok'),
          badge('提醒 ' + (counts.warn || 0), counts.warn ? 'warn' : 'ok'),
          el('span', { class: 'muted', text: '第 ' + lintData.chapter_n + ' 章' }),
          recheck),
        list);
    }

    function drawHistory() {
      if (!runs.length) {
        historyHost.replaceChildren(emptyState('还没有运行记录'));
        return;
      }
      const tokenMap = runTokenMap();
      const rows = runs.map((run) => {
        const target = run.target || {};
        const link = el('button', {
          class: 'linklike' + (run.id === selectedId ? ' is-active' : ''),
          type: 'button', text: run.id
        });
        link.addEventListener('click', handler(async () => {
          selectedId = run.id;
          await loadDetail(run.id);
          drawDetail();
          drawHistory();
        }));
        return [link, el('span', { text: formatTime(run.created_at) }),
          runStatusBadge(run.status),
          target.chapter_n ? '第' + target.chapter_n + '章' : '—',
          String(tokenMap[run.id] || 0)];
      });
      historyHost.replaceChildren(
        dataTable(['运行 id', '时间', '状态', '章节', 'tokens'], rows, '还没有运行记录'));
    }

    async function batchAction(batchId, action, payload, message, button) {
      await withBusy(button, async () => {
        const out = await api('/api/batches/' + enc(batchId) + '/' + action,
          { method: 'POST', body: payload || {} });
        const state = out && out.run ? out.run.status : '';
        toast(action === 'cancel' && state !== 'cancelled'
          ? '批次已结束，无需取消' : message, 'success');
        await refreshAll();
      });
    }

    function drawBatches() {
      if (!batches.length) {
        batchHost.replaceChildren(emptyState('还没有整本批跑批次',
          '用上方「整本批跑」连续跑多个章节；批次会在这里显示进度与子运行。'));
        return;
      }
      const cards = batches.slice(0, 5).map((batch) => {
        const state = batchStates[batch.id];
        const status = state && state.status;
        const run = (status && status.run) || batch;
        const target = run.target || {};
        const total = Number(target.chapters) || 0;
        const startN = Number(target.start_chapter) || 1;
        const endN = total ? startN + total - 1 : startN;
        const current = Math.min(Number(target.current_chapter) || startN, endN);
        const used = Number((status && status.usage && status.usage.total_tokens) || 0);
        const budget = Number((run.budget && run.budget.tokens) || 0);
        const children = (status && status.children) || [];

        const actions = el('div', { class: 'row-actions' });
        if (run.status === 'awaiting_human') {
          const approve = el('button', {
            class: 'btn btn-primary btn-small', type: 'button', text: '批准并继续'
          });
          approve.addEventListener('click', handler(() => batchAction(
            batch.id, 'resume', { decision: 'approve' }, '已批准，批次继续', approve)));
          actions.append(approve);
        }
        if (run.status === 'blocked' || run.status === 'failed') {
          const resume = el('button', {
            class: 'btn btn-small', type: 'button', text: '恢复'
          });
          resume.addEventListener('click', handler(() => batchAction(
            batch.id, 'resume', {}, '批次已恢复', resume)));
          actions.append(resume);
        }
        const cancel = el('button', {
          class: 'btn btn-danger btn-small', type: 'button', text: '取消'
        });
        cancel.addEventListener('click', handler(() => batchAction(
          batch.id, 'cancel', {}, '批次已取消', cancel)));
        actions.append(cancel);

        const childRows = children.map((child) => [
          child.chapter ? '第' + child.chapter + '章' : '—',
          runStatusBadge(child.status),
          String(child.usage || 0),
          el('span', { class: 'hint', text: child.error || '' })
        ]);

        const trace = state && state.childDetail
          ? assembleTraceNode(state.childDetail) : null;

        return el('div', { class: 'batch-card' },
          el('div', { class: 'automation-run-head' },
            runStatusBadge(run.status),
            el('strong', { text: batch.id }),
            el('span', { class: 'muted', text: '章节 ' + current + ' / ' + endN }),
            el('span', { class: 'muted', text: 'tokens ' + used
              + (budget ? ' / ' + budget : '') }),
            el('span', { class: 'muted', text: formatTime(run.created_at) })),
          run.error ? el('div', { class: 'notice notice-danger' },
            el('strong', { text: '批次错误' }), el('p', { text: run.error })) : null,
          el('div', { class: 'batch-children' },
            dataTable(['章号', '状态', 'tokens', '错误'], childRows,
              '还没有子运行')),
          trace,
          el('div', { class: 'form-actions' }, actions));
      });
      batchHost.replaceChildren(...cards);
    }

    async function refreshAll(preferId) {
      data = await api(base + '/automation');
      runs = (data.runs || []).filter((run) => run.kind !== 'batch');
      batches = Array.isArray(data.batches) ? data.batches : [];
      lintData = (data.latest && data.latest.lint) || null;
      await loadExtras();
      await loadBatchStates();
      const wanted = preferId && runs.some((run) => run.id === preferId) ? preferId : selectedId;
      selectedId = wanted && runs.some((run) => run.id === wanted)
        ? wanted : (runs.length ? runs[0].id : null);
      await loadDetail(selectedId);
      draw();
    }

    function draw() {
      drawStatus();
      drawDetail();
      drawLint();
      drawHistory();
      drawBatches();
    }

    const batchChaptersInput = el('input', {
      type: 'number', min: '1', max: '20', step: '1', value: '3'
    });
    const batchBudgetInput = el('input', {
      type: 'number', min: '500', max: '2000000', step: '1000', value: '600000'
    });
    const batchWordsInput = el('input', {
      type: 'number', min: '100', max: '100000', step: '100', value: '2500'
    });
    const batchGateInput = el('input', { type: 'checkbox', checked: true });
    const batchBtn = el('button', {
      class: 'btn btn-primary', type: 'submit', text: '开始批跑'
    });
    const batchForm = el('form', { class: 'form-grid' },
      field('章数', batchChaptersInput, '1–20，从下一章起连续跑'),
      field('批预算（tokens）', batchBudgetInput, '整批合计；用尽后暂停为「阻断」'),
      field('目标字数', batchWordsInput, '每章目标字数，默认 2500'),
      checkboxField('人工闸门', batchGateInput,
        '开启时每章停在「等待人工」，批准后再继续下一章'),
      el('div', { class: 'field field-wide form-actions' }, batchBtn));

    batchForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const chapters = Number(batchChaptersInput.value);
      if (!Number.isInteger(chapters) || chapters < 1 || chapters > 20) {
        toast('章数必须是 1–20 的整数', 'error');
        batchChaptersInput.focus();
        return;
      }
      const policy = {
        budget_tokens: Number(batchBudgetInput.value) || 600000,
        target_words: Number(batchWordsInput.value) || 2500,
        require_human_gate: Boolean(batchGateInput.checked)
      };
      await withBusy(batchBtn, async () => {
        const out = await api(base + '/automation/batch', {
          method: 'POST', body: { chapters: chapters, policy: policy }
        });
        const run = out && out.run;
        toast('批次已启动：'
          + (run ? (RUN_STATUS_LABELS[run.status] || run.status) : '已提交'), 'success');
        await refreshAll();
      });
    }));

    const budgetInput = el('input', { type: 'number', min: '500', max: '2000000', step: '1000', value: '200000' });
    const wordsInput = el('input', { type: 'number', min: '100', max: '100000', step: '100', value: '2500' });
    const gateInput = el('input', { type: 'checkbox', checked: true });
    const startBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '开始运行' });
    const runForm = el('form', { class: 'form-grid' },
      field('运行预算（tokens）', budgetInput, '500–2000000；写正文前按本次运行已花 token 核对'),
      field('目标字数', wordsInput, '100–100000，默认 2500'),
      checkboxField('人工闸门', gateInput, '开启时正文与检查完成后停在「等待人工」，需手动批准'),
      el('div', { class: 'field field-wide form-actions' }, startBtn));

    runForm.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const policy = {
        budget_tokens: Number(budgetInput.value) || 200000,
        target_words: Number(wordsInput.value) || 2500,
        require_human_gate: Boolean(gateInput.checked)
      };
      await withBusy(startBtn, async () => {
        const out = await api(base + '/runs', { method: 'POST', body: { policy: policy } });
        const run = out && out.run;
        toast('运行已启动：' + (run ? (RUN_STATUS_LABELS[run.status] || run.status) : '已提交'), 'success');
        await refreshAll(run ? run.id : null);
      });
    }));

    draw();
    return [
      pageHead('自动化', '现在该做什么、卡在哪、花了多少；单章流水线不替你做最终定稿。'),
      card('当前状态', statusHost),
      card('整本批跑', batchForm),
      card('批次', batchHost),
      card('开始运行', runForm),
      el('div', { class: 'auto-grid' },
        el('div', { class: 'auto-col' },
          card('运行列表', historyHost)),
        el('div', { class: 'auto-col' },
          card('运行详情', detailHost),
          card('L0 检查', lintHost)))
    ];
  }

  function traceView(trace) {
    if (!trace || typeof trace !== 'object') {
      return emptyState('本章尚未生成输入包',
        '跑一次自动化 assemble 后，这里会解释哪些设定被激活、哪些被丢弃。');
    }
    const activated = Array.isArray(trace.activated) ? trace.activated : [];
    const dropped = Array.isArray(trace.dropped) ? trace.dropped : [];
    const budget = trace.budget || {};
    const ratio = Math.max(0, Math.min(1, Number(budget.ratio) || 0));
    const counts = el('div', { class: 'trace-counts' },
      badge('激活 ' + activated.length, 'ok'),
      badge('丢弃 ' + dropped.length, 'muted'));
    const bar = el('div', { class: 'trace-budget' },
      el('div', { class: 'trace-budget-bar' },
        el('span', { class: 'trace-budget-fill', style: 'width:' + Math.round(ratio * 100) + '%' })),
      el('p', { class: 'hint', text: '预算 ' + (budget.tokens || 0) + ' tokens · 已用 '
        + (budget.used || 0) + ' · ' + Math.round(ratio * 100) + '%' }));
    const entries = [];
    for (const item of activated) {
      entries.push(el('div', { class: 'trace-item' },
        el('span', { class: 'trace-name', text: item.name || item.id || '未命名' }),
        el('span', { class: 'trace-reason', text: item.reason || '' })));
      if (entries.length >= 6) break;
    }
    for (const item of dropped) {
      if (entries.length >= 6) break;
      entries.push(el('div', { class: 'trace-item is-dropped' },
        el('span', { class: 'trace-name', text: item.name || item.id || '未命名' }),
        el('span', { class: 'trace-reason', text: item.reason || '未命中' })));
    }
    const warnings = (Array.isArray(trace.warnings) ? trace.warnings : [])
      .map((text) => el('p', { class: 'hint', text: text }));
    return el('div', { class: 'trace-view' },
      counts, bar,
      entries.length ? el('div', { class: 'trace-list' }, entries) : emptyState('本章没有可带入的设定'),
      ...warnings);
  }

  async function pageStudio(pid, chapterParam) {
    let first = await api('/api/projects/' + enc(pid) + '/studio/view');
    let n = chapterParam ? Number(chapterParam) : first.chapter_n;
    if (chapterParam === 'next') n = first.tree.next_chapter;
    if (!Number.isFinite(n) || n < 0) n = first.chapter_n;
    const data = (n === first.chapter_n && !chapterParam)
      ? first
      : await api('/api/projects/' + enc(pid) + '/studio/view?chapter_n=' + enc(String(n)));
    const tree = data.tree;
    const chapter = data.chapter || null;
    const ctx = data.context || {};
    const selected = data.chapter_n;

    const treeBox = el('div', { class: 'studio-tree-list' });
    treeBox.append(el('div', { class: 'studio-tree-head' },
      el('strong', { text: '章节树' }),
      el('span', { class: 'muted', text: tree.stats.chapters + ' 章 · ' + tree.stats.words + ' 字' })));
    for (const item of tree.chapters) {
      const active = item.n === selected;
      const btn = el('button', {
        class: 'studio-tree-item' + (active ? ' is-active' : ''),
        type: 'button'
      },
        el('span', { class: 'studio-tree-n', text: String(item.n) }),
        el('span', { class: 'studio-tree-title',
          text: item.title || ('第' + item.n + '章') }),
        el('span', { class: 'studio-tree-side' },
          riskDot(item.risk, item.export_blocked),
          chapterStatusBadge(item.status),
          el('span', { class: 'studio-tree-meta',
            text: item.words + ' 字' + (item.due_foreshadow ? ' · 到期伏笔 ' + item.due_foreshadow : '') })));
      btn.addEventListener('click', () => {
        location.hash = '#/p/' + enc(pid) + '/studio/' + enc(String(item.n));
      });
      treeBox.append(btn);
    }
    const newBtn = el('button', { class: 'btn btn-small', type: 'button', text: '＋ 新建一章' });
    newBtn.addEventListener('click', () => {
      location.hash = '#/p/' + enc(pid) + '/studio/next';
    });
    treeBox.append(newBtn);

    const titleInput = el('input', { type: 'text', maxlength: '300',
      value: chapter ? (chapter.title || '') : ('第' + selected + '章'),
      placeholder: '章节标题' });
    const statusSelect = selectEl(opts(STATUS_LABELS), chapter ? chapter.status : 'draft');
    const textarea = el('textarea', { class: 'studio-editor', rows: '22',
      placeholder: '在这里写正文。结构化体检、合规预检、串声检查都以这一段为准。'
    });
    textarea.value = chapter ? (chapter.content || '') : '';
    const wordCount = el('span', { class: 'muted', text: '0 字' });
    function refreshWords() {
      const count = textarea.value.replace(/\s+/g, '').length;
      wordCount.textContent = count + ' 字';
    }
    textarea.addEventListener('input', refreshWords);
    refreshWords();

    const saveBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '保存为新版本' });
    const complianceBtn = el('button', { class: 'btn', type: 'button', text: '合规预检' });
    const structureBtn = el('button', { class: 'btn', type: 'button', text: '结构体检' });
    const appealBtn = el('button', { class: 'btn', type: 'button', text: '申诉包' });
    const resultHost = el('div', { class: 'report-host' }, emptyState('体检 / 预检结果会显示在这里。'));

    async function saveChapter() {
      const body = {
        n: selected,
        title: titleInput.value.trim() || ('第' + selected + '章'),
        content: textarea.value,
        status: statusSelect.value
      };
      const out = await api('/api/projects/' + enc(pid) + '/chapters', { method: 'POST', body: body });
      toast('已保存第 ' + out.n + ' 章 v' + out.version, 'success');
      location.hash = '#/p/' + enc(pid) + '/studio/' + enc(String(out.n));
      render();
      return out;
    }

    saveBtn.addEventListener('click', handler(async () => {
      if (!textarea.value.trim()) { toast('正文为空，先写一点再保存', 'error'); textarea.focus(); return; }
      await withBusy(saveBtn, saveChapter);
    }));
    complianceBtn.addEventListener('click', handler(async () => {
      if (!textarea.value.trim()) { toast('先写正文', 'error'); return; }
      if (!chapter) {
        await saveChapter();
      }
      await withBusy(complianceBtn, async () => {
        const out = await api('/api/projects/' + enc(pid) + '/workbench/compliance', {
          method: 'POST', body: { text: textarea.value, chapter_n: selected, save: true }
        });
        resultHost.replaceChildren(reportView(out));
        toast(out.export_blocked ? '红色：导出会被阻断' : '预检完成',
          out.export_blocked ? 'error' : 'success');
      });
    }));
    structureBtn.addEventListener('click', handler(async () => {
      if (!textarea.value.trim()) { toast('先写正文', 'error'); return; }
      await withBusy(structureBtn, async () => {
        const out = await api('/api/projects/' + enc(pid) + '/structure', {
          method: 'POST', body: { text: textarea.value }
        });
        resultHost.replaceChildren(structureView(out));
        toast('结构体检完成', 'success');
      });
    }));
    appealBtn.addEventListener('click', handler(async () => {
      if (!chapter && !textarea.value.trim()) { toast('这一章还没有保存过，无法生成申诉包', 'error'); return; }
      await withBusy(appealBtn, async () => {
        if (!chapter) await saveChapter();
        const out = await api('/api/projects/' + enc(pid) + '/appeal', {
          method: 'POST', body: { chapter_n: selected }
        });
        await downloadApi('/api/projects/' + enc(pid) + '/appeal/download?file=' + enc(out.filename), out.filename);
      });
    }));

    const main = el('div', { class: 'studio-main' },
      el('div', { class: 'inline-form studio-meta' },
        field('标题', titleInput), field('状态', statusSelect),
        el('div', { class: 'studio-wordcount' }, wordCount)),
      el('div', { class: 'studio-toolbar' }, saveBtn, complianceBtn, structureBtn, appealBtn),
      textarea, resultHost);

    const charList = (ctx.characters || []).map((item) => el('div', { class: 'mini-card' },
      el('strong', { text: item.name || '' }),
      el('span', { class: 'muted', text: item.role || '人物' }),
      el('p', { class: 'hint', text: voiceSummary(item) || '未填语言指纹' })));
    const dueList = (ctx.due_foreshadow || []).map((item) => el('div', { class: 'mini-card mini-warn' },
      el('strong', { text: item.text || '未命名伏笔' }),
      el('span', { class: 'hint', text: '埋于第' + item.planted_chapter + '章 · 计划第' + item.due_chapter + '章回收' })));
    const compliance = ctx.compliance;
    const exp = ctx.expectation || {};
    function inspectorSection(title, ...children) {
      const key = 'longform-inspector:' + title;
      const box = el('details', { class: 'context-section' },
        el('summary', { class: 'context-section-head' }, el('h3', { text: title })),
        ...children);
      let open = true;
      try { open = localStorage.getItem(key) !== '0'; } catch (err) { open = true; }
      box.open = open;
      box.addEventListener('toggle', () => {
        try { localStorage.setItem(key, box.open ? '1' : '0'); } catch (err) { /* 忽略 */ }
      });
      return box;
    }
    const right = el('aside', { class: 'studio-context' },
      inspectorSection('本章流程', pipelineView(data.pipeline, pid)),
      inspectorSection('激活解释', traceView(ctx.entity_trace)),
      inspectorSection('最近合规',
        compliance ? el('div', { class: 'inline-form' },
          badge(compliance.verdict || '未判定', compliance.export_blocked ? 'danger'
            : (compliance.verdict === 'yellow' ? 'warn' : 'ok')),
          el('span', { class: 'muted', text: compliance.export_blocked ? '导出被阻断' : '未阻断' }))
          : emptyState('本章还没有合规记录')),
      inspectorSection('到期伏笔', dueList.length ? dueList : emptyState('没有到期伏笔')),
      inspectorSection('人物', charList.length ? charList : emptyState('还没有人物卡')),
      inspectorSection('预期账本',
        el('div', { class: 'expectation-inline' },
          Object.keys(EXPECTATION_LABELS).map((key) => el('span', { class: 'expectation-pill' },
            EXPECTATION_LABELS[key] + ' ' + ((exp.counts && exp.counts[key]) || 0))))));

    const focusOn = document.body.classList.contains('focus-mode');
    const focusBtn = el('button', {
      id: 'studio-focus-btn', class: 'btn', type: 'button',
      'aria-pressed': focusOn ? 'true' : 'false',
      text: focusOn ? '退出专注' : '专注模式'
    });
    focusBtn.addEventListener('click', () => {
      applyFocusMode(!document.body.classList.contains('focus-mode'));
    });

    return [
      pageHead('创作台', '左看章节树，中间写正文，右边看上下文；先体检，再预检，最后导出申诉包。', focusBtn),
      el('div', { class: 'studio-grid' },
        el('aside', { class: 'studio-tree' }, treeBox),
        main,
        right)
    ];
  }

  // ---------------------------------------------------------------- AI Studio 主控
  const STUDIO_MODES = [
    ['ai-studio', 'AI Studio 对话框（不配置 API）'],
    ['direct', '直接 API']
  ];
  const STUDIO_TASK_OPTIONS = [
    ['settings', '设置提示词'],
    ['chapter', '写正文'],
    ['review', '审校']
  ];

  function readFileText(file) {
    if (file.text) return file.text();
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(reader.error || new Error('读取文件失败'));
      reader.readAsText(file);
    });
  }

  async function pageAiStudio(pid) {
    const base = '/api/projects/' + enc(pid) + '/studio';
    const data = await api(base);
    const studio = data.studio || {};
    const pack = data.pack || {};
    const groups = Array.isArray(data.groups) ? data.groups : [];
    const groupTools = (pack.tools && pack.tools.groups) || {};

    // ---- 模式 ----
    const modeValue = ['ai-studio', 'direct'].indexOf(studio.mode) >= 0
      ? studio.mode : 'ai-studio';
    const modeSel = selectEl(STUDIO_MODES, modeValue);
    const modeHint = el('p', { class: 'hint' });
    function refreshModeHint() {
      modeHint.textContent = modeSel.value === 'direct'
        ? '直接 API：由自动化运行调用已配置的模型服务，需要先在「设置」里填好接口与模型。'
        : 'AI Studio 对话框：本机不存 LLM 密钥，由 AI Studio 主控写作；本页只负责导出设置包与提示词。';
    }
    modeSel.addEventListener('change', refreshModeHint);
    refreshModeHint();

    const modeCard = el('div', { class: 'studio-mode-card' },
      field('主导模式 mode', modeSel, '导出的提示词里会写明当前模式'),
      modeHint,
      el('div', { class: 'notice' },
        el('p', { text: '不配置模型 API 时，推荐选「AI Studio 对话框」：写作要求与工具组会写进导出的提示词，交给对话框执行。' })));

    // ---- 写作要求 ----
    const requirements = el('textarea', {
      rows: '6', maxlength: '20000', spellcheck: 'false',
      placeholder: '例如：每章 2500–3000 字；第三人称限知；章末留钩子，不要解释谜底。'
    });
    requirements.value = studio.requirements || '';

    // ---- 工具组 ----
    const selectedGroups = Array.isArray(studio.tool_groups) ? studio.tool_groups.slice() : [];
    const groupInputs = [];
    const groupGrid = el('div', { class: 'tool-group-grid' });
    for (const name of groups) {
      // 「其他」是未分组工具，取消全部勾选即隐含包含它，不能单独提交。
      const isOther = name === '其他';
      const input = el('input', {
        type: 'checkbox',
        checked: selectedGroups.indexOf(name) >= 0,
        disabled: isOther
      });
      input.value = name;
      if (!isOther) groupInputs.push(input);
      const count = Array.isArray(groupTools[name]) ? groupTools[name].length : 0;
      groupGrid.append(el('label', {
        class: 'tool-group-item' + (isOther ? ' is-fixed' : ''),
        title: isOther ? '未分组工具：取消全部勾选时自动包含' : null
      },
        input,
        el('span', { text: name }),
        el('span', { class: 'tool-group-count', text: count + ' 个工具' })));
    }
    if (!groups.length) groupGrid.append(emptyState('没有可选工具组'));

    function pickedGroups() {
      return groupInputs.filter((input) => input.checked).map((input) => input.value);
    }

    // ---- 保存 ----
    const saveBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '保存设置' });
    saveBtn.addEventListener('click', handler(async () => {
      await withBusy(saveBtn, async () => {
        await api(base, {
          method: 'PUT',
          body: {
            mode: modeSel.value,
            requirements: requirements.value,
            tool_groups: pickedGroups()
          }
        });
        toast('AI Studio 设置已保存', 'success');
        render();
      });
    }));

    // ---- 导出 ----
    const exportJson = el('button', { class: 'btn', type: 'button', text: '导出 JSON' });
    const exportPrompt = el('button', { class: 'btn', type: 'button', text: '导出提示词' });
    exportJson.addEventListener('click', handler(async () => {
      await withBusy(exportJson,
        () => downloadApi(base + '/export?format=json', pid + '.studio.json'));
    }));
    exportPrompt.addEventListener('click', handler(async () => {
      await withBusy(exportPrompt,
        () => downloadApi(base + '/export?format=prompt', pid + '.studio.prompt.md'));
    }));

    // ---- 导入 ----
    let importPackData = null;
    const fileInput = el('input', { type: 'file', accept: '.json,application/json' });
    const previewBtn = el('button', { class: 'btn', type: 'button', text: '预检导入', disabled: true });
    const applyBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '应用导入', disabled: true });
    const importHost = el('div', { class: 'import-result' },
      emptyState('先选择一个 .json 设置包，再预检变更。'));

    fileInput.addEventListener('change', handler(async () => {
      importPackData = null;
      previewBtn.disabled = true;
      applyBtn.disabled = true;
      const file = fileInput.files && fileInput.files[0];
      if (!file) return;
      let text;
      try {
        text = await readFileText(file);
      } catch (error) {
        toast('读取文件失败', 'error');
        return;
      }
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch (error) {
        toast('不是合法 JSON 文件', 'error');
        return;
      }
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
        toast('设置包顶层必须是 JSON 对象', 'error');
        return;
      }
      importPackData = parsed;
      previewBtn.disabled = false;
      importHost.replaceChildren(emptyState('已读取 ' + file.name + '，点「预检导入」查看变更。'));
    }));

    previewBtn.addEventListener('click', handler(async () => {
      if (!importPackData) { toast('请先选择设置包', 'error'); return; }
      await withBusy(previewBtn, async () => {
        const out = await api(base + '/import', {
          method: 'POST', body: { pack: importPackData, dry_run: true }
        });
        const counts = out.counts || {};
        const changes = Array.isArray(out.changes) ? out.changes : [];
        const rows = changes.map((item) => [item.scope, el('code', { text: item.key })]);
        importHost.replaceChildren(
          el('div', { class: 'notice' },
            el('p', { text: '预检通过，尚未写入。' }),
            el('p', { class: 'hint', text: 'style ' + (counts.style || 0)
              + ' · config ' + (counts.config || 0) + ' · studio ' + (counts.studio || 0) })),
          rows.length ? dataTable(['范围', '键'], rows, '') : emptyState('没有需要写入的变更'));
        applyBtn.disabled = !rows.length;
        toast('预检完成：' + changes.length + ' 项待写入', 'success');
      });
    }));

    applyBtn.addEventListener('click', handler(async () => {
      if (!importPackData) { toast('请先选择设置包', 'error'); return; }
      await withBusy(applyBtn, async () => {
        const out = await api(base + '/import', {
          method: 'POST', body: { pack: importPackData, dry_run: false }
        });
        const counts = out.counts || {};
        toast('已导入 ' + ((counts.style || 0) + (counts.config || 0)
          + (counts.studio || 0)) + ' 项设置', 'success');
        render();
      });
    }));

    // ---- 提示词生成 ----
    const taskSel = selectEl(STUDIO_TASK_OPTIONS, 'settings');
    const chapterInput = el('input', { type: 'number', min: '1', step: '1', value: '1' });
    const chapterField = field('章号 chapter_n', chapterInput, '仅「写正文」需要填写');
    function refreshTask() {
      const isChapter = taskSel.value === 'chapter';
      chapterField.hidden = !isChapter;
    }
    taskSel.addEventListener('change', refreshTask);
    refreshTask();

    const promptArea = el('textarea', {
      class: 'prompt-preview', rows: '14', readonly: true, spellcheck: 'false'
    });
    promptArea.value = data.prompt || '';
    const generateBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '生成提示词' });
    const copyBtn = el('button', { class: 'btn', type: 'button', text: '复制' });

    generateBtn.addEventListener('click', handler(async () => {
      const body = { task: taskSel.value };
      if (taskSel.value === 'chapter') {
        const n = Number(chapterInput.value);
        if (!Number.isFinite(n) || n < 1) {
          toast('请填写有效的章号', 'error');
          chapterInput.focus();
          return;
        }
        body.chapter_n = n;
      }
      await withBusy(generateBtn, async () => {
        const out = await api(base + '/prompt', { method: 'POST', body: body });
        promptArea.value = out.prompt || '';
        toast('提示词已生成', 'success');
      });
    }));

    copyBtn.addEventListener('click', handler(async () => {
      const text = promptArea.value || '';
      if (!text) { toast('没有可复制的内容', 'error'); return; }
      await withBusy(copyBtn, async () => {
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
          } else {
            promptArea.removeAttribute('readonly');
            promptArea.select();
            document.execCommand('copy');
            promptArea.setAttribute('readonly', '');
          }
          toast('已复制提示词', 'success');
        } catch (error) {
          toast('复制失败，请手动选择文本', 'error');
        }
      });
    }));

    // ---- 自驾：下一步 / 交接单 / 设计向导 ----
    const autoBase = '/api/projects/' + enc(pid) + '/autopilot';
    const handoffBase = '/api/projects/' + enc(pid) + '/handoffs';
    const handoffAction = (id, action) => '/api/handoffs/' + enc(id) + '/' + action;

    const nextHost = el('div', { class: 'auto-next' },
      el('p', { class: 'loading', text: '正在读取下一步…' }));
    const listHost = el('div', { class: 'auto-handoffs' },
      el('p', { class: 'loading', text: '正在读取交接单…' }));
    const designHost = el('div', { class: 'design-result' },
      emptyState('填写基本信息后生成设计交接单。'));

    function actionBadge(action) {
      const tones = { tool: 'ok', handoff: 'warn', human: 'danger', done: 'muted' };
      return badge(action || '未知', tones[action] || 'muted');
    }

    function statusTone(status) {
      if (status === 'applied') return 'ok';
      if (status === 'pending' || status === 'submitted') return 'warn';
      if (status === 'failed') return 'danger';
      return 'muted';
    }

    function readonlyArea(text, rows) {
      const area = el('textarea', {
        class: 'prompt-preview', rows: String(rows || 10), readonly: true, spellcheck: 'false'
      });
      area.value = text || '';
      return area;
    }

    function copyRow(text, hint) {
      const btn = el('button', { class: 'btn btn-small', type: 'button', text: '复制' });
      btn.addEventListener('click', handler(async () => {
        await withBusy(btn, () => copyText(typeof text === 'function' ? text() : text));
      }));
      return el('div', { class: 'prompt-actions' },
        hint ? el('span', { class: 'hint', text: hint }) : null, btn);
    }

    function renderNext(out) {
      const action = out && out.action ? out.action : 'human';
      const parts = [
        el('div', { class: 'auto-next-head' },
          actionBadge(action),
          el('p', { class: 'auto-why', text: (out && out.why) || '暂无说明' }))
      ];
      if (action === 'tool') {
        parts.push(el('p', { class: 'hint', text: '执行工具：' + (out.tool || '—') }));
        parts.push(readonlyArea(jsonText(out.args || {}), 8));
        parts.push(copyRow(() => jsonText(out.args || {}), 'args 为只读 JSON，可复制后交给 AI Studio 执行。'));
      } else if (action === 'handoff' && out.handoff) {
        const h = out.handoff;
        parts.push(el('p', { class: 'hint', text: '交接单 ' + (h.handoff_id || h.id || '')
          + ' · ' + (h.kind || '') + ' · ' + (h.status || '') }));
        parts.push(readonlyArea(h.prompt || '', 14));
        parts.push(copyRow(h.prompt || '',
          '把 prompt 粘贴到 AI Studio，结果提交到下方交接单。'));
      } else if (action === 'done') {
        const summary = out.summary || {};
        const pairs = Object.keys(summary).map((key) => [key, String(summary[key])]);
        parts.push(el('div', { class: 'notice' },
          el('p', { text: '已完成：' + (out.why || '') }),
          pairs.length ? kvList(pairs) : null));
      } else if (action === 'human') {
        parts.push(el('p', { class: 'hint', text: '人工闸门：' + (out.gate || '—')
          + (out.run_id ? ' · 运行 ' + out.run_id : '') }));
        if (Array.isArray(out.findings) && out.findings.length) {
          parts.push(el('p', { class: 'warn', text: '有 ' + out.findings.length + ' 条阻断项需要修复' }));
        }
      }
      nextHost.replaceChildren(...parts);
    }

    function renderHandoffs(out) {
      const rows = out && Array.isArray(out.handoffs) ? out.handoffs : [];
      if (!rows.length) {
        listHost.replaceChildren(emptyState('还没有交接单；用下方设计向导生成第一张。'));
        return;
      }
      listHost.replaceChildren(...rows.map(handoffRow));
    }

    function handoffRow(h) {
      const status = h.status || 'pending';
      const isOpen = status === 'pending' || status === 'submitted';
      const meta = (h.kind || '')
        + (h.chapter_n !== null && h.chapter_n !== undefined ? ' · 第 ' + h.chapter_n + ' 章' : '')
        + ' · ' + formatTime(h.created_at);
      const summary = el('summary', { class: 'handoff-head' },
        el('span', { class: 'handoff-id', text: h.id || '' }),
        badge(status, statusTone(status)),
        el('span', { class: 'handoff-meta', text: meta }));
      const body = el('div', { class: 'handoff-body' });

      if (isOpen) {
        const resultArea = el('textarea', {
          rows: '8', spellcheck: 'false',
          placeholder: '把 AI Studio 返回的 JSON/文本粘贴到这里'
        });
        const errorLine = el('p', { class: 'warn', hidden: true });
        const fileInput = el('input', { type: 'file', accept: '.json,.md,.txt' });
        fileInput.addEventListener('change', handler(async () => {
          const file = fileInput.files && fileInput.files[0];
          if (!file) return;
          try {
            resultArea.value = (await readFileText(file)).trim();
          } catch (error) {
            toast('读取文件失败', 'error');
          }
        }));

        const submitBtn = el('button', { class: 'btn btn-primary btn-small', type: 'button', text: '提交' });
        const cancelBtn = el('button', { class: 'btn btn-small', type: 'button', text: '取消' });

        submitBtn.addEventListener('click', handler(async () => {
          const text = resultArea.value.trim();
          if (!text) {
            toast('请先粘贴或选择交接结果', 'error');
            resultArea.focus();
            return;
          }
          errorLine.hidden = true;
          await withBusy(submitBtn, async () => {
            let payload = text;
            try { payload = JSON.parse(text); } catch (error) { /* 非 JSON 按纯文本提交 */ }
            try {
              const res = await api(handoffAction(h.id, 'submit'), {
                method: 'POST', body: { result: payload }, quiet: true
              });
              toast(res.already_applied ? '该交接单已应用过，无需重复提交' : '交接结果已应用',
                'success');
              await refreshAll();
            } catch (error) {
              errorLine.textContent = '提交失败：' + (error && error.message ? error.message : String(error));
              errorLine.hidden = false;
            }
          });
        }));

        cancelBtn.addEventListener('click', handler(async () => {
          await withBusy(cancelBtn, async () => {
            try {
              await api(handoffAction(h.id, 'cancel'), { method: 'POST', quiet: true });
              toast('交接单已取消', 'success');
              await refreshAll();
            } catch (error) {
              errorLine.textContent = '取消失败：' + (error && error.message ? error.message : String(error));
              errorLine.hidden = false;
            }
          });
        }));

        body.append(
          el('p', { class: 'hint', text: '可直接粘贴 JSON，也可选择 .json / .md / .txt 文件读入输入框。' }),
          resultArea,
          el('div', { class: 'studio-import-row' }, fileInput, submitBtn, cancelBtn),
          errorLine);
      } else if (h.error) {
        body.append(el('p', { class: 'warn', text: '错误：' + h.error }));
      } else if (h.result !== null && h.result !== undefined) {
        body.append(readonlyArea(jsonText(h.result), 6));
      } else {
        body.append(emptyState('没有可展开的内容'));
      }

      return el('details', { class: 'handoff', open: isOpen }, summary, body);
    }

    async function loadNext() {
      try {
        renderNext(await api(autoBase, { quiet: true }));
      } catch (error) {
        nextHost.replaceChildren(el('p', { class: 'warn',
          text: '读取下一步失败：' + (error && error.message ? error.message : String(error)) }));
      }
    }

    async function loadHandoffs() {
      try {
        renderHandoffs(await api(handoffBase, { quiet: true }));
      } catch (error) {
        listHost.replaceChildren(el('p', { class: 'warn',
          text: '读取交接单失败：' + (error && error.message ? error.message : String(error)) }));
      }
    }

    async function refreshAll() {
      await Promise.all([loadNext(), loadHandoffs()]);
    }

    const refreshNextBtn = el('button', { class: 'btn', type: 'button', text: '刷新下一步' });
    refreshNextBtn.addEventListener('click', handler(async () => {
      await withBusy(refreshNextBtn, refreshAll);
    }));

    // ---- 设计向导 ----
    const premiseInput = el('textarea', {
      rows: '4', maxlength: '20000', spellcheck: 'false',
      placeholder: '一句话核心设定，例如：一封匿名信揭开十年前的港口旧案。'
    });
    const genreInput = el('input', { type: 'text', placeholder: '例如：悬疑' });
    const chaptersInput = el('input', {
      type: 'number', min: '0', max: '5000', step: '1', value: '0'
    });
    const wordsInput = el('input', {
      type: 'number', min: '0', max: '100000', step: '1000', value: '0'
    });
    const designBtn = el('button', { class: 'btn btn-primary', type: 'button', text: '生成设计交接单' });

    designBtn.addEventListener('click', handler(async () => {
      const chapters = Number(chaptersInput.value || 0);
      const targetWords = Number(wordsInput.value || 0);
      if (!Number.isFinite(chapters) || chapters < 0
        || !Number.isFinite(targetWords) || targetWords < 0) {
        toast('章数与目标字数必须是非负整数', 'error');
        return;
      }
      await withBusy(designBtn, async () => {
        try {
          const out = await api('/api/projects/' + enc(pid) + '/design', {
            method: 'POST', quiet: true,
            body: {
              premise: premiseInput.value,
              genre: genreInput.value,
              chapters: Math.floor(chapters),
              target_words: Math.floor(targetWords)
            }
          });
          designHost.replaceChildren(
            el('div', { class: 'notice' },
              el('p', { text: '已生成设计交接单：' + (out.handoff_id || '') }),
              el('p', { class: 'hint',
                text: '把下面 prompt 粘贴到 AI Studio，再把返回 JSON 提交到上方交接单。' })),
            readonlyArea(out.prompt || '', 14),
            copyRow(out.prompt || ''));
          toast('设计交接单已生成', 'success');
          await refreshAll();
        } catch (error) {
          designHost.replaceChildren(el('p', { class: 'warn',
            text: '生成失败：' + (error && error.message ? error.message : String(error)) }));
        }
      });
    }));

    refreshAll();

    return [
      pageHead('AI Studio', '不配置模型 API，也能让 AI Studio 对话框按你的文风与工具范围写作；设置与提示词都可以导入导出。'),
      el('div', { class: 'studio-stack' },
        el('section', { class: 'studio-section' },
          el('h2', { class: 'studio-section-title', text: '自驾' }),
          card('下一步',
            el('p', { class: 'hint',
              text: '按当前项目状态给出唯一的下一个动作；交接类动作把 prompt 交给 AI Studio。' }),
            el('div', { class: 'form-actions' }, refreshNextBtn),
            nextHost),
          card('交接单',
            el('p', { class: 'hint',
              text: 'pending 交接单可直接粘贴结果提交；提交成功会推进项目状态并刷新下一步。' }),
            listHost),
          card('设计向导',
            el('p', { class: 'hint',
              text: '填写核心设定后生成 project_design 交接单；prompt 交给 AI Studio，结果提交到上方交接单。' }),
            el('div', { class: 'design-form' },
              field('核心设定 premise', premiseInput, '可留空，交给 AI Studio 先向作者确认。', 'field-wide'),
              el('div', { class: 'design-form-row' },
                field('题材 genre', genreInput),
                field('计划章数 chapters', chaptersInput),
                field('目标字数 target_words', wordsInput))),
            el('div', { class: 'form-actions' }, designBtn),
            designHost)),
        el('section', { class: 'studio-section' },
          el('h2', { class: 'studio-section-title', text: '设置包' }),
          el('div', { class: 'studio-pack-grid' },
            card('主导模式', modeCard),
            card('写作要求',
              field('写作要求 requirements', requirements,
                '会被写入导出的提示词，请写成可执行的约束。', 'field-wide')),
            card('启用工具组',
              el('p', { class: 'hint', text: '不勾选=启用全部工具组；勾选后只把这些组的工具写进提示词。' }),
              groupGrid),
            card('保存',
              el('p', { class: 'muted', text: '保存后会写入项目 config.studio，并即时反映到导出的提示词。' }),
              el('div', { class: 'form-actions' }, saveBtn)),
            card('导出',
              el('p', { class: 'hint', text: 'JSON 是设置包，可回传到 AI Studio 或再导入；提示词可直接粘贴进对话框。' }),
              el('div', { class: 'form-actions' }, exportJson, exportPrompt)),
            card('导入设置包',
              el('p', { class: 'hint', text: '选择 AI Studio 返回的 .studio.json；先预检确认变更，再应用写入。' }),
              el('div', { class: 'studio-import-row' }, fileInput, previewBtn, applyBtn),
              importHost))),
        el('section', { class: 'studio-section' },
          el('h2', { class: 'studio-section-title', text: '提示词' }),
          card('生成提示词',
            el('div', { class: 'inline-form' },
              field('任务 task', taskSel),
              chapterField,
              el('div', { class: 'field' }, el('label', { text: '操作' }), generateBtn)),
            el('div', { class: 'prompt-actions' }, copyBtn),
            promptArea)))
    ];
  }

  function svgNode(tag, attrs, text) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const key of Object.keys(attrs || {})) node.setAttribute(key, attrs[key]);
    if (text !== undefined) node.textContent = displayText(text, '');
    return node;
  }

  function relationSvg(data, focus) {
    const allNodes = data.nodes || [];
    const nodes = focus ? allNodes.filter((n) => n.name !== focus) : allNodes;
    const edges = data.edges || [];
    const svg = svgNode('svg', { viewBox: '0 0 760 420', class: 'graph-svg' });
    svg.append(svgNode('rect', { x: 0, y: 0, width: 760, height: 420, class: 'graph-bg' }));
    if (!allNodes.length) {
      svg.append(svgNode('text', { x: 380, y: 210, class: 'graph-empty', 'text-anchor': 'middle' },
        '还没有人物卡；先建人物并填 data.relations'));
      return svg;
    }
    const cx = 380, cy = 210, radius = 150;
    const positions = {};
    const ring = focus ? nodes : allNodes;
    if (focus) positions[focus] = [cx, cy];
    ring.forEach((node, index) => {
      const angle = (Math.PI * 2 * index / Math.max(1, ring.length)) - Math.PI / 2;
      positions[node.name] = [cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius];
    });
    const seen = new Set();
    for (const edge of edges) {
      const a = positions[edge.source], b = positions[edge.target];
      if (!a || !b) continue;
      const key = [edge.source, edge.target].sort().join('|') + ':' + edge.type;
      if (seen.has(key)) continue;
      seen.add(key);
      svg.append(svgNode('line', { x1: a[0], y1: a[1], x2: b[0], y2: b[1], class: 'graph-edge' }));
      svg.append(svgNode('text', {
        x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2 - 4, class: 'graph-edge-label',
        'text-anchor': 'middle'
      }, edge.type || '关联'));
    }
    const drawNodes = focus
      ? [{ name: focus, role: (allNodes.find((n) => n.name === focus) || {}).role || '', missing: false }].concat(nodes)
      : allNodes;
    for (const node of drawNodes) {
      const pos = positions[node.name];
      if (!pos) continue;
      const isFocus = Boolean(focus) && node.name === focus;
      svg.append(svgNode('circle', {
        cx: pos[0], cy: pos[1], r: isFocus ? 26 : 18,
        class: 'graph-node' + (isFocus ? ' is-focus' : '') + (node.missing ? ' is-missing' : '')
      }));
      svg.append(svgNode('text', {
        x: pos[0], y: pos[1] + (isFocus ? 5 : 4), class: 'graph-label', 'text-anchor': 'middle'
      }, node.name.length > 8 ? node.name.slice(0, 8) + '…' : node.name));
      if (!isFocus && node.role) {
        svg.append(svgNode('text', { x: pos[0], y: pos[1] + 32, class: 'graph-sub', 'text-anchor': 'middle' },
          node.role.length > 10 ? node.role.slice(0, 10) + '…' : node.role));
      }
    }
    return svg;
  }

  function foreshadowSvg(data) {
    const rows = (data.rows || []).slice(0, 18);
    const width = 820, left = 230, right = 60, top = 34, rowH = 26;
    const height = top + Math.max(1, rows.length) * rowH + 24;
    const maxCh = Math.max(1, data.max_chapter || 1);
    const x = (n) => left + (Math.max(0, n) / maxCh) * (width - left - right);
    const svg = svgNode('svg', { viewBox: '0 0 ' + width + ' ' + height, class: 'graph-svg' });
    svg.append(svgNode('rect', { x: 0, y: 0, width: width, height: height, class: 'graph-bg' }));
    for (let n = 0; n <= maxCh; n += Math.max(1, Math.ceil(maxCh / 10))) {
      svg.append(svgNode('line', { x1: x(n), y1: top - 12, x2: x(n), y2: height - 18, class: 'graph-grid' }));
      svg.append(svgNode('text', { x: x(n), y: top - 16, class: 'graph-axis', 'text-anchor': 'middle' }, '第' + n + '章'));
    }
    rows.forEach((row, index) => {
      const y = top + index * rowH + 12;
      svg.append(svgNode('text', { x: 12, y: y + 4, class: 'graph-row-label' },
        (row.text || '').slice(0, 16) + ((row.text || '').length > 16 ? '…' : '')));
      const tone = row.overdue ? 'is-overdue' : (row.status === 'resolved' ? 'is-resolved'
        : (row.status === 'dropped' ? 'is-dropped' : 'is-open'));
      svg.append(svgNode('line', { x1: x(row.planted), y1: y, x2: x(row.end), y2: y, class: 'foreshadow-bar ' + tone }));
      svg.append(svgNode('circle', { cx: x(row.planted), cy: y, r: 4, class: 'foreshadow-plant' }));
      if (row.due !== null) {
        svg.append(svgNode('rect', { x: x(row.due) - 4, y: y - 4, width: 8, height: 8,
          transform: 'rotate(45 ' + x(row.due) + ' ' + y + ')', class: 'foreshadow-due ' + tone }));
      }
      if (row.resolved !== null) {
        svg.append(svgNode('circle', { cx: x(row.resolved), cy: y, r: 5, class: 'foreshadow-resolved' }));
      }
    });
    return svg;
  }

  function timelineSvg(data) {
    const events = (data.events || []).slice(0, 24);
    const width = 820, height = Math.max(320, 150 + events.length * 10);
    const svg = svgNode('svg', { viewBox: '0 0 ' + width + ' ' + height, class: 'graph-svg' });
    svg.append(svgNode('rect', { x: 0, y: 0, width: width, height: height, class: 'graph-bg' }));
    if (!events.length) {
      svg.append(svgNode('text', { x: 410, y: 160, class: 'graph-empty', 'text-anchor': 'middle' },
        '还没有事件；在时间线里补 story_time 与 narrative_time'));
      return svg;
    }
    const narrative = data.narrative_order || [];
    const story = data.story_order || [];
    const xFor = (list, id) => {
      const index = list.findIndex((e) => e.id === id);
      return 80 + (Math.max(0, index) / Math.max(1, list.length - 1)) * (width - 160);
    };
    svg.append(svgNode('text', { x: 20, y: 48, class: 'graph-lane' }, '叙事顺序'));
    svg.append(svgNode('text', { x: 20, y: 238, class: 'graph-lane' }, '故事时间'));
    svg.append(svgNode('line', { x1: 20, y1: 80, x2: width - 20, y2: 80, class: 'graph-lane-line' }));
    svg.append(svgNode('line', { x1: 20, y1: 270, x2: width - 20, y2: 270, class: 'graph-lane-line' }));
    for (const event of events) {
      const x1 = xFor(narrative, event.id), x2 = xFor(story, event.id);
      svg.append(svgNode('line', { x1: x1, y1: 80, x2: x2, y2: 270, class: 'graph-connect' }));
      svg.append(svgNode('circle', { cx: x1, cy: 80, r: 5, class: 'timeline-dot' }));
      svg.append(svgNode('circle', { cx: x2, cy: 270, r: 5, class: 'timeline-dot timeline-story' }));
      svg.append(svgNode('text', { x: x1, y: 30, class: 'graph-axis', 'text-anchor': 'middle' },
        '第' + event.chapter + '章'));
      svg.append(svgNode('text', { x: x2, y: 300, class: 'graph-axis', 'text-anchor': 'middle' },
        (event.story_time || '无故事时间').slice(0, 12)));
      if (event.title) {
        svg.append(svgNode('text', { x: x1, y: 100, class: 'graph-event-label', 'text-anchor': 'middle' },
          event.title.slice(0, 10)));
      }
    }
    return svg;
  }

  async function pageGraph(pid) {
    const graph = await api('/api/projects/' + enc(pid) + '/graph');
    const tabs = [['relations', '人物关系'], ['foreshadow', '伏笔链路'],
      ['timeline', '双轨时间线'], ['materials', '素材网络']];
    let active = 'relations';
    let focus = (graph.relations && graph.relations.focus) || '';
    const tabBar = el('div', { class: 'tab-bar' });
    const host = el('div', { class: 'tab-panel' });

    function relationsPanel() {
      const names = (graph.relations && graph.relations.characters)
        || ((graph.relations && graph.relations.nodes) || []).map((n) => n.name);
      const select = selectEl([['', '全部人物']].concat(names.map((name) => [name, name])), focus);
      if (!names.length) return el('div', {}, emptyState('还没有人物卡；先去建人物并填 data.relations'),
        el('p', { class: 'hint', text: graph.relations ? graph.relations.hint : '' }));
      const svgHost = el('div', { class: 'graph-svg-host' });
      function redraw() {
        focus = select.value;
        const data = graph.relations || { nodes: [], edges: [] };
        svgHost.replaceChildren(relationSvg(data, focus));
      }
      select.addEventListener('change', redraw);
      redraw();
      return el('div', {},
        el('div', { class: 'inline-form' }, field('聚焦人物（一跳邻域）', select)),
        svgHost,
        graph.relations && graph.relations.missing_targets && graph.relations.missing_targets.length
          ? warningsBox(['关系里出现但人物卡未登记：' + graph.relations.missing_targets.join('、')], '关系缺口')
          : null,
        el('p', { class: 'hint', text: graph.relations ? graph.relations.hint : '' }));
    }

    function foreshadowPanel() {
      const data = graph.foreshadow || { rows: [], max_chapter: 1 };
      return el('div', {},
        el('div', { class: 'inline-form' },
          badge('未回收 ' + (data.open || 0), 'warn'),
          badge('逾期 ' + (data.overdue || 0), data.overdue ? 'danger' : null),
          el('span', { class: 'muted', text: '最多显示 18 条' })),
        el('div', { class: 'graph-svg-host' }, foreshadowSvg(data)),
        el('p', { class: 'hint', text: data.hint || '' }),
        (data.rows || []).length ? dataTable(['伏笔', '埋设', '计划回收', '状态'],
          data.rows.map((row) => [row.text, '第' + row.planted + '章',
            row.due === null ? '未设' : '第' + row.due + '章',
            row.overdue ? '逾期' : (row.status === 'resolved' ? '已回收' : '未回收')]), '') : emptyState('还没有伏笔'));
    }

    function timelinePanel() {
      const data = graph.timeline || { events: [] };
      return el('div', {},
        el('div', { class: 'graph-svg-host' }, timelineSvg(data)),
        el('p', { class: 'hint', text: data.hint || '' }),
        (data.events || []).length ? dataTable(['事件', '叙事章', '故事时间', '地点'],
          data.events.map((e) => [e.title, '第' + e.chapter + '章', e.story_time || '—', e.place || '—']), '')
          : emptyState('还没有时间线事件'));
    }

    function materialsPanel() {
      const data = graph.materials || { nodes: [], duplicates: [], unresolved: [], stats: {} };
      const nodeCards = (data.nodes || []).slice(0, 40).map((node) => el('div', { class: 'material' },
        el('div', { class: 'material-head' },
          el('h3', { class: 'material-title', text: node.title || node.id }),
          badge(node.source_kind || '素材')),
        el('div', { class: 'material-meta' },
          el('span', { text: '出链 ' + (node.outgoing || []).length + ' · 反链 ' + (node.backlinks || []).length }),
          el('code', { text: node.id })),
        (node.outgoing || []).length ? el('p', { class: 'hint',
          text: '指向：' + node.outgoing.map((x) => x.title + (x.resolved ? '' : '（未找到）')).join('、') }) : null,
        (node.backlinks || []).length ? el('p', { class: 'hint',
          text: '被引用：' + node.backlinks.map((x) => x.title).join('、') }) : null));
      return el('div', {},
        el('div', { class: 'inline-form' },
          badge('素材 ' + (data.stats.materials || 0)),
          badge('有链接 ' + (data.stats.linked || 0), 'ok'),
          badge('重复组 ' + (data.stats.duplicate_groups || 0), data.stats.duplicate_groups ? 'warn' : null),
          badge('未解析 ' + (data.stats.unresolved || 0), data.stats.unresolved ? 'danger' : null)),
        (data.duplicates || []).length ? warningsBox(data.duplicates.map((d) =>
          '重复：' + d.titles.join(' / ') + '（' + d.count + ' 条）'), '去重建议') : null,
        (data.unresolved || []).length ? warningsBox(data.unresolved.map((u) =>
          u.from_title + ' → [[' + u.target + ']] 没有对应素材'), '未解析双链') : null,
        el('p', { class: 'hint', text: data.hint || '' }),
        nodeCards.length ? el('div', { class: 'stack' }, nodeCards) : emptyState('还没有素材'));
    }

    const builders = { relations: relationsPanel, foreshadow: foreshadowPanel,
      timeline: timelinePanel, materials: materialsPanel };
    function draw() {
      tabBar.replaceChildren(...tabs.map(([key, label]) => {
        const btn = el('button', { class: 'tab' + (active === key ? ' is-active' : ''), type: 'button', text: label });
        btn.addEventListener('click', () => { active = key; draw(); });
        return btn;
      }));
      host.replaceChildren(builders[active]());
    }
    draw();
    return [
      pageHead('故事图谱', '人物一跳关系、伏笔链路、双轨时间线、素材双向链接；全部由本地数据生成。'),
      tabBar, host
    ];
  }

  // ---------------------------------------------------------------- 设置
  async function pageSettings() {
    const view = await api('/api/settings');
    const llm = (view && view.llm) || {};
    const ima = (view && view.ima) || {};

    const llmEnabled = el('input', { type: 'checkbox', checked: !!llm.enabled });
    const llmBase = el('input', { type: 'text', value: llm.base_url || '', placeholder: '接口根地址，例如服务商的 chat 接口域名' });
    const llmModel = el('input', { type: 'text', value: llm.model || '', placeholder: '模型名' });
    const llmKey = el('input', { type: 'text', value: llm.api_key || '', autocomplete: 'off', spellcheck: 'false', placeholder: llm.has_api_key ? '（已设置，留空表示不修改）' : '（未设置）' });
    const llmKeyEnv = el('input', { type: 'text', value: llm.api_key_env || 'LONGFORM_LLM_API_KEY' });
    const llmTimeout = el('input', { type: 'number', min: '1', max: '600', step: '1', value: String(llm.timeout === undefined ? 60 : llm.timeout) });

    const imaEnabled = el('input', { type: 'checkbox', checked: !!ima.enabled });
    const imaBase = el('input', { type: 'text', value: ima.base_url || '', placeholder: '接口根地址' });
    const imaKey = el('input', { type: 'text', value: ima.api_key || '', autocomplete: 'off', spellcheck: 'false', placeholder: ima.has_api_key ? '（已设置，留空表示不修改）' : '（未设置）' });
    const imaKeyEnv = el('input', { type: 'text', value: ima.api_key_env || 'LONGFORM_IMA_API_KEY' });
    const imaTimeout = el('input', { type: 'number', min: '1', max: '600', step: '1', value: String(ima.timeout === undefined ? 15 : ima.timeout) });
    const imaKb = el('input', { type: 'text', value: ima.knowledge_base || '', placeholder: '知识库标识（用于来源 ima:// 路径）' });

    const llmFieldset = el('fieldset', { class: 'settings-fieldset' },
      el('legend', { text: '模型服务（OpenAI 兼容）' }),
      el('div', { class: 'form-grid' },
        checkboxField('启用模型服务', llmEnabled, '未启用时「润色/生成」会给出可读错误'),
        field('接口地址 base_url', llmBase),
        field('模型名 model', llmModel),
        field('密钥 api_key（掩码显示）', llmKey, '显示 *** 表示已有密钥；留空表示保持不变'),
        field('密钥环境变量 api_key_env', llmKeyEnv, '推荐通过环境变量提供密钥'),
        field('超时 timeout（秒）', llmTimeout)));

    const imaFieldset = el('fieldset', { class: 'settings-fieldset' },
      el('legend', { text: 'IMA 知识库' }),
      el('div', { class: 'form-grid' },
        checkboxField('启用 IMA', imaEnabled, '未启用时检索会降级提示，不影响本地知识库'),
        field('接口地址 base_url', imaBase),
        field('密钥 api_key（掩码显示）', imaKey),
        field('密钥环境变量 api_key_env', imaKeyEnv),
        field('超时 timeout（秒）', imaTimeout),
        field('知识库 knowledge_base', imaKb)));

    const saveBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '保存设置' });
    const form = el('form', {}, llmFieldset, imaFieldset,
      el('div', { class: 'form-actions' }, saveBtn));

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const llmPayload = {
        enabled: llmEnabled.checked,
        base_url: llmBase.value.trim(),
        model: llmModel.value.trim(),
        api_key_env: llmKeyEnv.value.trim() || 'LONGFORM_LLM_API_KEY',
        timeout: Number(llmTimeout.value) || 60
      };
      const imaPayload = {
        enabled: imaEnabled.checked,
        base_url: imaBase.value.trim(),
        api_key_env: imaKeyEnv.value.trim() || 'LONGFORM_IMA_API_KEY',
        timeout: Number(imaTimeout.value) || 15,
        knowledge_base: imaKb.value.trim()
      };
      const typedLlmKey = llmKey.value.trim();
      const typedImaKey = imaKey.value.trim();
      if (typedLlmKey) llmPayload.api_key = typedLlmKey;
      if (typedImaKey) imaPayload.api_key = typedImaKey;
      if (typedLlmKey || typedImaKey) {
        const ok = await confirmDialog({
          title: '以明文保存密钥？',
          message: '保存后密钥将以明文写入本地 config/settings.json。建议清空密钥输入，改用环境变量 api_key_env 提供。',
          confirmText: '仍然写入'
        });
        if (!ok) return;
      }
      await withBusy(saveBtn, async () => {
        const result = await api('/api/settings', { method: 'PUT', body: { llm: llmPayload, ima: imaPayload } });
        toast('设置已保存', 'success');
        if (result && Array.isArray(result.warnings) && result.warnings.length) {
          toast('配置提醒：' + result.warnings[0], 'error');
        }
        render();
      });
    }));

    const warnings = warningsBox(view && view.warnings);
    return [
      pageHead('设置', '模型服务与 IMA 连接；密钥读取一律脱敏。'),
      card('密钥提示',
        el('div', { class: 'notice' },
          el('p', { text: '密钥默认只从环境变量读取。若在本页填写密钥并保存，将以明文写入本地 config/settings.json，建议改用环境变量。' })),
        warnings,
        listOfCapabilities(llm, ima)),
      form
    ];
  }

  function listOfCapabilities(llm, ima) {
    return el('p', { class: 'hint', text: '模型服务：'
      + (llm.configured ? '已配置' : '未配置')
      + ' · IMA：' + (ima.configured ? '已配置' : '未配置') });
  }

  // ---------------------------------------------------------------- 项目配置
  async function pageConfig(meta, pid) {
    const config = await api('/api/projects/' + enc(pid) + '/config');
    const obj = config && typeof config === 'object' ? config : {};
    const flat = flattenConfig(obj);
    const keyInput = el('input', { type: 'text', maxlength: 200, list: 'config-keys', placeholder: '如：packet.token_budget', required: true });
    const typeSel = selectEl([['text', '文本'], ['json', 'JSON']], 'json');
    const valueArea = el('textarea', { rows: '4', spellcheck: 'false' });
    const saveBtn = el('button', { class: 'btn btn-primary', type: 'submit', text: '保存配置项' });
    const datalist = el('datalist', { id: 'config-keys' }, flat.map((pair) => el('option', { value: pair[0] })));

    const form = el('form', { class: 'form-grid' },
      field('键 key', keyInput, '支持点号嵌套，如 packet.include_facts'),
      field('值类型', typeSel),
      field('值', valueArea, 'JSON 类型需为合法 JSON', 'field-wide'),
      el('div', { class: 'field field-wide form-actions' }, saveBtn),
      datalist);

    form.addEventListener('submit', handler(async (event) => {
      event.preventDefault();
      const key = keyInput.value.trim();
      if (!key) {
        toast('请填写配置键', 'error');
        keyInput.focus();
        return;
      }
      let value;
      if (typeSel.value === 'json') {
        try {
          value = JSON.parse(valueArea.value);
        } catch (error) {
          toast('JSON 解析失败，请检查格式', 'error');
          valueArea.focus();
          return;
        }
      } else {
        value = valueArea.value;
      }
      await withBusy(saveBtn, async () => {
        await api('/api/projects/' + enc(pid) + '/config', { method: 'PUT', body: { key: key, value: value } });
        toast('已保存配置项 ' + key, 'success');
        render();
      });
    }));

    const rows = flat.map((pair) => [
      el('code', { text: pair[0] }),
      el('span', { text: jsonText(pair[1]) })
    ]);

    return [
      pageHead('项目配置', '项目级键值与运行统计。'),
      card('统计', statGrid(meta.stats)),
      card('当前配置',
        Object.keys(obj).length
          ? el('pre', { class: 'code-block', text: jsonText(obj) })
          : emptyState('项目暂无自定义配置'),
        rows.length ? dataTable(['键', '值'], rows, '') : null),
      card('写入配置项', form)
    ];
  }

  // ---------------------------------------------------------------- 启动
  function readToken() {
    const params = new URLSearchParams(location.search);
    const fromUrl = params.get('token');
    if (fromUrl) {
      window.sessionStorage.setItem('longform.web.token', fromUrl);
      params.delete('token');
      const search = params.toString();
      history.replaceState(null, '', location.pathname + (search ? '?' + search : '') + location.hash);
      return fromUrl;
    }
    return window.sessionStorage.getItem('longform.web.token') || '';
  }

  function init() {
    session.token = readToken();
    if (!location.hash) {
      history.replaceState(null, '', location.pathname + location.search + '#/projects');
    }
    lastHash = location.hash;

    const THEME_KEY = 'longform-theme';
    const themeBtn = document.getElementById('theme-toggle');
    function applyTheme(theme) {
      const dark = theme === 'dark';
      document.documentElement.dataset.theme = dark ? 'dark' : 'light';
      if (themeBtn) {
        themeBtn.setAttribute('aria-pressed', dark ? 'true' : 'false');
        themeBtn.setAttribute('aria-label', dark ? '切换到日间主题' : '切换到夜间主题');
        themeBtn.title = dark ? '切换到日间主题' : '切换到夜间主题';
      }
    }
    let savedTheme = null;
    try { savedTheme = localStorage.getItem(THEME_KEY); } catch (err) { savedTheme = null; }
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    applyTheme(savedTheme || (prefersDark ? 'dark' : 'light'));
    if (themeBtn) {
      themeBtn.addEventListener('click', () => {
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        try { localStorage.setItem(THEME_KEY, next); } catch (err) { /* 忽略隐私模式限制 */ }
      });
    }

    const commandBtn = document.getElementById('command-btn');
    if (commandBtn) commandBtn.addEventListener('click', openCommandPalette);

    const projectBtn = document.getElementById('project-badge');
    if (projectBtn) projectBtn.addEventListener('click', handler(toggleProjectMenu));

    const navToggle = document.getElementById('nav-toggle');
    if (navToggle) {
      navToggle.addEventListener('click', () => {
        const nav = document.getElementById('nav');
        setNavOpen(!(nav && nav.classList.contains('is-open')));
      });
    }

    const backdrop = document.getElementById('nav-backdrop');
    if (backdrop) backdrop.addEventListener('click', () => setNavOpen(false));

    const skip = document.querySelector('.skip-link');
    if (skip) {
      skip.addEventListener('click', (event) => {
        event.preventDefault();
        const view = document.getElementById('view');
        if (view) view.focus();
      });
    }

    const nav = document.getElementById('nav');
    if (nav) {
      nav.addEventListener('click', (event) => {
        if (event.target.closest('a')) setNavOpen(false);
      });
    }

    document.addEventListener('click', (event) => {
      const wrap = document.getElementById('project-switch');
      if (wrap && !wrap.contains(event.target)) closeProjectMenu();
    });

    render();
  }

  window.addEventListener('hashchange', render);
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && (event.key === 'k' || event.key === 'K')) {
      event.preventDefault();
      openCommandPalette();
      return;
    }
    if (event.key === 'Escape') {
      closeModal();
      setNavOpen(false);
      closeProjectMenu();
      if (document.body.classList.contains('focus-mode')) applyFocusMode(false);
    } else if (event.key === 'Tab') {
      trapFocus(event);
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
