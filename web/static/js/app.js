/* ============================================================
   JM Reader — 全局逻辑中枢
   主题 · 导航 · Toast/Modal · 分类树 · 下载调度 · 页面路由
   ============================================================ */

/* ── 主题 ────────────────────────────────────────────────
 * 支持：system / light / dark / auto（定时夜间，带 start/end）
 * 持久化按用户"选择"存；实际生效主题需在渲染时调用 Theme.resolve() 计算
 * Theme.setPreferred(choice, opts)：存到 localStorage + 后端
 * Theme.applyEffective()：按当前 choice + 系统/定时计算，设 data-theme
 */
const Theme = {
  _nightStart: "22:00",
  _nightEnd: "06:00",
  _pref: "system",     // 用户选择：system/light/dark/auto
  _eff: "light",       // 当前实际生效 light/dark
  getPreferred() { return this._pref; },
  getEffective() { return this._eff; },
  get() { return this._eff; }, // 兼容旧逻辑：返回实际生效主题
  _parseTime(t) {
    if (!t) return null;
    const m = /^(\d{1,2}):(\d{2})$/.exec(t);
    if (!m) return null;
    return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
  },
  _inNightRange(now, start, end) {
    const n = now.getHours() * 60 + now.getMinutes();
    if (start <= end) return n >= start && n < end;   // 比如 08-20 白天区间
    return n >= start || n < end;                     // 跨零点：22-06 夜间
  },
  setEffective(eff) {
    if (eff !== "light" && eff !== "dark") eff = "light";
    this._eff = eff;
    document.documentElement.setAttribute("data-theme", eff);
    document.querySelectorAll('.theme-toggle i').forEach(icon => {
      icon.className = eff === "dark" ? "fas fa-sun" : "fas fa-moon";
    });
  },
  apply() {
    // 按 _pref + _nightStart/_nightEnd + 系统设置计算当前有效主题
    let eff = "light";
    const now = new Date();
    if (this._pref === "light") eff = "light";
    else if (this._pref === "dark") eff = "dark";
    else if (this._pref === "auto") {
      const s = this._parseTime(this._nightStart);
      const e = this._parseTime(this._nightEnd);
      const inNight = (s != null && e != null) ? this._inNightRange(now, s, e) : false;
      eff = inNight ? "dark" : "light";
    } else { // system 或其他未知值 → 跟随系统
      try {
        const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
        eff = prefersDark ? "dark" : "light";
      } catch (_) { eff = "light"; }
    }
    this.setEffective(eff);
    return eff;
  },
  setPreferred(pref, opts) {
    pref = String(pref || "system").toLowerCase();
    if (!["system", "light", "dark", "auto"].includes(pref)) pref = "system";
    this._pref = pref;
    if (opts) {
      if (opts.start != null) this._nightStart = opts.start;
      if (opts.end != null) this._nightEnd = opts.end;
    }
    try { localStorage.setItem("jm_theme", pref); } catch (_) {}
    try { localStorage.setItem("jm_theme_night_start", this._nightStart); } catch (_) {}
    try { localStorage.setItem("jm_theme_night_end", this._nightEnd); } catch (_) {}
    this.apply();
  },
  // 从 localStorage 恢复（首屏后端还没返回之前也能用）
  restoreFromLocal() {
    try {
      const p = localStorage.getItem("jm_theme");
      if (p) this._pref = p;
      const s = localStorage.getItem("jm_theme_night_start");
      if (s) this._nightStart = s;
      const e = localStorage.getItem("jm_theme_night_end");
      if (e) this._nightEnd = e;
    } catch (_) {}
    this.apply();
  },
  toggle() {
    // 顶栏快捷切换：在 system/auto 下直接走 light→dark→light；light/dark 下翻转
    if (this._pref === "system" || this._pref === "auto") {
      this.setPreferred(this._eff === "dark" ? "light" : "dark");
    } else {
      this.setPreferred(this._pref === "dark" ? "light" : "dark");
    }
    this.savePreferred();
  },
  sync(el) {
    const icon = el && el.querySelector("i");
    if (icon) icon.className = this.getEffective() === "dark" ? "fas fa-sun" : "fas fa-moon";
  },
  async savePreferred() {
    const payload = {
      theme: this._pref,
      theme_night_start: this._nightStart,
      theme_night_end: this._nightEnd,
    };
    const r = await api("POST", "/api/settings", payload);
    if (r.success) {
      setAppConfig("theme", this._pref);
      setAppConfig("theme_night_start", this._nightStart);
      setAppConfig("theme_night_end", this._nightEnd);
    }
    return r;
  }
};

// 首屏尽早本地恢复一次（后端返回后 applyGlobalSettings 会覆写）
try { Theme.restoreFromLocal(); } catch (_) {}

/* ── 列表型本地存储工厂（收藏标签 / 作者 / 常用标签 / 历史 共用）──
   消除原先每个 store 重复书写 list/save/add/remove 的样板代码。 */
function createListStore(KEY, cap = 50, opts = {}) {
  const prepend = opts.prepend !== false; // 默认新项插到最前；QuickTags 用 append
  return {
    KEY,
    list() { try { return JSON.parse(localStorage.getItem(KEY) || '[]'); } catch (_) { return []; } },
    save(arr) { try { localStorage.setItem(KEY, JSON.stringify(arr.slice(0, cap))); } catch (_) {} },
    add(v) { v = (v || '').trim(); if (!v) return; const a = this.list(); if (!a.includes(v)) { prepend ? a.unshift(v) : a.push(v); this.save(a); } },
    remove(v) { this.save(this.list().filter(x => x !== v)); },
    contains(v) { return this.list().includes(v); },
    toggle(v) { this.contains(v) ? this.remove(v) : this.add(v); },
    move(from, to) { const a = this.list(); const [x] = a.splice(from, 1); if (x !== undefined) { a.splice(to, 0, x); this.save(a); } }
  };
}

/* ── 收藏标签（全局，搜索页 + 详情页共用）────────────────── */
const FavTags = createListStore('jm_fav_tags', 50);

/* ── 收藏作者（全局）──────────────────────────────────────── */
const FavAuthors = createListStore('jm_fav_authors', 50);

/* ── 搜索历史（全局，下拉与偏好库共用，提升到模块级便于统一管理）── */
const SearchHistory = createListStore('jm_search_history', 10);

/* ── 搜索偏好持久化（排序 / 时间 / 方向）──────────────────── */
const SearchPrefs = {
  KEY: 'jm_search_prefs',
  get() { try { return JSON.parse(localStorage.getItem(this.KEY) || '{}'); } catch (_) { return {}; } },
  set(p) { try { localStorage.setItem(this.KEY, JSON.stringify({ ...this.get(), ...p })); } catch (_) {} }
};

/* ── 前端本地设置（阅读器预加载、标签高亮等，无需后端重建）── */
const Settings = {
  get(key, def) {
    try { const v = localStorage.getItem('jm_setting_' + key); return v === null ? def : JSON.parse(v); }
    catch (_) { return def; }
  },
  set(key, val) { try { localStorage.setItem('jm_setting_' + key, JSON.stringify(val)); } catch (_) {} }
};

/* ── 搜索偏好“库”：历史 / 收藏标签 / 收藏作者 的统一管理入口 ──
   关键能力：误删可恢复（删除只移入回收站，库面板可一键恢复）。
   下拉面板的删除也走这里，保证只有一条删除代码路径。 */
const PrefLibrary = (() => {
  const cats = {
    history: { store: SearchHistory, label: '历史' },
    tags:    { store: FavTags,       label: '标签' },
    authors: { store: FavAuthors,    label: '作者' },
  };
  const trashKey = (c) => 'jm_lib_trash_' + c;
  const getTrash = (c) => { try { return JSON.parse(localStorage.getItem(trashKey(c)) || '[]'); } catch (_) { return []; } };
  const setTrash = (c, a) => { try { localStorage.setItem(trashKey(c), JSON.stringify(a.slice(0, 50))); } catch (_) {} };
  return {
    cats,
    list(c) { return cats[c].store.list(); },
    add(c, v) { if (v && v.trim()) cats[c].store.add(v.trim()); },
    edit(c, oldV, newV) {
      newV = (newV || '').trim(); if (!newV || !oldV) return false;
      const a = cats[c].store.list(); const i = a.indexOf(oldV);
      if (i < 0) return false;
      if (a.includes(newV)) a.splice(i, 1); else a[i] = newV;
      cats[c].store.save(a); return true;
    },
    // 可恢复删除：从库移除并存入回收站
    remove(c, v) {
      if (!cats[c].store.list().includes(v)) return false;
      cats[c].store.remove(v);
      const t = getTrash(c).filter(x => x !== v); t.unshift(v); setTrash(c, t);
      return true;
    },
    restore(c, v) {
      const t = getTrash(c); if (!t.includes(v)) return false;
      setTrash(c, t.filter(x => x !== v));
      cats[c].store.add(v); return true;
    },
    trash(c) { return getTrash(c); },
    clearTrash(c) { setTrash(c, []); }
  };
})();

/* ── 搜索偏好“库”面板：历史 / 标签 / 作者 的统一管理（误删可恢复）──
   放在模块级，供设置页的「搜索偏好库」钻取面板调用。
   依赖：PrefLibrary（数据 + 回收站）、toastUndo（撤销提示）、escapeHtml。 */
function initPrefLib() {
  const shownRoot = document.getElementById('prefLibShown');
  const allRoot = document.getElementById('prefLibAll');
  if (!shownRoot || !allRoot) return;

  // 每个分类在「搜索栏展示」与「全部库」两区各渲染一份
  const CATS = [
    { cat: 'history', icon: 'fa-clock',    label: '搜索历史', placeholder: '输入历史词' },
    { cat: 'tags',    icon: 'fa-tag',      label: '收藏标签', placeholder: '输入标签' },
    { cat: 'authors', icon: 'fa-user-pen', label: '收藏作者', placeholder: '输入作者名' },
  ];
  // 拖到同义词框时，按分类推断别名类型
  const TYPE_OF = { history: 'keyword', tags: 'tag', authors: 'author' };

  function ensureBlock(parent, meta) {
    let block = parent.querySelector(`.pref-lib-block[data-cat="${meta.cat}"]`);
    if (block) return block;
    block = document.createElement('div');
    block.className = 'pref-lib-block';
    block.dataset.cat = meta.cat;
    block.innerHTML = `
      <div class="pref-lib-block__head"><i class="fas ${meta.icon}"></i> ${meta.label}</div>
      <div class="pref-lib-addrow">
        <input class="search-input" data-add-input placeholder="${meta.placeholder}">
        <button class="btn btn--sm btn--primary" data-add-btn><i class="fas fa-plus"></i></button>
      </div>
      <div class="pref-lib-list" data-list></div>
      <div class="pref-lib-trash" data-trash hidden></div>`;
    const input = block.querySelector('[data-add-input]');
    const addBtn = block.querySelector('[data-add-btn]');
    const add = () => {
      const v = input.value.trim();
      if (!v) { toast('请输入内容', 'warn'); return; }
      if (PrefLibrary.list(meta.cat).includes(v)) { toast('已存在', 'warn'); return; }
      PrefLibrary.add(meta.cat, v); input.value = ''; render();
    };
    addBtn.onclick = add;
    input.addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
    parent.appendChild(block);
    return block;
  }

  function makeChip(cat, v, deleted) {
    const type = TYPE_OF[cat];
    const chip = document.createElement('span');
    chip.className = 'sp-chip' + (deleted ? ' sp-chip--trash' : '');
    chip.dataset.cat = cat; chip.dataset.v = v; chip.dataset.type = type;
    chip.setAttribute('draggable', 'true');
    chip.innerHTML = `
      <span class="sp-chip__name">${escapeHtml(v)}</span>
      ${deleted
        ? `<button class="sp-restore" data-v="${escapeHtml(v)}" title="恢复"><i class="fas fa-rotate-left"></i></button>`
        : `<button class="sp-chip__edit" data-v="${escapeHtml(v)}" title="编辑"><i class="fas fa-pen"></i></button><button class="sp-chip__del" data-v="${escapeHtml(v)}" title="删除"><i class="fas fa-times"></i></button>`}`;
    chip.addEventListener('dragstart', e => {
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', v);
      try { e.dataTransfer.setData('application/x-jm', JSON.stringify({ cat, v, deleted: !!deleted })); } catch (_) {}
    });
    if (deleted) {
      chip.querySelector('.sp-restore').onclick = () => { PrefLibrary.restore(cat, v); render(); };
    } else {
      chip.querySelector('.sp-chip__del').onclick = () => {
        PrefLibrary.remove(cat, v); render();
        toastUndo(`已删除「${v}」，点此恢复`, () => { PrefLibrary.restore(cat, v); render(); });
      };
      chip.querySelector('.sp-chip__edit').onclick = () => {
        const newV = prompt('修改：', v);
        if (newV == null) return;
        if (!PrefLibrary.edit(cat, v, newV.trim())) { toast('修改失败或内容已存在', 'warn'); return; }
        render();
      };
    }
    return chip;
  }

  function render() {
    // 「搜索栏展示的」：仅当前库（active）
    CATS.forEach(meta => {
      const block = ensureBlock(shownRoot, meta);
      const listEl = block.querySelector('[data-list]');
      const arr = PrefLibrary.list(meta.cat);
      listEl.innerHTML = '';
      if (!arr.length) listEl.innerHTML = `<div class="manage-empty">暂无项目</div>`;
      else arr.forEach(v => listEl.appendChild(makeChip(meta.cat, v, false)));
    });
    // 「全部库（含已删除）」：active + 回收站
    CATS.forEach(meta => {
      const block = ensureBlock(allRoot, meta);
      const listEl = block.querySelector('[data-list]');
      const trashEl = block.querySelector('[data-trash]');
      const active = PrefLibrary.list(meta.cat);
      const trash = PrefLibrary.trash(meta.cat);
      listEl.innerHTML = '';
      if (!active.length) listEl.innerHTML = `<div class="manage-empty">（无展示项）</div>`;
      else active.forEach(v => listEl.appendChild(makeChip(meta.cat, v, false)));
      if (trash.length) {
        trashEl.hidden = false;
        trashEl.innerHTML = `<div class="pref-lib-trash__head">已删除（可拖回上方恢复 / 点 ↺ 恢复）<button class="sp-clear-trash" type="button" title="清空回收站">清空</button></div>`;
        trash.forEach(v => trashEl.appendChild(makeChip(meta.cat, v, true)));
        trashEl.querySelector('.sp-clear-trash').onclick = () => { PrefLibrary.clearTrash(meta.cat); render(); };
      } else {
        trashEl.hidden = true; trashEl.innerHTML = '';
      }
    });
  }

  // 拖放规则：
  //  - 上方「搜索栏展示的」chip 拖到「全部库」区 = 删除（移入回收站）
  //  - 「全部库」里已删除 chip 拖回「搜索栏展示的」区 = 恢复
  function wireDrop(zone, onDrop) {
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drop-target'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drop-target'));
    zone.addEventListener('drop', e => {
      e.preventDefault(); zone.classList.remove('drop-target');
      let payload = null;
      try { payload = JSON.parse(e.dataTransfer.getData('application/x-jm')); } catch (_) {}
      if (!payload) {
        const v = e.dataTransfer.getData('text/plain');
        if (!v) return;
        payload = { cat: null, v, deleted: false };
      }
      onDrop(payload);
    });
  }
  wireDrop(allRoot, p => {
    if (p.cat) { PrefLibrary.remove(p.cat, p.v); render(); toast(`已移入回收站：${p.v}`, 'success'); }
  });
  wireDrop(shownRoot, p => {
    if (p.cat && p.deleted) { PrefLibrary.restore(p.cat, p.v); render(); toast(`已恢复：${p.v}`, 'success'); }
  });

  // 同义词输入框：接受从偏好库拖入的标签 / 作者，自动匹配别名类型
  ['aliasCanon', 'aliasVariants'].forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('dragover', e => { e.preventDefault(); el.classList.add('drop-target'); });
    el.addEventListener('dragleave', () => el.classList.remove('drop-target'));
    el.addEventListener('drop', e => {
      e.preventDefault(); el.classList.remove('drop-target');
      let payload = null;
      try { payload = JSON.parse(e.dataTransfer.getData('application/x-jm')); } catch (_) {}
      const v = payload ? payload.v : e.dataTransfer.getData('text/plain');
      if (!v) return;
      const at = document.getElementById('aliasType');
      if (payload && at && (payload.type === 'tag' || payload.type === 'author')) at.value = payload.type;
      if (el.id === 'aliasVariants') {
        const cur = el.value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
        if (!cur.includes(v)) cur.push(v);
        el.value = cur.join('，');
      } else {
        el.value = v;
      }
      toast(`已填入：${v}`, 'success');
    });
  });

  render();
}

/* ── 常用标签（钉在搜索栏右侧的快捷标签，支持拖拽排序）────────
   与收藏标签不同：新项追加到末尾（prepend:false）。 */
const QuickTags = createListStore('jm_quick_tags', 15, { prepend: false });

/* ── API ───────────────────────────────────────────────── */
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  const data = await res.json().catch(() => ({}));
  return data;
}

/* ── 应用配置缓存（设置项在全局可用）──────────────────────── */
let appConfigCache = null;
async function loadAppConfig(force = false) {
  if (appConfigCache && !force) return appConfigCache;
  const r = await api('GET', '/api/settings');
  appConfigCache = r.success ? (r.data || {}) : {};
  return appConfigCache;
}
function setAppConfig(key, value) {
  if (!appConfigCache) appConfigCache = {};
  appConfigCache[key] = value;
}

/* v2.14：书架/搜索「每行列数」——0=自动（走断点自适应），2~8=固定列数 */
function applyGridColumns(kind) {
  const cfg = appConfigCache || {};
  const n = Math.min(10, Math.max(0, parseInt(cfg[kind], 10) || 0));
  const g = document.getElementById('comicGrid');
  if (!g) return;
  if (n >= 2) { g.classList.add('cols-fixed'); g.style.setProperty('--cols', n); }
  else { g.classList.remove('cols-fixed'); g.style.removeProperty('--cols'); }
}

/* ── Toast ─────────────────────────────────────────────── */
function toast(msg, type = 'info', ms = 2600) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = `toast toast--${type}`;
  const icon = type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-circle-exclamation'
    : type === 'warn' ? 'fa-triangle-exclamation' : 'fa-circle-info';
  el.innerHTML = `<i class="fas ${icon}"></i><span></span>`;
  el.querySelector('span').textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(10px)'; }, ms - 300);
  setTimeout(() => el.remove(), ms);
}
function toastClosable(msg, type = 'info', ms = 3000) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) return;
  const el = document.createElement('div');
  el.className = `toast toast--${type} toast--closable`;
  const icon = type === 'success' ? 'fa-check-circle' : type === 'error' ? 'fa-circle-exclamation'
    : type === 'warn' ? 'fa-triangle-exclamation' : 'fa-circle-info';
  el.innerHTML = `<i class="fas ${icon}"></i><span></span><button class="toast__close" title="关闭"><i class="fas fa-times"></i></button>`;
  el.querySelector('span').textContent = msg;
  const t = setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(10px)'; }, ms - 300);
  const t2 = setTimeout(() => { if (el.parentNode) el.remove(); }, ms);
  el.querySelector('.toast__close').onclick = () => { clearTimeout(t); clearTimeout(t2); el.remove(); };
  wrap.appendChild(el);
}
// 带“撤销”按钮的提示，用于可恢复的删除操作
function toastUndo(msg, onUndo, ms = 4500) {
  const wrap = document.getElementById('toastWrap');
  if (!wrap) { if (onUndo) onUndo(); return; }
  const el = document.createElement('div');
  el.className = 'toast toast--info toast--undo';
  el.innerHTML = `<i class="fas fa-rotate-left"></i><span></span><button class="toast__undo" type="button">撤销</button>`;
  el.querySelector('span').textContent = msg;
  let done = false;
  const finish = () => { if (el.parentNode) el.remove(); };
  const t = setTimeout(() => { el.style.opacity = '0'; setTimeout(finish, 300); }, ms);
  el.querySelector('.toast__undo').onclick = () => {
    if (done) return; done = true; clearTimeout(t);
    if (onUndo) onUndo(); finish();
  };
  wrap.appendChild(el);
}

/* ── Modal ─────────────────────────────────────────────── */
function openModal({ title, sub, bodyHtml, actions = [], center = false, grip = true }) {
  const root = document.getElementById('modalRoot');
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal-panel ${center ? 'modal-panel--center' : ''}">
      ${grip && !center ? '<div class="modal-grip"></div>' : ''}
      <h3 class="modal-title">${title}</h3>
      ${sub ? `<p class="modal-sub">${sub}</p>` : ''}
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-actions">${actions.map(a => `<button class="btn ${a.cls || ''}" data-act="${a.key}">${a.label}</button>`).join('')}</div>
    </div>`;
  root.appendChild(overlay);
  const panel = overlay.querySelector('.modal-panel');
  overlay.addEventListener('click', e => { if (e.target === overlay) closeModal(); });
  const close = () => overlay.remove();
  overlay.querySelector('.modal-actions').addEventListener('click', e => {
    const b = e.target.closest('[data-act]'); if (!b) return;
    const act = actions.find(a => a.key === b.dataset.act);
    if (act && act.onClick) act.onClick(close, overlay);
  });
  return { close, overlay, panel };
}
function closeModal() {
  const m = document.querySelector('.modal-overlay');
  if (m) m.remove();
}

/* ── 波纹反馈 ──────────────────────────────────────────── */
function attachRipple(root = document) {
  root.querySelectorAll('.btn, .nav-item, .quick-tag, .cat-item').forEach(el => {
    if (el.dataset.rippled) return;
    el.dataset.rippled = '1';
    el.addEventListener('click', e => {
      const r = el.getBoundingClientRect();
      const size = Math.max(r.width, r.height);
      const s = document.createElement('span');
      s.className = 'ripple';
      s.style.width = s.style.height = size + 'px';
      s.style.left = (e.clientX - r.left - size / 2) + 'px';
      s.style.top = (e.clientY - r.top - size / 2) + 'px';
      el.appendChild(s);
      setTimeout(() => s.remove(), 600);
    });
  });
}

/* ── 分类树选择器（支持新建父/子/层级分类） ─────────────── */
async function pickCategories(preset = [], onConfirm) {
  let tree = await api('GET', '/api/categories?tree=1');
  tree = tree.data || [];
  const selected = new Set(preset);

  function flattenWithParent(nodes, parentId = null) {
    let out = [];
    for (const n of nodes) {
      out.push({ id: n.id, name: n.name, parent_id: parentId });
      if (n.children && n.children.length) out = out.concat(flattenWithParent(n.children, n.id));
    }
    return out;
  }

  function renderNodes(nodes, depth = 0) {
    return nodes.map(n => {
      const hasChildren = n.children && n.children.length;
      const checked = selected.has(n.id) ? 'checked' : '';
      const childHtml = hasChildren ? `<div class="tree-children">${renderNodes(n.children, depth + 1)}</div>` : '';
      return `<div class="tree-node" style="padding-left:${depth * 14}px">
        <label class="tree-item">
          <input type="checkbox" value="${n.id}" ${checked} class="cat-cb" style="accent-color:var(--accent)">
          <i class="fas fa-folder tree-item__icon"></i>
          <span class="tree-item__name">${escapeHtml(n.name)}</span>
          ${n.comic_count != null ? `<span class="tree-item__count">${n.comic_count}</span>` : ''}
        </label>
        <button class="tree-add-child" data-add="${n.id}" title="在此分类下新建子分类"><i class="fas fa-plus"></i></button>
      </div>${childHtml}`;
    }).join('');
  }

  // 逐层创建/复用路径（A/B/C），返回叶子 id；中间层级已存在则复用
  async function createPath(raw) {
    const parts = raw.split('/').map(s => s.trim()).filter(Boolean);
    if (!parts.length) return null;
    let parentId = null, leafId = null;
    for (const p of parts) {
      const flat = flattenWithParent(tree);
      let found = null;
      for (const node of flat) { if (node.name === p && node.parent_id === parentId) { found = node; break; } }
      if (found) { leafId = found.id; parentId = found.id; continue; }
      const r = await api('POST', '/api/categories', { name: p, parent_id: parentId });
      if (!r.success || !r.data) { toast('创建分类失败：' + (r.message || ''), 'error'); return null; }
      leafId = r.data.id; parentId = leafId;
      const tt = await api('GET', '/api/categories?tree=1');
      tree = tt.data || tree;
    }
    return leafId;
  }

  const { close, overlay: ov } = openModal({
    title: '选择分类',
    sub: '可多选；点 + 在所选分类下建子分类；名称用 / 分隔可一次建“父/子”层级',
    bodyHtml: `<div class="tree" id="pickTree">${renderNodes(tree)}</div>
      <div style="margin-top:12px"><input class="batch-textarea" id="newCatName" placeholder="或输入新分类名（支持 A/B/C 层级），确认时自动创建" style="min-height:40px"></div>`,
    actions: [
      { key: 'cancel', label: '取消', cls: 'btn--ghost', onClick: c => c() },
      { key: 'ok', label: '确定', cls: 'btn--primary', onClick: async (c, ov) => {
        const checks = [...ov.querySelectorAll('.cat-cb:checked')].map(cb => parseInt(cb.value));
        const newName = ov.querySelector('#newCatName').value.trim();
        if (newName) {
          const lid = await createPath(newName);
          if (lid != null) checks.push(lid);
        }
        c();
        onConfirm(checks);
      }}
    ]
  });

  function rerender() {
    const el = ov.querySelector('#pickTree');
    if (!el) return;
    el.innerHTML = renderNodes(tree);
    el.querySelectorAll('.tree-add-child').forEach(b => {
      b.onclick = async () => {
        const name = prompt('在该分类下新建子分类名称：');
        if (!name || !name.trim()) return;
        const r = await api('POST', '/api/categories', { name: name.trim(), parent_id: parseInt(b.dataset.add) });
        if (r.success && r.data) {
          const tt = await api('GET', '/api/categories?tree=1');
          tree = tt.data || tree;
          selected.add(r.data.id);
          rerender();
          toast('已创建子分类', 'success');
        } else { toast('创建失败', 'error'); }
      };
    });
  }
  rerender();
}

/* ── 下载调度 ─────────────────────────────────────────── */
function monitorProgress(downloadId, label) {
  const tick = async () => {
    const r = await api('GET', `/api/download/progress/${downloadId}`);
    if (!r.success) return;
    const d = r.data;
    if (d.status === 'completed' || d.status === 'downloaded') {
      toast(`${label} 下载完成`, 'success');
    } else if (d.status === 'error') {
      toast(`${label} 失败：${d.message || ''}`, 'error');
    } else if (d.status === 'starting' || d.status === 'downloading') {
      if (d.progress != null && d.progress < 100) {
        // 轻量提示，不刷屏
      }
      setTimeout(tick, 1500);
    }
  };
  setTimeout(tick, 800);
}

async function downloadComic(jmId, cats = [], label) {
  label = label || `JM-${jmId}`;
  const r = await api('POST', `/api/download/${jmId}`, { category_ids: cats });
  if (r.success) {
    toast(`${label} 开始下载，可在「下载」页查看进度`, 'info');
    if (r.download_id) monitorProgress(r.download_id, label);
  } else {
    if (r.downloaded) toast('该漫画已下载', 'warn');
    else toast(r.message || '下载失败', 'error');
  }
  return r;
}

async function batchDownload(ids, cats = []) {
  if (!ids.length) { toast('请先输入漫画 ID', 'warn'); return; }
  const r = await api('POST', '/api/download/batch', { ids, category_ids: cats });
  if (r.success) {
    const started = (r.data || []).filter(d => d.status === 'downloading').length;
    const skipped = (r.data || []).filter(d => d.status === 'skipped').length;
    toast(`已启动 ${started} 本，跳过 ${skipped} 本`, 'success');
    (r.data || []).forEach(d => { if (d.download_id) monitorProgress(d.download_id, `JM-${d.jm_id}`); });
  } else {
    toast(r.message || '批量下载失败', 'error');
  }
}

/* ── 批量下载入口（搜索页 / 下载管理页共用）── */
function initBatchBar() {
  const inputs = document.getElementById('batchInputs');
  const cnt = document.getElementById('batchCount');
  if (!inputs) return;
  function collectIds() {
    return [...new Set([...inputs.querySelectorAll('input')].map(i => i.value.trim()).filter(s => /^\d+$/.test(s)).map(Number))];
  }
  function syncCount() {
    if (cnt) cnt.textContent = `${collectIds().length} 个`;
  }
  function addBox(focus = true) {
    const row = document.createElement('div');
    row.className = 'batch-row';
    row.innerHTML = `<input type="text" inputmode="numeric" placeholder="JM号" maxlength="10">
      <button class="rm" title="移除"><i class="fas fa-times"></i></button>`;
    const inp = row.querySelector('input');
    const rm = row.querySelector('.rm');
    rm.onclick = () => { row.remove(); syncCount(); };
    inp.addEventListener('input', () => {
      syncCount();
      const allRows = inputs.querySelectorAll('.batch-row');
      if (inp.value.trim() && row === allRows[allRows.length - 1]) addBox(false);
    });
    inputs.appendChild(row);
    if (focus) inp.focus();
    syncCount();
  }
  inputs.innerHTML = '';
  addBox(false); addBox(false);
  syncCount();
  const addB = document.getElementById('addBatchBox');
  const clearB = document.getElementById('clearBatch');
  const dlB = document.getElementById('batchDownloadBtn');
  if (addB) addB.onclick = () => addBox(true);
  if (clearB) clearB.onclick = () => { inputs.innerHTML = ''; addBox(false); addBox(false); syncCount(); };
  if (dlB) dlB.onclick = async () => {
    const ids = collectIds();
    if (!ids.length) { toast('请先输入漫画 ID', 'warn'); return; }
    pickCategories([], (cats) => batchDownload(ids, cats));
  };
}

/* ── 工具 ─────────────────────────────────────────────── */
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, m => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]
  ));
}
function parseIds(text) {
  return [...new Set(
    String(text || '').split(/[\s,，]+/).map(s => s.trim()).filter(s => /^\d+$/.test(s)).map(Number)
  )];
}
function renderPageChapter(pages, chapterCount) {
  const p = pages > 0 ? `${pages} 页` : '';
  const ch = chapterCount > 0 ? `${chapterCount} 章` : '';
  const text = [p, ch].filter(Boolean).join(' · ');
  return text ? `<div class="comic-card__meta">${escapeHtml(text)}</div>` : '';
}

/* ── 拉黑 / 别名 辅助 ─────────────────────────────────────── */
function showBlockHits(kind, name, hits) {
  const arr = hits || [];
  const list = arr.slice(0, 25)
    .map(h => `<li>${escapeHtml(h.title || ('JM-' + h.jm_id))}</li>`).join('')
    + (arr.length > 25 ? `<li>…等 ${arr.length} 部</li>` : '');
  openModal({
    title: '本地命中提示',
    sub: `已拉黑${kind}「${escapeHtml(name)}」`,
    bodyHtml: arr.length
      ? `<p class="muted">本地书架中有 <b>${arr.length}</b> 部命中作品（已灰显）：</p><ul class="hit-list">${list}</ul>`
      : `<p class="muted">本地书架中没有命中作品。</p>`,
    actions: [{ key: 'ok', label: '知道了', cls: 'btn--primary', onClick: c => c() }],
  });
}
async function blockWork(jmId, title) {
  const r = await api('POST', '/api/blocklist', { type: 'work', value: String(jmId) });
  if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
  toast(`已拉黑《${title || jmId}》`, 'success');
  document.querySelectorAll(`.comic-card[data-jm-id="${jmId}"]`).forEach(c => c.remove());
  pruneSearchSessionCards(c => String(c.id) === String(jmId));
  showBlockHits('作品', title || jmId, r.local_hits);
}
async function blockAuthor(author) {
  if (!author) return;
  const r = await api('POST', '/api/blocklist', { type: 'author', value: author });
  if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
  toast(`已拉黑作者「${author}」`, 'success');
  document.querySelectorAll('.comic-card').forEach(c => { if (c.dataset.author === author) c.remove(); });
  pruneSearchSessionCards(c => c.author === author);
  showBlockHits('作者', author, r.local_hits);
}
async function blockTag(tag) {
  if (!tag) return;
  const r = await api('POST', '/api/blocklist', { type: 'tag', value: tag });
  if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
  toast(`已拉黑标签「${tag}」`, 'success');
  // 当前结果中命中该标签的卡片移除（与作者拉黑一致）
  document.querySelectorAll('.comic-card').forEach(card => {
    const tags = (card.dataset.tags || '').split(',').filter(Boolean);
    if (tags.includes(tag)) card.remove();
  });
  pruneSearchSessionCards(c => (c.tags || []).includes(tag));
  showBlockHits('标签', tag, r.local_hits);
}
function showTagContextMenu(x, y, tag, el) {
  const old = document.getElementById('tagContextMenu');
  if (old) old.remove();
  const fav = FavTags.list().includes(tag);
  const menu = document.createElement('div');
  menu.id = 'tagContextMenu';
  menu.className = 'tag-context-menu';
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.innerHTML = `
    <div class="tag-context-item" data-act="fav"><i class="fas fa-star"></i> ${fav ? '取消收藏' : '收藏'}「${escapeHtml(tag)}」</div>
    <div class="tag-context-item tag-context-item--danger" data-act="block"><i class="fas fa-ban"></i> 屏蔽此标签</div>
  `;
  document.body.appendChild(menu);
  const close = () => menu.remove();
  setTimeout(() => document.addEventListener('click', function handler(e) {
    if (!menu.contains(e.target)) { close(); document.removeEventListener('click', handler); }
  }), 0);
  menu.addEventListener('click', (e) => {
    const item = e.target.closest('[data-act]');
    if (!item) return;
    const act = item.dataset.act;
    if (act === 'fav') {
      if (fav) { FavTags.remove(tag); if (el) el.classList.remove('on'); }
      else { FavTags.add(tag); if (el) el.classList.add('on'); }
      toast(fav ? '已取消收藏' : '已收藏标签', 'success');
    } else if (act === 'block') {
      blockTag(tag);
    }
    close();
  });
}
async function unblockWork(jmId) {
  const r = await api('DELETE', '/api/blocklist/by-value', { type: 'work', value: String(jmId) });
  if (r.success) toast('已取消拉黑', 'success'); else toast(r.message || '取消失败', 'error');
}
async function unblockAuthor(author) {
  const r = await api('DELETE', '/api/blocklist/by-value', { type: 'author', value: author });
  if (r.success) toast('已取消拉黑', 'success'); else toast(r.message || '取消失败', 'error');
}
async function unblockComic(jmId, author) {
  const r = await api('GET', `/api/blocklist/affects?jm_id=${jmId}&author=${encodeURIComponent(author || '')}`);
  if (!r.success || !r.data || !r.data.length) { toast('未在黑名单中找到', 'warn'); return; }
  for (const id of r.data) await api('DELETE', `/api/blocklist/${id}`);
  toast('已取消拉黑', 'success');
}

/* 屏蔽后同步清理搜索页 sessionStorage 缓存，避免切回搜索页时屏蔽项「复活」。
   搜索页用 sessionStorage 快照恢复卡片；后端虽已过滤搜索结果，但快照可能仍含旧卡片。 */
function pruneSearchSessionCards(predicate) {
  try {
    const SK = 'jm_search_state';
    const s = JSON.parse(sessionStorage.getItem(SK) || 'null');
    if (!s || !Array.isArray(s.cards) || !s.cards.length) return;
    const before = s.cards.length;
    s.cards = s.cards.filter(c => !predicate(c));
    if (s.cards.length !== before) sessionStorage.setItem(SK, JSON.stringify(s));
  } catch (_) {}
}


/* ── 导航高亮 ─────────────────────────────────────────── */
function setupNav() {
  const p = location.pathname;
  const cur = p.startsWith('/library') ? 'library'
    : p.startsWith('/settings') ? 'settings'
    : p.startsWith('/recycle') ? 'library'
    : p.startsWith('/reader') ? 'library'
    : 'search';   // /search · /home(302) · /detail 都归「发现」
  document.querySelectorAll('.nav-item').forEach(n => {
    n.classList.toggle('nav-item--active', n.dataset.route === cur);
  });
}

/* ── 封面懒加载 ───────────────────────────────────────── */
function setupLazyCovers(root = document) {
  const imgs = root.querySelectorAll('img[data-cover]');
  if (!('IntersectionObserver' in window) || !imgs.length) {
    imgs.forEach(loadCover); return;
  }
  const io = new IntersectionObserver((ents) => {
    ents.forEach(e => { if (e.isIntersecting) { loadCover(e.target); io.unobserve(e.target); } });
  }, { rootMargin: '200px' });
  imgs.forEach(i => io.observe(i));
}
async function loadCover(img) {
  const jmId = img.dataset.cover;
  try {
    const r = await api('GET', `/api/cover/${jmId}`);
    if (r.success && r.data && r.data.cover) {
      img.src = r.data.cover;
      img.onerror = () => { img.src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E"; };
    }
  } catch (e) {}
}

/* ═══════════════════════════════════════════════════════
   搜索页
   ═══════════════════════════════════════════════════════ */
/* 搜索结果卡片标签渲染：按 search_tag_limit 限制显示数量（0=全部） */
function cardTagsHtml(tags, selectedTags = null, favs = null) {
  if (!tags || !tags.length) return '';
  let shown = tags, extra = 0;
  let limit = 0;
  if (appConfigCache && appConfigCache.search_tag_limit !== undefined) {
    limit = parseInt(appConfigCache.search_tag_limit, 10) || 0;
  }
  if (limit > 0 && tags.length > limit) {
    shown = tags.slice(0, limit);
    extra = tags.length - limit;
  }
  if (favs === null) favs = FavTags.list();   // 调用方可传入已计算好的收藏集，避免逐卡解析
  let html = shown.map(t => {
    const isSel = selectedTags && selectedTags.has(t);
    const isFav = favs.includes(t);
    let cls = 'comic-card__tag';
    if (isSel || isFav) cls += ' on';
    return `<span class="${cls}" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</span>`;
  }).join('');
  if (extra > 0) html += `<span class="comic-card__tag comic-card__tag--more" title="还有 ${extra} 个标签">+${extra}</span>`;
  return html;
}

/* 绑定单张卡片内标签的点击（收藏切换）与右键（上下文菜单） */
function bindCardTagHandlers(card) {
  card.querySelectorAll('.comic-card__tag').forEach(tg => {
    tg.addEventListener('click', (e) => {
      e.stopPropagation();
      const t = tg.dataset.tag;
      if (FavTags.list().includes(t)) { FavTags.remove(t); tg.classList.remove('on'); }
      else { FavTags.add(t); tg.classList.add('on'); }
    });
    tg.addEventListener('contextmenu', (e) => {
      e.preventDefault();
      e.stopPropagation();
      showTagContextMenu(e.clientX, e.clientY, tg.dataset.tag, tg);
    });
  });
}

// 搜索/推荐结果补标签：v2.4.5 起关键词搜索不再逐卡抓详情，tags 字段为空；
// 推荐里的关键词种子同理。这里渲染后对缺标签的卡片批量调 /api/search/enrich 回填，
// 搜索/推荐均保持即时，标签随后补齐。gridEl 限定作用域，避免两 grid 同 id 卡片串味。
const _enrichTimers = {};
function enrichSearchCards(list, gridEl) {
  const root = gridEl || document.getElementById('comicGrid');
  if (!root) return;
  if (!list || !list.length) return;
  const need = list.filter(c => (c.id || c.album_id) && (!c.tags || !c.tags.length));
  if (!need.length) return;
  const ids = need.map(c => c.id || c.album_id).filter(Boolean);
  const chunks = [];
  for (let i = 0; i < ids.length; i += 12) chunks.push(ids.slice(i, i + 12));
  clearTimeout(_enrichTimers[root.id]);
  _enrichTimers[root.id] = setTimeout(async () => {
    for (const chunk of chunks) {
      try {
        const r = await api('GET', `/api/search/enrich?ids=${chunk.join(',')}`);
        if (!r || !r.success || !r.data) continue;
        Object.values(r.data).forEach(d => {
          const id = String(d.id);
          const src = list.find(c => String(c.id || c.album_id) === id);
          if (src) src.tags = d.tags || [];
          const card = root.querySelector(`.comic-card[data-jm-id="${id}"]`);
          if (!card) return;
          card.dataset.tags = (d.tags || []).join(',');
          const tagsEl = card.querySelector('.comic-card__tags');
          if (tagsEl) {
            tagsEl.innerHTML = cardTagsHtml(d.tags || [], new Set(), FavTags.list());
            bindCardTagHandlers(card);
          }
        });
      } catch (e) { /* 补标签失败静默，标签留空不影响主流程 */ }
    }
  }, 350);
}

/* 漫画卡片渲染（模块级，搜索页 / 发现页推荐 / 屏蔽预览 共用）
   opts.onAuthorClick(author)：点击作者名时的回调；不传则仅展示不可点。 */
function renderCards(list, append = true, target = null, opts = {}) {
  const grid = target || document.getElementById('comicGrid');
  if (!grid) return;
  if (!append) grid.innerHTML = '';
  const favTagsSet = FavTags.list();
  const favAuthorsSet = FavAuthors.list();
  const onAuthorClick = opts.onAuthorClick || null;
  list.forEach((c, i) => {
    const id = c.id || c.album_id || c.comic_id;
    const author = c.author || '';
    const isFavAuthor = author && favAuthorsSet.includes(author);
    const card = document.createElement('div');
    card.className = 'comic-card';
    card.dataset.jmId = id;
    card.dataset.title = c.title || '';
    card.dataset.author = author;
    card.dataset.tags = (c.tags || []).join(',');
    card.dataset.pages = String(c.pages || 0);
  card.style.animationDelay = (Math.min(i, 15) * 0.03) + 's';
    // P1：搜索结果本地状态样式（已下载/读完灰显卡 + 角标提示）
    const grayDl = !!c.__gray_dl, grayFin = !!c.__gray_fin, dled = !!c.downloaded, fin = !!c.finished;
    const cardCls = [grayDl || grayFin ? 'comic-card--gray' : '', dled ? 'is-downloaded' : '', fin ? 'is-finished' : ''].filter(Boolean).join(' ');
    if (cardCls) card.className += ' ' + cardCls;
    card.innerHTML = `
      ${dled ? `<span class="status-badge status-badge--dl"><i class="fas fa-check"></i> 已下载</span>` : ''}
      ${fin ? `<span class="status-badge status-badge--fin"><i class="fas fa-circle-check"></i> 已读完</span>` : ''}
      <img class="comic-card__cover" ${c.cover ? `src="${escapeHtml(c.cover)}"` : `data-cover="${id}" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E"`}>
      <div class="comic-card__body">
        <div class="comic-card__title">${escapeHtml(c.title || '未命名漫画')}</div>
        ${author ? `<div class="comic-card__meta"><span class="author-name" data-author="${escapeHtml(author)}">${escapeHtml(author)}</span></div>` : ''}
        ${renderPageChapter(c.pages, 0)}
        <div class="comic-card__tags">${cardTagsHtml(c.tags, opts.favSel || new Set(), favTagsSet)}</div>
        <div class="comic-card__actions unified">
          <button class="act act--primary" data-act="dl" title="下载"><i class="fas fa-download"></i></button>
          <button class="act act--toggle${isFavAuthor ? ' on' : ''}" data-act="fav-author" title="收藏作者"><i class="fas fa-star"></i></button>
          <button class="act act--danger" data-act="block-author" title="拉黑作者"><i class="fas fa-user-slash"></i></button>
          <button class="act act--danger" data-act="block-work" title="拉黑作品"><i class="fas fa-ban"></i></button>
        </div>
      </div>`;
    card.querySelector('[data-act="dl"]').onclick = (e) => {
      e.stopPropagation();
      pickCategories([], (cats) => downloadComic(id, cats, c.title));
    };
    card.querySelector('[data-act="fav-author"]').onclick = (e) => {
      e.stopPropagation();
      if (FavAuthors.list().includes(author)) { FavAuthors.remove(author); e.currentTarget.classList.remove('on'); }
      else { FavAuthors.add(author); e.currentTarget.classList.add('on'); }
    };
    card.querySelector('[data-act="block-author"]').onclick = (e) => {
      e.stopPropagation();
      blockAuthor(author);
    };
    card.querySelector('[data-act="block-work"]').onclick = (e) => {
      e.stopPropagation();
      blockWork(id, c.title);
    };
    // 点卡片空白处 → 漫画详情页（内部按钮/标签/作者各自处理，不误触发）
    card.style.cursor = 'pointer';
    card.onclick = (e) => {
      if (e.target.closest('[data-act], [data-tag], .author-name, .comic-card__tags, .comic-card__actions')) return;
      location.href = '/detail/' + id;
    };
    bindCardTagHandlers(card);
    const authorName = card.querySelector('.author-name');
    if (authorName && onAuthorClick) {
      authorName.addEventListener('click', (e) => {
        e.stopPropagation();
        onAuthorClick(authorName.dataset.author);
      });
      authorName.classList.add('author-name--clickable');
    }
    grid.appendChild(card);
  });
}

function initSearch() {
  const grid = document.getElementById('comicGrid');
  loadAppConfig().then(() => applyGridColumns('search_columns')).catch(() => {});
  const input = document.getElementById('searchInput');
  /* 发现态（未搜索：订阅新作 + 推荐）与搜索态（结果）互斥切换，
     对齐手机版 v6.1.0 起把「主页 + 搜索」合并成的发现页 */
  const discoverPane = document.getElementById('discoverPane');
  const searchPane = document.getElementById('searchPane');
  function setView(isSearch) {
    if (discoverPane) discoverPane.style.display = isSearch ? 'none' : '';
    if (searchPane) searchPane.style.display = isSearch ? '' : 'none';
  }
  const btn = document.getElementById('searchBtn');
  const loading = document.getElementById('loading');
  const empty = document.getElementById('emptyState');
  const countEl = document.getElementById('resultCount');
  const sortBySel = document.getElementById('sortBy');
  const timeSel = document.getElementById('timeRange');
  const dirSel = document.getElementById('sortDir');
  const resultSec = document.getElementById('resultSection');
  if (resultSec) resultSec.style.display = 'none'; // 未搜索时隐藏结果区，避免大块空白
  let page = 1, keyword = '', total = 0, loadingMore = false;
  const main = document.getElementById('appMain');
  const clearKwBtn = document.getElementById('clearKwBtn');
  loadAppConfig(); // 预载配置，使标签显示数量限制即时生效

  // ── 搜索偏好（范围 / 排序 / 时间 / 方向）持久化 ──
  const scopeTabs = Array.from(document.querySelectorAll('#searchModeTabs .d-mode'));
  function getScope() {
    const active = document.querySelector('#searchModeTabs .d-mode.is-active');
    return active ? active.dataset.mode : 'comic';
  }
  function setScope(mode) {
    scopeTabs.forEach(t => t.classList.toggle('is-active', t.dataset.mode === mode));
  }
  (function restoreSearchPrefs() {
    const p = SearchPrefs.get();
    if (sortBySel && p.sortBy) sortBySel.value = p.sortBy;
    if (timeSel && p.time) timeSel.value = p.time;
    if (dirSel && p.dir) dirSel.value = p.dir;
    if (p.scope && scopeTabs.some(t => t.dataset.mode === p.scope)) setScope(p.scope);
  })();
  function saveSearchPrefs() {
    SearchPrefs.set({
      scope: getScope(),
      sortBy: sortBySel ? sortBySel.value : 'mr',
      time: timeSel ? timeSel.value : 'a',
      dir: dirSel ? dirSel.value : 'desc'
    });
  }

  // ── 统一下拉面板 ──
  const searchDrop = document.getElementById('searchDrop');
  function showSearchDrop() {
    if (!searchDrop) return;
    renderSearchHistory();
    renderFavTags();
    renderFavAuthors();
    searchDrop.hidden = false;
    
  }
  function hideSearchDrop() {
    if (searchDrop) searchDrop.hidden = true;
  }

  // ── 搜索历史（数据来自模块级 SearchHistory）──
  function renderSearchHistory() {
    const listEl = document.getElementById('searchHistoryList');
    const emptyEl = document.getElementById('searchHistoryEmpty');
    if (!listEl) return;
    const arr = SearchHistory.list();
    if (emptyEl) emptyEl.style.display = arr.length ? 'none' : 'block';
    listEl.innerHTML = arr.map(k => `<span class="sh-chip" data-k="${escapeHtml(k)}">
        ${escapeHtml(k)}
        <button class="sh-del" data-k="${escapeHtml(k)}" title="删除"><i class="fas fa-times"></i></button>
      </span>`).join('');
    listEl.querySelectorAll('.sh-chip').forEach(it => {
      it.addEventListener('click', (e) => {
        if (e.target.closest('.sh-del')) return;
        input.value = it.dataset.k;
        hideSearchDrop();
        clearState(); doSearch(true);
      });
    });
    listEl.querySelectorAll('.sh-del').forEach(b => {
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const k = b.dataset.k;
        PrefLibrary.remove('history', k);
        renderSearchHistory();
        toastUndo(`已删除「${k}」，点此恢复`, () => { PrefLibrary.restore('history', k); renderSearchHistory(); });
      });
    });
    const clr = document.getElementById('shClear');
    if (clr) clr.onclick = () => {
      if (!confirm('确定清空全部搜索历史？可在「搜索偏好库」恢复')) return;
      SearchHistory.list().forEach(k => PrefLibrary.remove('history', k));
      renderSearchHistory();
    };
  }

  // ── 收藏标签 / 作者：统一的芯片列表渲染（消除两套重复逻辑）──
  const favSel = new Set();
  const authorSel = new Set();
  const searchCardOpts = {
    favSel,
    onAuthorClick: (author) => {
      input.value = '';
      authorSel.clear(); favSel.clear();
      authorSel.add(author);
      hideSearchDrop();
      clearState(); doSearch(true);
    }
  };
  function renderChipList(listEl, emptyEl, arr, selSet, cls, dataAttr, delCls, cat) {
    if (!listEl) return;
    if (emptyEl) emptyEl.style.display = arr.length ? 'none' : 'block';
    listEl.innerHTML = arr.map(v => `<span class="${cls}${selSet.has(v) ? ' selected' : ''}" draggable="true" data-${dataAttr}="${escapeHtml(v)}">
        ${escapeHtml(v)}
        <button class="${delCls}" data-${dataAttr}="${escapeHtml(v)}" title="删除收藏"><i class="fas fa-times"></i></button>
      </span>`).join('');
    listEl.querySelectorAll('.' + cls).forEach(el => {
      el.addEventListener('click', (e) => {
        if (e.target.closest('.' + delCls)) return;
        const v = el.dataset[dataAttr];
        if (selSet.has(v)) selSet.delete(v); else selSet.add(v);
        el.classList.toggle('selected');
        updateQuickTagHighlight();
      });
      el.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', el.dataset[dataAttr]); });
    });
    listEl.querySelectorAll('.' + delCls).forEach(b => {
      b.addEventListener('click', (e) => {
        e.stopPropagation();
        const v = b.dataset[dataAttr];
        PrefLibrary.remove(cat, v);          // 可恢复删除（移入回收站）
        selSet.delete(v);
        renderChipList(listEl, emptyEl, PrefLibrary.list(cat), selSet, cls, dataAttr, delCls, cat);
        updateQuickTagHighlight();
        toastUndo(`已删除「${v}」，点此恢复`, () => {
          PrefLibrary.restore(cat, v);
          renderChipList(listEl, emptyEl, PrefLibrary.list(cat), selSet, cls, dataAttr, delCls, cat);
        });
      });
    });
  }
  function renderFavTags() {
    renderChipList(document.getElementById('favTagsList'), document.getElementById('favTagsEmpty'), FavTags.list(), favSel, 'fav-tag', 't', 'ft-del', 'tags');
  }
  function renderFavAuthors() {
    renderChipList(document.getElementById('favAuthorsList'), document.getElementById('favAuthorsEmpty'), FavAuthors.list(), authorSel, 'fav-author', 'a', 'fa-del', 'authors');
  }
  const favAuthorsClear = document.getElementById('favAuthorsClear');
  if (favAuthorsClear) favAuthorsClear.onclick = () => {
    if (!confirm('确定清空所有收藏作者？可在「搜索偏好库」恢复')) return;
    FavAuthors.list().forEach(a => PrefLibrary.remove('authors', a));
    authorSel.clear(); renderFavAuthors();
  };
  // 标签「清空」：清除本次已选标签（不删收藏列表，方便重新挑选）
  const clearTagsBtn = document.getElementById('clearTagsBtn');
  if (clearTagsBtn) clearTagsBtn.onclick = () => {
    if (favSel.size === 0) { toast('当前没有已选标签', 'warn'); return; }
    favSel.clear(); renderFavTags();  updateQuickTagHighlight();
  };

  function getSearchMode() {
    const active = searchDrop && searchDrop.querySelector('.td-mode-btn.active');
    return active ? active.dataset.mode : 'or';
  }
  function updateSearchCond(kw, tagList, authorList, mode, scope) {
    const el = document.getElementById('searchCond');
    if (!el) return;
    const scopeLabel = { comic: '', title: '标题', author: '作者', tag: '标签' }[scope || 'comic'];
    const parts = [];
    if (scopeLabel) parts.push(`范围:${scopeLabel}`);
    if (kw) parts.push(`「${kw}」`);
    if (tagList && tagList.length) parts.push(`标签:${tagList.join(' ')}（${mode === 'and' ? '且' : '或'}）`);
    if (authorList && authorList.length) parts.push(`作者:${authorList.join(' ')}`);
    if (parts.length) { el.textContent = ' · ' + parts.join(' · '); el.style.display = ''; }
    else el.style.display = 'none';
  }
  // 命中关键词高亮（标题 + 作者），标签范围不高亮（关键词是标签名）
  function highlightResults(kw, scope) {
    if (!kw || scope === 'tag') return;
    const kwe = escapeHtml(kw).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    if (!kwe) return;
    const re = new RegExp('(' + kwe + ')', 'gi');
    grid.querySelectorAll('.comic-card').forEach(card => {
      const t = card.querySelector('.comic-card__title');
      if (t && card.dataset.title) t.innerHTML = escapeHtml(card.dataset.title).replace(re, '<mark>$1</mark>');
      const a = card.querySelector('.author-name');
      if (a && card.dataset.author) a.innerHTML = escapeHtml(card.dataset.author).replace(re, '<mark>$1</mark>');
    });
  }
  // 空态推荐：展示常用/收藏标签，点一下即按该标签搜索
  function renderEmptySuggest() {
    const box = document.getElementById('searchEmptySuggest');
    if (!box) return;
    const tags = Array.from(new Set([...FavTags.list(), ...QuickTags.list()])).slice(0, 12);
    if (!tags.length) { box.style.display = 'none'; box.innerHTML = ''; return; }
    box.style.display = '';
    box.innerHTML = `<div class="ses-title">没找到，试试这些标签：</div><div class="ses-tags">` +
      tags.map(t => `<span class="ses-chip" data-t="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join('') + `</div>`;
    box.querySelectorAll('.ses-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const t = chip.dataset.t;
        if (!favSel.has(t)) { favSel.add(t); renderFavTags(); updateQuickTagHighlight(); }
        hideSearchDrop(); clearState(); doSearch(true);
      });
    });
  }

  // sessionStorage 持久化
  const SK_KEY = 'jm_search_state';
  const SCROLL_KEY = 'jm_search_scroll';
  function loadState() {
    try {
      const s = JSON.parse(sessionStorage.getItem(SK_KEY) || 'null');
      if (!s) return false;
      input.value = s.keyword || '';
      if (sortBySel) sortBySel.value = s.order_by || 'mr';
      if (timeSel) timeSel.value = s.time_range || 'a';
      if (dirSel) dirSel.value = s.sort_order || 'desc';
      // 恢复本次已选标签/作者（避免切回搜索页后选择被清空）
      if (Array.isArray(s.sel_tags)) { s.sel_tags.forEach(t => favSel.add(t)); }
      if (Array.isArray(s.sel_authors)) { s.sel_authors.forEach(a => authorSel.add(a)); }
      if (s.keyword && s.cards && s.cards.length) {
        const resultSec = document.getElementById('resultSection');
        if (resultSec) resultSec.style.display = '';
        keyword = s.keyword; page = s.page || 1; total = s.total || 0;
        renderCards(s.cards, false, grid, searchCardOpts);
        enrichSearchCards(s.cards, grid); // 切回搜索页恢复缓存后仍需补标签（saveState 在 enrich 异步回填前执行，缓存标签可能为空）
        if (countEl) countEl.textContent = `${s.total || 0} 个结果（已恢复）`;
        document.getElementById('loadMore').style.display = (s.hasMore || false) ? 'block' : 'none';
        renderFavTags(); renderFavAuthors(); updateQuickTagHighlight();         updateSearchCond(keyword, [...favSel], [...authorSel], getSearchMode());
        syncClearKwBtn();
        return true;
      }
    } catch (_) {}
    return false;
  }
  function saveState(hasMore = false) {
    try {
      const cards = [...grid.querySelectorAll('.comic-card')].map(card => ({
        id: card.dataset.jmId,
        title: card.dataset.title,
        author: card.dataset.author,
        tags: (card.dataset.tags || '').split(',').filter(Boolean),
        pages: card.dataset.pages ? parseInt(card.dataset.pages, 10) : 0,
      }));
      sessionStorage.setItem(SK_KEY, JSON.stringify({
        keyword, page, total,
        order_by: sortBySel ? sortBySel.value : 'mr',
        time_range: timeSel ? timeSel.value : 'a',
        sort_order: dirSel ? dirSel.value : 'desc',
        sel_tags: [...favSel],
        sel_authors: [...authorSel],
        cards,
        hasMore,
      }));
    } catch (_) {}
  }
  function clearState() {
    try {
      sessionStorage.removeItem(SK_KEY);
      sessionStorage.removeItem(SCROLL_KEY);
    } catch (_) {}
  }

  async function doSearch(reset = true) {
    keyword = input.value.trim();
    const scope = getScope();
    const selTags = [...favSel];
    const selAuthors = [...authorSel];
    const hasKw = !!keyword, hasTag = selTags.length > 0, hasAuthor = selAuthors.length > 0;
    if (!hasKw && !hasTag && !hasAuthor) { toast('请输入关键词、选择标签或选择作者', 'warn'); return; }
    setView(true);   // 切到搜索态（隐藏发现区块）
    if (reset) {
      if (keyword) SearchHistory.add(keyword);
      page = 1; grid.innerHTML = ''; total = 0;
      try { sessionStorage.removeItem(SCROLL_KEY); } catch (_) {}
    }
    loading.style.display = 'block'; empty.style.display = 'none';
    const suggestBox = document.getElementById('searchEmptySuggest');
    if (suggestBox) suggestBox.style.display = 'none';
    document.getElementById('loadMore').style.display = 'none';
    const mode = getSearchMode();
    const orderBy = sortBySel ? sortBySel.value : 'mr';
    const timeRange = timeSel ? timeSel.value : 'a';
    const sortOrder = dirSel ? dirSel.value : 'desc';
    const isNum = /^\d+$/.test(keyword);
    const enc = encodeURIComponent;
    let res, condKw = '', condTags = [], condAuthors = [];

    try {
      if (hasTag || hasAuthor) {
        // 联合搜索：关键词 + 已选标签 + 已选作者；scope 决定「纯关键词」如何解释
        const params = new URLSearchParams();
        if (scope === 'author' && hasKw && !hasAuthor) {
          params.set('author', keyword); condAuthors = [keyword];
        } else if (scope === 'tag' && hasKw && !hasTag) {
          params.set('tags', keyword); condTags = [keyword];
        } else {
          if (hasKw) params.set('keyword', keyword);
          condKw = keyword;
        }
        if (selTags.length) { params.set('tags', selTags.join(',')); condTags = condTags.concat(selTags); }
        if (selAuthors.length) { params.set('author', selAuthors.join(',')); condAuthors = condAuthors.concat(selAuthors); }
        params.set('mode', mode);
        res = await api('GET', `/api/search/combined?${params.toString()}`);
      } else if (hasKw) {
        if (scope === 'author' && !isNum) {
          condAuthors = [keyword];
          res = await api('GET', `/api/search/author?author=${enc(keyword)}`);
        } else if (scope === 'tag') {
          condTags = [keyword];
          res = await api('GET', `/api/search/tags?tags=${enc(keyword)}&mode=${mode}`);
        } else if (isNum) {
          condKw = keyword;
          res = await api('GET', `/api/search/jm/${keyword}`);
        } else {
          condKw = keyword;
          res = await api('GET', `/api/search/keyword?keyword=${enc(keyword)}&page=${page}&sort=${sortOrder}&order_by=${orderBy}&time=${timeRange}`);
        }
      }
      loading.style.display = 'none';
      if (res.success && res.data && res.data.length) {
        renderCards(res.data, true, grid, searchCardOpts); total = res.data.length;
        enrichSearchCards(res.data, grid); // 搜索结果渲染后按需补标签（v2.4.5 起关键词搜索不再带 tags）
        countEl.textContent = `${total} 个结果`;
        highlightResults(keyword, scope);
        if (!hasTag && !hasAuthor) {
          const hasMore = res.data.length >= 20;
          document.getElementById('loadMore').style.display = hasMore ? 'block' : 'none';
        }
      } else if (res.success) {
        empty.style.display = 'block'; countEl.textContent = '';
        renderEmptySuggest();
      } else {
        empty.style.display = 'block'; countEl.textContent = '';
        toast(res.message || '搜索失败', 'error');
      }
    } catch (e) {
      loading.style.display = 'none'; empty.style.display = 'block';
      toast('搜索失败：' + (e.message || e), 'error');
    }

    updateSearchCond(condKw, condTags, condAuthors, mode, scope);
    attachRipple(grid);
    setupLazyCovers(grid);
    saveState(false);
  }

  // ── 事件绑定 ──
  // 搜索范围 Tab：切换即生效（重新搜索当前条件；纯视觉切换交给模板内脚本）
  scopeTabs.forEach(t => t.addEventListener('click', () => {
    scopeTabs.forEach(x => x.classList.remove('is-active'));
    t.classList.add('is-active');
    saveSearchPrefs();
    if (input.value.trim() || favSel.size || authorSel.size) { clearState(); doSearch(true); }
  }));
  btn.onclick = () => { hideSearchDrop(); clearState(); doSearch(true); };
  input.addEventListener('keydown', e => { if (e.key === 'Enter') { hideSearchDrop(); clearState(); doSearch(true); } });
  input.addEventListener('focus', showSearchDrop);
  input.addEventListener('click', showSearchDrop); // 已聚焦时点击也能展开
  input.addEventListener('input', () => {
    if (clearKwBtn) clearKwBtn.hidden = !input.value.trim();
    if (!input.value.trim()) showSearchDrop();
  });
  // 失去焦点时延迟关闭，方便点击下拉内部
  input.addEventListener('blur', () => setTimeout(hideSearchDrop, 180));
  document.getElementById('loadMore').onclick = () => { page++; doSearch(false); };
  [sortBySel, timeSel, dirSel].forEach(el => {
    if (!el) return;
    el.addEventListener('change', () => {
      saveSearchPrefs();
      clearState(); doSearch(true);
    });
  });

  // ── 批量下载入口（独立函数，搜索页与下载管理页共用）──
  initBatchBar();

  // 下拉面板开关（点击标签区空白处展开）
  const sbTagArea = document.getElementById('sbTagArea');
  if (sbTagArea) {
    sbTagArea.addEventListener('click', (e) => {
      if (e.target.closest('.qt-chip')) return;       // 点芯片只切换选中，不展开面板
      if (e.target.closest('.batch-bar')) return;      // 点批量下载区不展开下拉
      e.stopPropagation();
      if (searchDrop && !searchDrop.hidden) hideSearchDrop(); else showSearchDrop();
    });
  }
  if (searchDrop) {
    searchDrop.querySelectorAll('.td-mode-btn').forEach(b => {
      b.addEventListener('click', () => {
        searchDrop.querySelectorAll('.td-mode-btn').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
      });
    });
    searchDrop.addEventListener('mousedown', e => e.preventDefault()); // 防止内部点击触发 input blur
  }
  document.addEventListener('click', (e) => {
    if (searchDrop && !searchDrop.hidden &&
        !e.target.closest('#searchDrop') && !e.target.closest('#sbTagArea') && !e.target.closest('#searchInput')) {
      hideSearchDrop();
    }
  });

  // ── 清空按钮 ──
  function syncClearKwBtn() {
    if (!clearKwBtn) return;
    clearKwBtn.classList.toggle('sb-clear--visible', !!input.value.trim());
  }
  if (clearKwBtn) clearKwBtn.onclick = () => { input.value = ''; syncClearKwBtn(); clearState(); input.focus(); };
  input.addEventListener('input', syncClearKwBtn);
  input.addEventListener('focus', syncClearKwBtn);
  syncClearKwBtn();
  // 清空已选标签按钮已移到搜索按钮旁（clearTagsBtn）

  // ── 常用标签（钉在搜索栏，拖拽排序）──
  const qtagsEl = document.getElementById('quickTags');
  function renderQuickTags() {
    if (!qtagsEl) return;
    const arr = QuickTags.list();
    qtagsEl.innerHTML = arr.map((t, i) =>
      `<span class="qt-chip" draggable="true" data-idx="${i}" data-t="${escapeHtml(t)}">
        ${escapeHtml(t)}<button class="qt-del" data-t="${escapeHtml(t)}">&times;</button>
      </span>`).join('');
    qtagsEl.querySelectorAll('.qt-chip').forEach(chip => {
      chip.addEventListener('dragstart', e => { e.dataTransfer.setData('text/plain', chip.dataset.idx); chip.classList.add('qt-drag'); });
      chip.addEventListener('dragend', () => chip.classList.remove('qt-drag'));
      chip.addEventListener('dragover', e => e.preventDefault());
      chip.addEventListener('drop', e => {
        e.preventDefault();
        const from = parseInt(e.dataTransfer.getData('text/plain'));
        const to = parseInt(chip.dataset.idx);
        if (!isNaN(from) && !isNaN(to) && from !== to) { QuickTags.move(from, to); renderQuickTags(); updateQuickTagHighlight(); }
      });
      chip.addEventListener('click', e => {
        if (e.target.closest('.qt-del')) return;
        const t = chip.dataset.t;
        if (favSel.has(t)) favSel.delete(t); else favSel.add(t);
        chip.classList.toggle('qt-on');
        renderFavTags();        });
      chip.querySelector('.qt-del').addEventListener('click', e => { e.stopPropagation(); QuickTags.remove(chip.dataset.t); favSel.delete(chip.dataset.t); renderQuickTags();   });
      if (favSel.has(chip.dataset.t)) chip.classList.add('qt-on');
    });
  }
  function updateQuickTagHighlight() {
    if (!qtagsEl) return;
    qtagsEl.querySelectorAll('.qt-chip').forEach(chip => chip.classList.toggle('qt-on', favSel.has(chip.dataset.t)));
  }
  renderQuickTags();
  // 常用标签区接收从下拉拖入的标签
  if (qtagsEl) {
    qtagsEl.addEventListener('dragover', e => { e.preventDefault(); qtagsEl.classList.add('qt-drop'); });
    qtagsEl.addEventListener('dragleave', () => qtagsEl.classList.remove('qt-drop'));
    qtagsEl.addEventListener('drop', e => {
      e.preventDefault(); qtagsEl.classList.remove('qt-drop');
      const t = e.dataTransfer.getData('text/plain');
      if (t && !QuickTags.list().includes(t)) { QuickTags.add(t); renderQuickTags(); updateQuickTagHighlight(); }
    });
  }

  // ── 回到顶部 ──
  const backBtn = document.getElementById('backToTop');
  if (backBtn && main) {
    main.addEventListener('scroll', () => { backBtn.hidden = main.scrollTop < 300; });
    backBtn.addEventListener('click', () => { main.scrollTop = 0; main.style.scrollBehavior = 'auto'; });
  }

  // URL 参数：支持 ?author=xxx 自动搜索
  let urlTriggered = false;
  (function applyUrlParams() {
    const params = new URLSearchParams(location.search);
    const authorParam = (params.get('author') || '').trim();
    const tagParam = (params.get('tag') || '').trim();
    if (authorParam) { authorSel.add(authorParam);   }
    if (tagParam) { favSel.add(tagParam);   updateQuickTagHighlight(); }
    if (authorParam || tagParam) { urlTriggered = true; hideSearchDrop(); clearState(); doSearch(true); }
  })();

  // 恢复上次搜索结果与滚动位置
  const restored = !urlTriggered && loadState();

  // 初始态：带搜索条件进来 / 恢复了上次搜索 → 搜索态；否则显示发现页
  if (urlTriggered || restored) {
    setView(true);
  } else {
    setView(false);
    initDiscover();
  }

  // 「返回发现」：清掉搜索条件与结果，回到发现态（推荐 + 订阅新作）
  const backToDiscover = document.getElementById('backToDiscover');
  if (backToDiscover) backToDiscover.onclick = () => {
    input.value = '';
    try { sessionStorage.removeItem(SCROLL_KEY); } catch (_) {}
    grid.innerHTML = '';
    if (countEl) countEl.textContent = '';
    if (empty) empty.style.display = 'none';
    if (typeof clearState === 'function') clearState();
    setView(false);
    initDiscover();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };
  if (restored) {
    attachRipple(grid);
    setupLazyCovers(grid);
    try {
      const sy = parseInt(sessionStorage.getItem(SCROLL_KEY) || '0', 10) || 0;
      if (sy > 0 && main) {
        const prev = main.style.scrollBehavior;
        main.style.scrollBehavior = 'auto';
        let tries = 0;
        const applyScroll = () => {
          main.scrollTop = sy;
          if (tries++ < 20) setTimeout(applyScroll, 50);
          else main.style.scrollBehavior = prev;
        };
        requestAnimationFrame(applyScroll);
        window.addEventListener('load', () => { main.scrollTop = sy; }, { once: true });
      }
    } catch (_) {}
  }
  // 多路保存滚动位置
  let _scrollSaveTimer = null;
  const saveScrollPos = () => {
    try { if (main) sessionStorage.setItem(SCROLL_KEY, String(main.scrollTop || 0)); } catch (_) {}
  };
  if (main) {
    main.addEventListener('scroll', () => {
      if (_scrollSaveTimer) return;
      _scrollSaveTimer = setTimeout(() => { _scrollSaveTimer = null; saveScrollPos(); }, 150);
    }, { passive: true });
  }
  window.addEventListener('beforeunload', saveScrollPos);
  window.addEventListener('pagehide', saveScrollPos);
  document.addEventListener('visibilitychange', () => { if (document.hidden) saveScrollPos(); });
  document.addEventListener('click', saveScrollPos, true);
}

/* ═══════════════════════════════════════════════════════
   主页（推荐）
   ═══════════════════════════════════════════════════════ */
/* 发现页内容（订阅新作 + 为你推荐）
   v2.11.0：原「主页」并入发现页，本函数由 initSearch 在发现态调用 */
function initDiscover() {
  const recommendGrid = document.getElementById('recommendGrid');
  const recommendLoading = document.getElementById('recommendLoading');
  const recommendEmpty = document.getElementById('recommendEmpty');
  const recommendNote = document.getElementById('recommendNote');
  if (!recommendGrid) return;
  async function loadRecommendations() {
    recommendGrid.innerHTML = '';
    if (recommendEmpty) recommendEmpty.style.display = 'none';
    if (recommendLoading) recommendLoading.style.display = 'block';
    if (recommendNote) recommendNote.textContent = '';
    try {
      const r = await api('GET', '/api/recommend');
      if (recommendLoading) recommendLoading.style.display = 'none';
      if (!r.success || !r.enabled) { if (recommendEmpty) recommendEmpty.style.display = 'block'; return; }
      const data = r.data || [];
      if (!data.length) {
        if (recommendEmpty) recommendEmpty.style.display = 'block';
        if (recommendNote && r.note) recommendNote.textContent = r.note;
        return;
      }
      renderCards(data, false, recommendGrid, {
        onAuthorClick: (author) => { location.href = `/search?author=${encodeURIComponent(author)}`; }
      });
      enrichSearchCards(data, recommendGrid); // 推荐里关键词种子同样不带 tags，按需补
      attachRipple(recommendGrid);
      setupLazyCovers(recommendGrid);
      if (recommendNote) {
        const src = (r.seeds && r.seeds.length) ? `基于 ${r.seeds.length} 个兴趣词` : '';
        recommendNote.textContent = src + (r.is_default ? ' · 默认' : '');
      }
    } catch (e) {
      if (recommendLoading) recommendLoading.style.display = 'none';
      if (recommendEmpty) recommendEmpty.style.display = 'block';
    }
  }
  // 推荐流只在首次进发现页时加载（避免每次从搜索结果返回都重新请求）
  if (!initDiscover._recLoaded) { initDiscover._recLoaded = true; loadRecommendations(); }

  /* ── 订阅更新区块 ─────────────────────────────── */
  const subSection = document.getElementById('subUpdatesSection');
  const subList = document.getElementById('subUpdatesList');
  const subManageBtn = document.getElementById('subManageBtn');

  const subCoverFallback = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E";

  function fmtRelative(ts) {
    if (!ts) return '';
    const diff = Date.now() - ts;
    const m = Math.floor(diff / 60000);
    if (m < 1) return '刚刚';
    if (m < 60) return m + ' 分钟前';
    const h = Math.floor(m / 60);
    if (h < 24) return h + ' 小时前';
    const d = Math.floor(h / 24);
    return d + ' 天前';
  }

  async function loadSubUpdates() {
    try {
      const r = await api('GET', '/api/sub_updates?unseen=1');
      const items = r.data || [];
      if (!subSection || !subList) return;
      if (!items.length) { subSection.style.display = 'none'; return; }
      subSection.style.display = '';
      subList.innerHTML = items.map(u => {
        const cover = u.cover_url ? `src="${escapeHtml(u.cover_url)}"` : `src="${subCoverFallback}"`;
        const go = u.book_id ? `onclick="location.href='/reader/${u.book_id}'"` : '';
        return `
        <div class="sub-update-item" ${go} title="${escapeHtml(u.title || '')}">
          <img class="sub-update-item__cover" ${cover} loading="lazy"
               onerror="this.onerror=null;this.src='${subCoverFallback}'">
          <div class="sub-update-item__info">
            <div class="sub-update-item__title">${escapeHtml(u.title || '未命名')}</div>
            <div class="sub-update-item__author">${escapeHtml(u.author || '')}</div>
          </div>
          <span class="sub-update-item__time">${fmtRelative(u.found_at)}</span>
          <span class="sub-update-item__badge">新</span>
        </div>`;
      }).join('');
      attachRipple(subList);
    } catch (_) {
      if (subSection) subSection.style.display = 'none';
    }
  }

  function openSubManage() {
    const modal = openModal({
      title: '管理订阅',
      bodyHtml: `
        <div class="sub-manage-add">
          <input class="sub-manage-input" placeholder="输入作者名并回车订阅" maxlength="64">
        </div>
        <div class="sub-manage-list"></div>`,
      center: true, grip: false
    });
    const listEl = modal.overlay.querySelector('.sub-manage-list');
    const inputEl = modal.overlay.querySelector('.sub-manage-input');

    async function renderSubs() {
      const r = await api('GET', '/api/subscriptions');
      const subs = r.data || [];
      if (!subs.length) {
        listEl.innerHTML = '<div class="empty" style="padding:22px 0"><p style="margin:0">还没有订阅任何作者</p></div>';
        return;
      }
      listEl.innerHTML = subs.map(s => `
        <div class="sub-manage-row" data-id="${s.id}">
          <div class="sub-manage-row__main">
            <div class="sub-manage-row__name">${escapeHtml(s.author)}</div>
            <div class="sub-manage-row__meta">${s.new_count ? `<span class="sub-update-item__badge">${s.new_count} 更新</span>` : ''}
              ${s.last_title ? `<span class="muted">最近：${escapeHtml(s.last_title)}</span>` : ''}</div>
          </div>
          <button class="btn btn--sm btn--danger" data-del="${s.id}"><i class="fas fa-trash"></i></button>
        </div>`).join('');
      listEl.querySelectorAll('[data-del]').forEach(btn => btn.addEventListener('click', async () => {
        const sid = btn.dataset.del;
        const d = await api('DELETE', `/api/subscriptions/${sid}`);
        if (d.success) { toast('已取消订阅', 'success'); renderSubs(); loadSubUpdates(); }
        else toast(d.message || '操作失败', 'error');
      }));
    }

    inputEl.addEventListener('keydown', async (e) => {
      if (e.key !== 'Enter') return;
      const author = inputEl.value.trim();
      if (!author) return;
      const r = await api('POST', '/api/subscriptions', { author });
      if (r.success) { toast('已订阅 ' + author, 'success'); inputEl.value = ''; renderSubs(); loadSubUpdates(); }
      else toast(r.message || '订阅失败', 'error');
    });

    renderSubs();
  }

  const btnAllSeen = document.getElementById('subAllSeenBtn');
  if (btnAllSeen) btnAllSeen.addEventListener('click', async () => {
    const r = await api('POST', '/api/sub_updates/seen', { all: true });
    if (r.success) { toast('已全部标为已读', 'success'); loadSubUpdates(); }
  });
  if (subManageBtn) subManageBtn.addEventListener('click', openSubManage);
  loadSubUpdates();
}

/* ═══════════════════════════════════════════════════════
   阅读页（书架 + 分类树）
   v2.13.0：双层分类 UI（块状级联导航 + 折叠管理面板）合并为单一分类树；
            分类归属改为单选；新增「未归类」收件箱视图；补重命名 / 删除入口。
   ═══════════════════════════════════════════════════════ */
let _currentCat = null; // null = 全部；>=0 = 真实分类 id
let _viewMode = 'all';   // 'all' | 'favorite' | 'uncategorized'
const _openNodes = new Set(); // 展开的树节点 id 集合（默认全部展开）
const VIRTUAL_ALL = 'all';
const VIRTUAL_FAV = '__fav__';
const VIRTUAL_UNCAT = '__uncat__';
function initLibrary() {
  const grid = document.getElementById('comicGrid');
  loadAppConfig().then(() => applyGridColumns('library_columns')).catch(() => {});
  const treeEl = document.getElementById('catTree');
  const nameEl = document.getElementById('currentCatName');
  const countEl = document.getElementById('currentCatCount');
  const empty = document.getElementById('emptyState');
  let _dragData = null; // {type:'comic'|'category', jmId|catId}
  let _treeAllOpen = true; // 分类树"全部展开/折叠"状态
  let _rawTree = [];   // 分类树原始数据（/api/categories?tree=1）
  let _uncatCount = 0; // 「未归类」本数（loadTree 时顺便统计）

  function clearDropTargets() {
    treeEl.querySelectorAll('.drop-target').forEach(x => x.classList.remove('drop-target'));
  }

  function setActiveView(which) {
    _viewMode = which;
    if (which !== 'all') {
      treeEl.querySelectorAll('.tree-item').forEach(x => x.classList.remove('active'));
    }
  }

  // 收藏数徽标（挂在分类树「我的收藏」行上）
  async function refreshQuickCounts() {
    const r = await api('GET', '/api/downloaded');
    const list = r.data || [];
    // 请求返回时 loadTree() 可能已重建过 DOM，故此处重新查询
    const badge = document.getElementById('favCountBadge');
    if (badge) badge.textContent = list.filter(c => c.favorite).length;
    return list;
  }

  // 拖拽预判：不能把分类拖到自身或自己的子孙下（后端也会拒，这里提前给视觉反馈）
  function _collectSubtreeIds(tree, id) {
    const res = [];
    (function walk(nodes) {
      for (const n of nodes || []) {
        if (n.id === id) { (function collect(x) { (x.children || []).forEach(ch => { res.push(ch.id); collect(ch); }); })(n); return; }
        walk(n.children || []);
      }
    })(tree);
    return res;
  }
  function _isDescendant(tree, catId, maybeDescendant) {
    return catId !== maybeDescendant && _collectSubtreeIds(tree, catId).includes(maybeDescendant);
  }

  function specialRow(key, icon, label, extra = '') {
    const active =
      (key === VIRTUAL_ALL && _viewMode === 'all' && _currentCat == null) ||
      (key === VIRTUAL_FAV && _viewMode === 'favorite') ||
      (key === VIRTUAL_UNCAT && _viewMode === 'uncategorized');
    return `<div class="tree-item ${active ? 'active' : ''}" data-virtual="${key}">
        <i class="fas ${icon} tree-item__icon"></i>
        <span class="tree-item__name">${label}</span>
        ${extra}
      </div>`;
  }

  async function loadTree() {
    const [r, uncatR] = await Promise.all([
      api('GET', '/api/categories?tree=1'),
      api('GET', '/api/downloaded?uncategorized=1'),
    ]);
    const tree = r.data || [];
    _rawTree = tree;
    _uncatCount = ((uncatR && uncatR.data) || []).length;
    let totalComics = 0;
    tree.forEach(countSub);
    function countSub(n) { totalComics += (n.count || n.comic_count || 0); (n.children || []).forEach(countSub); }
    // 收集/清空所有节点 id，用于"全部展开/折叠"
    function collectIds(nodes) { nodes.forEach(n => { _openNodes.add(n.id); (n.children || []).length && collectIds(n.children); }); }
    function clearIds(nodes) { nodes.forEach(n => { _openNodes.delete(n.id); (n.children || []).length && clearIds(n.children); }); }
    // 「折叠全部 / 展开全部」一键按钮（放在每个分类 id 集合之上，避免重复绑定）
    const toggleAllBtn = document.getElementById('treeToggleAll');
    if (toggleAllBtn && toggleAllBtn.dataset.bound !== '1') {
      toggleAllBtn.dataset.bound = '1';
      const syncBtn = () => {
        toggleAllBtn.innerHTML = _treeAllOpen
          ? '<i class="fas fa-folder-minus"></i>'
          : '<i class="fas fa-folder-plus"></i>';
        toggleAllBtn.title = _treeAllOpen ? '折叠全部子分类' : '展开全部子分类';
      };
      toggleAllBtn.addEventListener('click', async () => {
        _treeAllOpen = !_treeAllOpen;
        syncBtn();
        await loadTree();
      });
      syncBtn();
    }
    if (_treeAllOpen) collectIds(tree); else clearIds(tree);

    // 虚拟行（全部 / 收藏 / 未归类）+ 真实分类树，合并在同一棵树里
    let html = `<div class="tree-group"><i class="fas fa-th-large"></i>视图</div>`;
    html += specialRow(VIRTUAL_ALL, 'fa-layer-group', '全部漫画',
      `<span class="tree-item__count">${totalComics}</span>`);
    html += specialRow(VIRTUAL_FAV, 'fa-star', '我的收藏',
      `<span class="tree-item__badge" id="favCountBadge"></span>`);
    html += specialRow(VIRTUAL_UNCAT, 'fa-inbox', '未归类',
      `<span class="tree-item__count">${_uncatCount}</span>`);
    html += `<div class="tree-sep"></div>`;
    html += `<div class="tree-group"><i class="fas fa-folder"></i>我的分类</div>`;
    html += renderTree(tree, 0);

    treeEl.innerHTML = html;

    // 虚拟行：切换视图
    treeEl.querySelectorAll('.tree-item[data-virtual]').forEach(it => {
      it.onclick = () => {
        const key = it.dataset.virtual;
        if (key === VIRTUAL_ALL) { _viewMode = 'all'; _currentCat = null; }
        else if (key === VIRTUAL_FAV) { _viewMode = 'favorite'; _currentCat = null; }
        else { _viewMode = 'uncategorized'; _currentCat = null; }
        treeEl.querySelectorAll('.tree-item').forEach(x => x.classList.remove('active'));
        it.classList.add('active');
        loadComics();
      };
    });

    // 「未归类」行可作为拖放目标：把漫画拖进来 = 取消它现有的全部分类
    const uncatRow = treeEl.querySelector(`.tree-item[data-virtual="${VIRTUAL_UNCAT}"]`);
    if (uncatRow) {
      uncatRow.addEventListener('dragover', (e) => {
        if (!_dragData || _dragData.type !== 'comic') return;
        e.preventDefault();
        uncatRow.classList.add('drop-target');
      });
      uncatRow.addEventListener('dragleave', () => uncatRow.classList.remove('drop-target'));
      uncatRow.addEventListener('drop', async (e) => {
        e.preventDefault();
        uncatRow.classList.remove('drop-target');
        if (!_dragData || _dragData.type !== 'comic') return;
        const jmId = _dragData.jmId;
        _dragData = null;
        await moveComicToCategory(jmId, null);
      });
    }

    treeEl.querySelectorAll('.tree-item[data-cat]').forEach(it => {
      const catId = it.dataset.cat;
      it.onclick = (e) => {
        if (e.target.closest('.tree-item__actbtn')) return; // 点增删改按钮不触发选择
        if (e.target.closest('[data-toggle]')) return;      // 点折叠箭头不触发选择
        setActiveView('all');
        _currentCat = parseInt(catId);
        treeEl.querySelectorAll('.tree-item').forEach(x => x.classList.remove('active'));
        it.classList.add('active');
        loadComics();
      };
      // 分类可拖动（用于调整层级）
      it.setAttribute('draggable', 'true');
      it.addEventListener('dragstart', (e) => {
        _dragData = { type: 'category', catId: parseInt(catId) };
        it.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', String(catId)); } catch (_) {}
      });
      it.addEventListener('dragend', () => { _dragData = null; it.classList.remove('dragging'); clearDropTargets(); });
      // 作为放置目标：接受漫画（移动分类）或分类（调整父级）
      it.addEventListener('dragover', (e) => {
        if (!_dragData) return;
        const target = parseInt(catId);
        if (_dragData.type === 'category') {
          // v2.13.0 预判：拖到自身 / 自己的子孙 → 直接不给放，并标红
          if (_dragData.catId === target) return;
          if (_isDescendant(_rawTree, _dragData.catId, target)) {
            it.classList.add('drop-invalid');
            return;
          }
        }
        e.preventDefault();
        it.classList.add('drop-target');
      });
      it.addEventListener('dragleave', () => {
        it.classList.remove('drop-target');
        it.classList.remove('drop-invalid');
      });
      it.addEventListener('drop', async (e) => {
        e.preventDefault();
        it.classList.remove('drop-target');
        it.classList.remove('drop-invalid');
        if (!_dragData) return;
        const target = parseInt(catId);
        if (_dragData.type === 'comic') {
          await moveComicToCategory(_dragData.jmId, target);
        } else if (_dragData.type === 'category' && _dragData.catId !== target) {
          if (_isDescendant(_rawTree, _dragData.catId, target)) {
            toast('不能移动到其子分类下', 'warn');
            _dragData = null;
            return;
          }
          await moveCategoryToParent(_dragData.catId, target);
        }
        _dragData = null;
      });
    });
    // v2.13.0：分类重命名 / 删除按钮（替换原「移动到其他分类」按钮）
    treeEl.querySelectorAll('.tree-item__actbtn').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const cid = parseInt(btn.dataset.cat);
        if (btn.dataset.act === 'rename') await renameCategory(cid, tree);
        else await deleteCategoryFlow(cid, tree);
      });
    });
    // 展开/折叠切换
    treeEl.querySelectorAll('.tree-item__toggle[data-toggle]').forEach(toggle => {
      toggle.addEventListener('click', (e) => {
        e.stopPropagation();
        const id = parseInt(toggle.dataset.toggle);
        const childrenDiv = treeEl.querySelector(`.tree-children[data-parent="${id}"]`);
        if (!childrenDiv) return;
        if (_openNodes.has(id)) {
          _openNodes.delete(id);
          toggle.classList.remove('open', 'fa-chevron-down');
          toggle.classList.add('fa-chevron-right');
          childrenDiv.style.display = 'none';
        } else {
          _openNodes.add(id);
          toggle.classList.remove('fa-chevron-right');
          toggle.classList.add('open', 'fa-chevron-down');
          childrenDiv.style.display = '';
        }
      });
    });
    // 收藏数徽标随 DOM 重建，重新取一次
    refreshQuickCounts();
  }

  /* ── 分类重命名 ── */
  async function renameCategory(catId, tree) {
    const node = _findCatNode(tree, catId);
    if (!node) return;
    const name = prompt('重命名分类：', node.name);
    if (name == null) return;
    const trimmed = String(name).trim();
    if (!trimmed || trimmed === node.name) return;
    const r = await api('PUT', `/api/categories/${catId}`, { name: trimmed });
    if (r.success) { toast('已重命名', 'success'); loadTree(); loadComics(); }
    else toast(r.message || '重命名失败', 'error');
  }

  /* ── 分类删除：非空时必须选择漫画去向（对齐 Android deleteCategoryWithReassign）── */
  async function deleteCategoryFlow(catId, tree) {
    const node = _findCatNode(tree, catId);
    if (!node) return;
    const cnt = await api('GET', `/api/categories/${catId}?count_only=1`);
    const n = (cnt && cnt.count) || 0;

    if (n === 0) {
      if (!confirm(`确定删除分类「${node.name}」？该分类下没有漫画。`)) return;
      const r = await api('DELETE', `/api/categories/${catId}`, { reassign_to: null });
      if (r.success) {
        toast('已删除分类', 'success');
        if (_currentCat === catId) { _currentCat = null; _viewMode = 'all'; }
        loadTree(); loadComics();
      } else toast(r.message || '删除失败', 'error');
      return;
    }

    // 非空：列出可选去向（排除自身与子孙），必须选一个
    const forbidden = new Set([catId, ..._collectSubtreeIds(tree, catId)]);
    const flat = [];
    (function walk(ns, depth) {
      ns.forEach(x => {
        if (!forbidden.has(x.id)) flat.push({ id: x.id, name: x.name, depth });
        walk(x.children || [], depth + 1);
      });
    })(tree, 0);

    const bodyHtml = `<div style="font-size:13px;color:var(--d-text-2);margin-bottom:10px">
        该分类下有 <b>${n}</b> 部漫画，请选择将它们移动到哪里：
      </div>
      <div class="tree">${flat.map(x => `
        <div class="tree-item" data-pick="${x.id}" style="padding-left:${10 + x.depth * 14}px">
          <i class="fas fa-folder tree-item__icon"></i>
          <span class="tree-item__name">${escapeHtml(x.name)}</span>
        </div>`).join('')}
        <div class="tree-item" data-pick="">
          <i class="fas fa-inbox tree-item__icon"></i>
          <span class="tree-item__name">未归类</span>
        </div>
      </div>`;
    const { close } = openModal({
      title: `删除「${escapeHtml(node.name)}」`,
      sub: '删除前请先安排漫画去向，不会丢失任何漫画',
      bodyHtml,
      actions: [{ key: 'cancel', label: '取消', cls: 'btn--ghost', onClick: c => c() }],
    });
    document.querySelectorAll('[data-pick]').forEach(el => {
      el.onclick = async () => {
        const raw = el.dataset.pick;
        const target = raw === '' ? null : parseInt(raw);
        close();
        const r = await api('DELETE', `/api/categories/${catId}`, { reassign_to: target });
        if (r.success) {
          toast(target == null ? `已删除，${n} 部漫画移入未归类` : `已删除，${n} 部漫画已移动`, 'success');
          if (_currentCat === catId) { _currentCat = null; _viewMode = 'all'; }
          loadTree(); loadComics();
        } else toast(r.message || '删除失败', 'error');
      };
    });
  }

  function _findCatNode(tree, id) {
    let found = null;
    (function walk(ns) {
      for (const n of ns || []) {
        if (n.id === id) { found = n; return; }
        walk(n.children || []);
        if (found) return;
      }
    })(tree);
    return found;
  }

  function renderTree(nodes, depth) {
    return nodes.map(n => {
      const hasChildren = n.children && n.children.length;
      const count = n.count || n.comic_count || 0;
      const isOpen = _openNodes.has(n.id);
      const toggleHtml = hasChildren
        ? `<i class="fas ${isOpen ? 'fa-chevron-down' : 'fa-chevron-right'} tree-item__toggle ${isOpen ? 'open' : ''}" data-toggle="${n.id}" title="${isOpen ? '折叠' : '展开'}子文件夹"></i>`
        : `<span class="tree-item__toggle"></span>`;
      return `<div class="tree-item ${_currentCat === n.id ? 'active' : ''}" data-cat="${n.id}" style="padding-left:${6 + depth * 16}px">
        ${toggleHtml}
        <i class="fas fa-folder tree-item__icon"></i>
        <span class="tree-item__name">${escapeHtml(n.name)}</span>
        <span class="tree-item__count">${count}</span>
        <span class="tree-item__acts">
          <button class="tree-item__actbtn" data-cat="${n.id}" data-act="rename" title="重命名"><i class="fas fa-pen"></i></button>
          <button class="tree-item__actbtn tree-item__actbtn--danger" data-cat="${n.id}" data-act="del" title="删除分类"><i class="fas fa-trash-can"></i></button>
        </span>
      </div>` + (hasChildren ? `<div class="tree-children" data-parent="${n.id}" style="${isOpen ? '' : 'display:none'}">${renderTree(n.children, depth + 1)}</div>` : '');
    }).join('');
  }

  async function moveComicToCategory(jmId, catId) {
    const res = await api('PUT', `/api/comic/${jmId}/categories`, { category_ids: catId == null ? [] : [catId] });
    if (res.success) {
      const t = catId == null ? '已移入「未归类」' : '已移动到该分类';
      toast(t, 'success');
      loadTree(); loadComics();
    } else toast(res.message || '移动失败', 'error');
  }

  async function loadComics() {
    // 未归类 / 具体分类由后端筛选；收藏、读完为前端过滤
    let url = '/api/downloaded';
    if (_viewMode === 'uncategorized') url = '/api/downloaded?uncategorized=1';
    else if (_currentCat != null) url = `/api/downloaded?category_id=${_currentCat}`;
    const r = await api('GET', url);
    let list = r.data || [];
    if (_viewMode === 'favorite') list = list.filter(c => c.favorite);
    else if (_viewMode === 'finished') list = list.filter(c => c.finished);
    if (_viewMode === 'favorite') nameEl.textContent = '我的收藏';
    else if (_viewMode === 'finished') nameEl.textContent = '读完作品';
    else if (_viewMode === 'uncategorized') nameEl.textContent = '未归类';
    else {
      const pn = _findPathNodes();
      nameEl.textContent = _currentCat == null ? '全部漫画' : (pn.length ? pn[pn.length - 1].name : '分类');
    }
    countEl.textContent = `${list.length} 本`;
    if (!list.length) {
      grid.innerHTML = '';
      empty.style.display = 'block';
      if (_viewMode === 'favorite') {
        empty.querySelector('h3').textContent = '还没有收藏任何漫画';
        const iconEl = empty.querySelector('.empty__icon');
        if (iconEl) iconEl.innerHTML = '<i class="fas fa-star"></i>';
      } else if (_viewMode === 'finished') {
        empty.querySelector('h3').textContent = '还没有读完的作品';
        const iconEl = empty.querySelector('.empty__icon');
        if (iconEl) iconEl.innerHTML = '<i class="fas fa-circle-check"></i>';
      } else if (_viewMode === 'uncategorized') {
        empty.querySelector('h3').textContent = '没有未归类的漫画';
        const iconEl = empty.querySelector('.empty__icon');
        if (iconEl) iconEl.innerHTML = '<i class="fas fa-inbox"></i>';
      } else {
        empty.querySelector('h3').textContent = '这个分类还是空的';
        const iconEl = empty.querySelector('.empty__icon');
        if (iconEl) iconEl.innerHTML = '<i class="fas fa-folder-open"></i>';
      }
      attachRipple(empty);
      return;
    }
    empty.style.display = 'none';
    grid.innerHTML = '';
    list.forEach((c, i) => {
      const id = c.id;
      const blocked = !!c.blocked;
      const card = document.createElement('div');
      card.className = 'comic-card' + (blocked ? ' comic-card--blocked' : '');
      card.style.animationDelay = (Math.min(i, 15) * 0.03) + 's';
      card.setAttribute('draggable', 'true');
      card.addEventListener('dragstart', (e) => {
        _dragData = { type: 'comic', jmId: id };
        card.classList.add('dragging');
        e.dataTransfer.effectAllowed = 'move';
        try { e.dataTransfer.setData('text/plain', String(id)); } catch (_) {}
      });
      card.addEventListener('dragend', () => { _dragData = null; card.classList.remove('dragging'); });
      const cats = (c.categories || []).map(x => `<span class="chip chip--accent" style="margin:2px">${escapeHtml(x.name)}</span>`).join('');
      card.innerHTML = `
        ${blocked ? `<div class="blocked-badge"><i class="fas fa-ban"></i> 已拉黑</div>
        <button class="comic-card__fab unblock-fab" data-act="unblock" title="取消拉黑"><i class="fas fa-rotate-left"></i></button>` : ''}
        <button class="comic-card__fab" title="移动到分类（也可直接拖拽）" data-act="move"><i class="fas fa-folder-plus"></i></button>
        <img class="comic-card__cover" src="${c.cover_path ? '/api/cover/downloaded/' + id : "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='3' height='4'%3E%3C/svg%3E"}" onclick="location.href='/reader/${id}'">
        <div class="comic-card__body">
          <div class="comic-card__title">${escapeHtml(c.title || '未命名漫画')}</div>
          ${renderPageChapter(c.pages, c.chapter_count)}
          <div style="margin:6px 0">${cats}</div>
          <div class="comic-card__actions">
            <button class="btn" data-act="fav" title="收藏"><i class="fas fa-star"></i></button>
            <button class="btn" data-act="fin" title="读完"><i class="fas fa-check"></i></button>
            <button class="btn btn--primary" data-act="read">阅读</button>
            <button class="btn" data-act="del"><i class="fas fa-trash"></i></button>
          </div>
        </div>`;
      // 点卡片空白 → 详情页（封面仍是"直接阅读"的快捷路径，按钮各自处理）
      card.style.cursor = 'pointer';
      card.onclick = (e) => {
        if (e.target.closest('[data-act], .comic-card__cover, .comic-card__actions, .chip')) return;
        location.href = '/detail/' + id;
      };
      card.querySelector('[data-act="read"]').onclick = () => location.href = `/reader/${id}`;
      if (c.favorite) card.querySelector('[data-act="fav"]').classList.add('on');
      if (c.finished) card.querySelector('[data-act="fin"]').classList.add('on');
      card.querySelector('[data-act="fav"]').onclick = async (e) => {
        e.stopPropagation();
        const r = await api('POST', `/api/status/${id}/favorite`);
        if (r.success) {
          e.currentTarget.classList.toggle('on', !!r.favorite);
          toast(r.favorite ? '已收藏' : '已取消收藏', 'success');
          // 在「我的收藏」视图里取消收藏 → 卡片立即消失
          if (!r.favorite && _viewMode === 'favorite') {
            card.style.transition = 'opacity 180ms, transform 180ms';
            card.style.opacity = '0';
            card.style.transform = 'scale(0.96)';
            setTimeout(() => { card.remove(); _afterListChanged(); }, 180);
          } else {
            refreshQuickCounts(); syncEmptyAndCount();
          }
        } else toast(r.message || '操作失败', 'error');
      };
      card.querySelector('[data-act="fin"]').onclick = async (e) => {
        e.stopPropagation();
        const r = await api('POST', `/api/status/${id}/finished`);
        if (r.success) {
          e.currentTarget.classList.toggle('on', !!r.finished);
          toast(r.finished ? '已标记读完' : '已取消读完标记', 'success');
          if (!r.finished && _viewMode === 'finished') {
            card.style.transition = 'opacity 180ms, transform 180ms';
            card.style.opacity = '0';
            card.style.transform = 'scale(0.96)';
            setTimeout(() => { card.remove(); _afterListChanged(); }, 180);
          } else {
            refreshQuickCounts(); syncEmptyAndCount();
          }
        } else toast(r.message || '操作失败', 'error');
      };
      card.querySelector('[data-act="del"]').onclick = async (e) => {
        e.stopPropagation();
        if (!confirm('移入回收站：立即删除本地图片，记录与封面保留 7 天，期间可重新下载拾回。继续？')) return;
        const d = await api('DELETE', `/api/delete/${id}`);
        if (d.success) { toast('已移入回收站', 'success'); card.remove(); _afterListChanged(); loadTree(); }
        else toast(d.message || '移入回收站失败', 'error');
      };
      card.querySelector('[data-act="move"]').onclick = async (e) => {
        e.stopPropagation();
        const cur = (c.categories || []).map(x => x.id);
        pickCategories(cur, async (cats) => {
          const res = await api('PUT', `/api/comic/${id}/categories`, { category_ids: cats });
          if (res.success) { toast('已更新分类', 'success'); loadComics(); loadTree(); }
        });
      };
      if (blocked) {
        const ub = card.querySelector('[data-act="unblock"]');
        if (ub) ub.addEventListener('click', async (e) => {
          e.stopPropagation();
          await unblockComic(id, c.author);
          loadComics();
        });
      }
      grid.appendChild(card);
    });
    attachRipple(grid);
  }

  async function moveCategoryToParent(catId, parentId) {
    const res = await api('POST', `/api/categories/${catId}/move`, { parent_id: parentId });
    if (res.success) { toast('已移动分类', 'success'); loadTree(); loadComics(); }
    else toast(res.message || '移动失败', 'error');
  }

  function syncEmptyAndCount() {
    const cards = grid.querySelectorAll('.comic-card');
    countEl.textContent = `${cards.length} 本`;
    if (cards.length === 0) {
      // 触发一次 loadComics 会自动按当前视图渲染正确空态
      loadComics();
    } else {
      empty.style.display = 'none';
    }
  }
  function _afterListChanged() {
    refreshQuickCounts(); syncEmptyAndCount();
  }

  document.getElementById('newCatBtn').onclick = async () => {
    const name = prompt('新建分类名称：');
    if (!name) return;
    const r = await api('POST', '/api/categories', { name });
    if (r.success) { toast('已创建', 'success'); loadTree(); }
  };
  document.getElementById('newSubBtn').onclick = async () => {
    if (_currentCat == null) { toast('请先选择一个父分类', 'warn'); return; }
    const name = prompt('新建子分类名称：');
    if (!name) return;
    const r = await api('POST', '/api/categories', { name, parent_id: _currentCat });
    if (r.success) { toast('已创建', 'success'); loadTree(); }
  };

  setActiveView('all');
  loadTree();
  loadComics();
}

async function reDownloadFromHistory(jmId) {
  try {
    const r = await api('GET', `/api/comic/${jmId}/categories`);
    const cats = (r.success && r.data) ? r.data : [];
    downloadComic(jmId, cats);
    toast('已加入下载队列（原分类）', 'success');
  } catch (_) {
    downloadComic(jmId, []);
  }
}

/* ═══════════════════════════════════════════════════════
   设置页
   ═══════════════════════════════════════════════════════ */
function initSettings() {
  // 分类钻取导航：点分类块 → 展开对应详情面板；返回回到上一层（主页或父级面板）
  // 注：v2.7 起子页走真实路由 /settings/<sub>，这段兼容逻辑仅为未迁移的老 drill-panel 兜底（无相关 DOM 时静默不报错）
  const settingsHome = document.getElementById('settingsHome');
  const settingsDetail = document.getElementById('settingsDetail');
  const drillStack = []; // 记录父级面板 cat，支持屏蔽管理 → 搜索设置 这样的二级返回
  function showDrillPanel(cat) {
    if (!settingsDetail) return;
    settingsDetail.querySelectorAll('.drill-panel').forEach(p => { p.hidden = (p.dataset.cat !== cat); });
    if (settingsHome) settingsHome.hidden = true;
    settingsDetail.hidden = false;
    window.scrollTo({ top: 0 });
    // 重放进入动画，营造“跳到独立隐藏子页”的感觉（与下载页一致）
    settingsDetail.classList.remove('page-enter');
    void settingsDetail.offsetWidth;
    settingsDetail.classList.add('page-enter');
    if (cat === 'block') loadBlocklist();
  }
  function openDrill(cat, parentCat = null) {
    if (parentCat) drillStack.push(parentCat);
    else drillStack.length = 0; // 从设置主页进入新分类，清空栈
    showDrillPanel(cat);
  }
  if (settingsHome && settingsDetail) {
    settingsHome.querySelectorAll('.cat-card').forEach(card => {
      card.addEventListener('click', () => openDrill(card.dataset.cat));
    });
    const drillBack = document.getElementById('drillBack');
    if (drillBack) drillBack.addEventListener('click', () => {
      if (drillStack.length) {
        const prev = drillStack.pop();
        showDrillPanel(prev);
      } else {
        settingsDetail.hidden = true;
        settingsHome.hidden = false;
        window.scrollTo({ top: 0 });
      }
    });
    const blockPageBtn = document.getElementById('blockPageBtn');
    if (blockPageBtn) blockPageBtn.addEventListener('click', () => openDrill('block', 'search'));
  }

  // 搜索偏好库（历史 / 标签 / 作者 统一管理，误删可恢复）—— 现在内嵌在「搜索设置」分类里
  initPrefLib();

  // 设置项加载
  api('GET', '/api/settings').then(r => {
    if (!r.success) return; const s = r.data;
    appConfigCache = s;

    // P1-下载（相关 DOM 仅在 /settings/download 存在，其余子页/主页跳过不赋值）
    const maxConcurrentEl = document.getElementById('maxConcurrent');
    const maxConcurrentValEl = document.getElementById('maxConcurrentVal');
    if (s.max_concurrent_downloads && maxConcurrentEl) maxConcurrentEl.value = s.max_concurrent_downloads;
    if (maxConcurrentEl && maxConcurrentValEl) {
      maxConcurrentValEl.textContent = maxConcurrentEl.value;
      maxConcurrentEl.addEventListener('input', () => { maxConcurrentValEl.textContent = maxConcurrentEl.value; });
    }
    const iqEl = document.getElementById('imageQuality');
    if (iqEl && s.image_quality) iqEl.value = s.image_quality;
    const peEl = document.getElementById('proxyEnabled');
    if (peEl) peEl.checked = s.proxy_enabled === '1' || s.proxy_enabled === true || s.proxy_enabled === 'true';
    const puEl = document.getElementById('proxyUrl');
    if (puEl && s.proxy_url) puEl.value = s.proxy_url;
    const cacheSizeEl = document.getElementById('cacheSize');
    const clearCacheBtn = document.getElementById('clearCacheBtn');
    if (cacheSizeEl) api('GET', '/api/cache/status').then(rr => { if (rr.success) cacheSizeEl.textContent = `${rr.data.cache_size_mb} MB`; });
    if (clearCacheBtn) clearCacheBtn.onclick = async () => {
      const rr = await api('POST', '/api/cache/clear');
      if (rr.success) {
        toast(`已清理 ${rr.data.cleared_size_mb} MB`, 'success');
        const rem = (cacheSizeEl && cacheSizeEl.textContent.replace(/[^0-9.]/g, '')) || '0';
        cacheSizeEl.textContent = `${rr.data.remaining_size_mb || Number(rem || 0).toFixed(2)} MB`;
      }
    };

    // P1-外观（主题 + 夜间）
    const themeModeEl = document.getElementById('themeMode');
    const themeNightStartEl = document.getElementById('themeNightStart');
    const themeNightEndEl = document.getElementById('themeNightEnd');
    const themeAutoRow = document.querySelector('.theme-auto-row');
    if (themeModeEl) {
      const pref = String(s.theme || 'system').toLowerCase();
      themeModeEl.value = ['system','light','dark','auto'].includes(pref) ? pref : 'system';
      if (themeAutoRow) themeAutoRow.style.display = (pref === 'auto') ? '' : 'none';
      themeModeEl.addEventListener('change', () => {
        if (themeAutoRow) themeAutoRow.style.display = (themeModeEl.value === 'auto') ? '' : 'none';
        Theme.setPreferred(themeModeEl.value, { start: themeNightStartEl.value || '22:00', end: themeNightEndEl.value || '06:00' });
        Theme.savePreferred();
        toast('主题已保存', 'success');
      });
    }
    if (themeNightStartEl) {
      themeNightStartEl.value = s.theme_night_start || '22:00';
      themeNightStartEl.addEventListener('change', () => {
        Theme.setPreferred(themeModeEl ? themeModeEl.value : 'auto', { start: themeNightStartEl.value || '22:00', end: (themeNightEndEl && themeNightEndEl.value) || '06:00' });
        Theme.savePreferred();
      });
    }
    if (themeNightEndEl) {
      themeNightEndEl.value = s.theme_night_end || '06:00';
      themeNightEndEl.addEventListener('change', () => {
        Theme.setPreferred(themeModeEl ? themeModeEl.value : 'auto', { start: (themeNightStartEl && themeNightStartEl.value) || '22:00', end: themeNightEndEl.value || '06:00' });
        Theme.savePreferred();
      });
    }
    // 同步后端主题配置到 Theme（全局 applyGlobalSettings 也会做，这里保证子页里显示对）
    const pref = String(s.theme || 'system').toLowerCase();
    Theme.setPreferred(pref, { start: s.theme_night_start || Theme._nightStart, end: s.theme_night_end || Theme._nightEnd });

    // P1-搜索
    const limit = parseInt(s.search_result_limit, 10);
    if (document.getElementById('searchResultLimit')) document.getElementById('searchResultLimit').value = (isNaN(limit) || limit < 10) ? 80 : limit;
    const tagLimitEl = document.getElementById('searchTagLimit');
    if (tagLimitEl) {
      const tl = parseInt(s.search_tag_limit, 10);
      tagLimitEl.value = (isNaN(tl) || tl < 0) ? 0 : tl;
    }
    const defaultSortEl = document.getElementById('defaultSort');
    if (defaultSortEl) defaultSortEl.value = (['mr','mv','mp'].includes(String(s.default_sort || '').toLowerCase())) ? s.default_sort : 'mr';
    const sbBeh = document.getElementById('searchBarBehavior');
    if (sbBeh) sbBeh.value = (['stay','hide'].includes(String(s.search_bar_behavior || '').toLowerCase())) ? s.search_bar_behavior : 'hide';
    const hd = document.getElementById('hideDownloadedInSearch');
    if (hd) hd.value = (['off','gray','hide'].includes(String(s.hide_downloaded_in_search || '').toLowerCase())) ? s.hide_downloaded_in_search : 'hide';
    const hf = document.getElementById('hideFinishedInSearch');
    if (hf) hf.value = (['off','gray','hide'].includes(String(s.hide_finished_in_search || '').toLowerCase())) ? s.hide_finished_in_search : 'hide';
    const sabEl = document.getElementById('searchApplyBlocks');
    if (sabEl) sabEl.checked = s.search_apply_blocks !== '0';

    // P2-标签库：兴趣库/实验库 即时计数
    const tCnt = document.getElementById('libTagCount');
    if (tCnt) tCnt.textContent = s.tag_count || 0;
    const tCnt2 = document.getElementById('libTagCount2');
    if (tCnt2) tCnt2.textContent = s.tag_count || 0;
    const eCnt = document.getElementById('libExpTagCount');
    if (eCnt) eCnt.textContent = s.exp_tag_count || 0;
    const eCnt2 = document.getElementById('libExpTagCount2');
    if (eCnt2) eCnt2.textContent = s.exp_tag_count || 0;

    // P2-智能关联配置绑定
    const sa = document.getElementById('smartAssociation');
    if (sa) sa.checked = s.smart_association !== '0';
    const minHits = parseInt(s.pref_min_hits, 10);
    const minHitsEl = document.getElementById('prefMinHits');
    if (minHitsEl) {
      const v = (isNaN(minHits) || minHits < 2) ? 4 : Math.min(20, minHits);
      minHitsEl.value = v;
      const label = document.getElementById('prefMinHitsVal');
      if (label) label.textContent = v;
    }
    const maxTags = parseInt(s.pref_max_tags, 10);
    const maxTagsEl = document.getElementById('prefMaxTags');
    if (maxTagsEl) {
      const v = (isNaN(maxTags) || maxTags < 3) ? 25 : Math.min(200, maxTags);
      maxTagsEl.value = v;
      const label = document.getElementById('prefMaxTagsVal');
      if (label) label.textContent = v;
    }
    const stratHn = String(s.search_strategy || 'hybrid');
    const stratHnEl = document.getElementById('searchStrategyHn');
    if (stratHnEl) stratHnEl.checked = (stratHn === 'hybrid');
    const stratHd = document.getElementById('searchStrategyHd');
    if (stratHd) stratHd.checked = (stratHn === 'hybrid_default');
    const recStrat = String(s.recommend_strategy || 'hybrid');
    const recStratEl = document.getElementById('recommendStrategy');
    if (recStratEl) recStratEl.checked = (recStrat !== 'weighted_only');
    const hideIrrEl = document.getElementById('hideIrrelevant');
    if (hideIrrEl) hideIrrEl.checked = s.hide_irrelevant !== '0';

    // 智能关联滑块的即时 label 更新
    if (minHitsEl) minHitsEl.addEventListener('input', () => {
      const label = document.getElementById('prefMinHitsVal'); if (label) label.textContent = minHitsEl.value;
    });
    if (maxTagsEl) maxTagsEl.addEventListener('input', () => {
      const label = document.getElementById('prefMaxTagsVal'); if (label) label.textContent = maxTagsEl.value;
    });

    // P2：初始化子页（仅该页有主容器时生效）
    initTagsPage();
    initTagsManagePage();

    const subsPollBtn = document.getElementById('subsTriggerPollBtn');
    const subsPollHint = document.getElementById('subsPollHint');
    if (subsPollBtn) subsPollBtn.onclick = async () => {
      subsPollBtn.disabled = true;
      try {
        const rr = await api('POST', '/api/subscriptions/poll');
        if (!rr.success) throw new Error(rr.message || '失败');
        subsPollHint.style.display = '';
        subsPollHint.textContent = `完成：检查 ${rr.checked || 0} 位作者，新增 ${rr.added_new || 0} 条新作（${rr.authors_with_new || 0} 位有更新）。回发现页可查看「订阅新作」。`;
      } catch (e) {
        subsPollHint.style.display = '';
        subsPollHint.textContent = `失败：${e.message || e}`;
      } finally {
        subsPollBtn.disabled = false;
      }
    };

    // P1-阅读
    const readerToastEl = document.getElementById('readerChapterToast');
    if (readerToastEl) readerToastEl.checked = s.reader_chapter_toast !== '0';
    const shelfSortEl = document.getElementById('shelfSort');
    if (shelfSortEl) {
      const validSorts = ['download_desc','download_asc','read_desc','read_asc','favorite_desc','favorite_asc'];
      shelfSortEl.value = validSorts.includes(String(s.shelf_sort || '')) ? s.shelf_sort : 'download_desc';
    }
    // v2.14：书架/搜索每行列数
    const shelfColsEl = document.getElementById('shelfColumns');
    if (shelfColsEl) shelfColsEl.value = String(parseInt(s.library_columns, 10) || 0);
    const searchColsEl = document.getElementById('searchColumns');
    if (searchColsEl) searchColsEl.value = String(parseInt(s.search_columns, 10) || 0);
  });

  // 设置分组可折叠
  document.querySelectorAll('.settings-group__head').forEach(h => {
    h.addEventListener('click', () => {
      const g = h.dataset.g;
      const body = document.getElementById(g + 'Body');
      if (!body) return;
      const now = body.classList.toggle('open');
      h.classList.toggle('collapsed', !now);
    });
  });

  // 即时保存：任一设置项变更即写入后端（不再需要「保存设置」按钮）
  async function saveSettings(showToast = true) {
    const payload = {};
    const srl = document.getElementById('searchResultLimit');
    if (srl) {
      let limit = parseInt(srl.value, 10);
      if (isNaN(limit) || limit < 10) limit = 80;
      if (limit > 500) limit = 500;
      srl.value = limit;
      payload.search_result_limit = String(limit);
    }
    const mc = document.getElementById('maxConcurrent');
    if (mc) payload.max_concurrent_downloads = mc.value;
    const iq = document.getElementById('imageQuality');
    if (iq) payload.image_quality = iq.value;
    const pe = document.getElementById('proxyEnabled');
    if (pe) payload.proxy_enabled = pe.checked ? '1' : '0';
    const pu = document.getElementById('proxyUrl');
    if (pu) payload.proxy_url = pu.value.trim();
    const stl = document.getElementById('searchTagLimit');
    if (stl) payload.search_tag_limit = String(parseInt(stl.value, 10) || 0);
    const ds = document.getElementById('defaultSort');
    if (ds) payload.default_sort = ds.value;
    const sbb = document.getElementById('searchBarBehavior');
    if (sbb) payload.search_bar_behavior = sbb.value;
    const hd = document.getElementById('hideDownloadedInSearch');
    if (hd) payload.hide_downloaded_in_search = hd.value;
    const hf = document.getElementById('hideFinishedInSearch');
    if (hf) payload.hide_finished_in_search = hf.value;
    const sab = document.getElementById('searchApplyBlocks');
    if (sab) payload.search_apply_blocks = sab.checked ? '1' : '0';
    const rct = document.getElementById('readerChapterToast');
    if (rct) payload.reader_chapter_toast = rct.checked ? '1' : '0';
    const ss = document.getElementById('shelfSort');
    if (ss) payload.shelf_sort = ss.value;
    // v2.14：书架/搜索每行列数（0=自动）
    const sc = document.getElementById('shelfColumns');
    if (sc) payload.library_columns = sc.value;
    const seCols = document.getElementById('searchColumns');
    if (seCols) payload.search_columns = seCols.value;
    setAppConfig('library_columns', sc ? sc.value : undefined);
    setAppConfig('search_columns', seCols ? seCols.value : undefined);
    payload.theme = Theme.getPreferred();
    payload.theme_night_start = Theme._nightStart;
    payload.theme_night_end = Theme._nightEnd;

    // P2-标签库/智能关联
    const sa = document.getElementById('smartAssociation');
    if (sa) payload.smart_association = sa.checked ? '1' : '0';
    const minHitsEl = document.getElementById('prefMinHits');
    if (minHitsEl) {
      let v = parseInt(minHitsEl.value, 10);
      if (isNaN(v) || v < 2) v = 4;
      if (v > 20) v = 20;
      minHitsEl.value = v;
      payload.pref_min_hits = String(v);
    }
    const maxTagsEl = document.getElementById('prefMaxTags');
    if (maxTagsEl) {
      let v = parseInt(maxTagsEl.value, 10);
      if (isNaN(v) || v < 3) v = 25;
      if (v > 200) v = 200;
      maxTagsEl.value = v;
      payload.pref_max_tags = String(v);
    }
    const hnStrat = document.getElementById('searchStrategyHn');
    if (hnStrat) payload.search_strategy = hnStrat.checked ? 'hybrid' : 'hybrid_default';
    const recStratEl = document.getElementById('recommendStrategy');
    if (recStratEl) payload.recommend_strategy = recStratEl.checked ? 'hybrid' : 'weighted_only';
    const hideIrr = document.getElementById('hideIrrelevant');
    if (hideIrr) payload.hide_irrelevant = hideIrr.checked ? '1' : '0';
    const activeLibEl = document.getElementById('activeTagLib');
    if (activeLibEl) payload.active_tag_lib = String(activeLibEl.value || 'interest');

    if (!Object.keys(payload).length) return;
    const r = await api('POST', '/api/settings', payload);
    if (r.success) {
      const allKeys = ['search_tag_limit','default_sort','search_bar_behavior','hide_downloaded_in_search','hide_finished_in_search','search_apply_blocks','reader_chapter_toast','shelf_sort','theme','theme_night_start','theme_night_end','smart_association','pref_min_hits','pref_max_tags','search_strategy','recommend_strategy','hide_irrelevant','active_tag_lib'];
      for (const k of allKeys) {
        if (payload[k] != null) setAppConfig(k, payload[k]);
      }
      if (showToast) toast('设置已保存', 'success');
    } else if (showToast) {
      toast('保存失败', 'error');
    }
  }
  // 滑块(拖动)只更新数字标签；松手(change)才保存，避免拖动过程狂写
  const mcEl = document.getElementById('maxConcurrent');
  if (mcEl) mcEl.addEventListener('input', () => { const v = mcEl.value; const vEl = document.getElementById('maxConcurrentVal'); if (vEl) vEl.textContent = v; });
  const bindSave = (id, evt) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(evt, () => saveSettings());
  };
  bindSave('maxConcurrent', 'change');
  bindSave('imageQuality', 'change');
  bindSave('proxyEnabled', 'change');
  bindSave('proxyUrl', 'change');
  bindSave('searchResultLimit', 'change');
  bindSave('searchTagLimit', 'change');
  bindSave('readerChapterToast', 'change');
  bindSave('defaultSort', 'change');
  bindSave('searchBarBehavior', 'change');
  bindSave('hideDownloadedInSearch', 'change');
  bindSave('hideFinishedInSearch', 'change');
  bindSave('searchApplyBlocks', 'change');
  bindSave('shelfSort', 'change');
  bindSave('shelfColumns', 'change');
  bindSave('searchColumns', 'change');
  bindSave('smartAssociation', 'change');

  // v2.14：收藏备份（导出/导入，格式跨端兼容手机端）
  const favExportBtn = document.getElementById('favExportBtn');
  if (favExportBtn) favExportBtn.onclick = async () => {
    try {
      favExportBtn.disabled = true;
      const r = await api('POST', '/api/favorites/export');
      if (r.success) toast(`已导出 ${r.data.count} 条收藏 → ${r.data.path}`, 'success', 5000);
      else toast(r.message || '导出失败', 'error');
    } catch (e) { toast('导出失败：' + e.message, 'error'); }
    finally { favExportBtn.disabled = false; }
  };
  const favImportBtn = document.getElementById('favImportBtn');
  const favImportFile = document.getElementById('favImportFile');
  if (favImportBtn && favImportFile) {
    favImportBtn.onclick = () => favImportFile.click();
    favImportFile.onchange = async () => {
      const f = favImportFile.files && favImportFile.files[0];
      if (!f) return;
      try {
        const payload = JSON.parse(await f.text());
        const r = await api('POST', '/api/favorites/import', payload);
        if (r.success) toast(`导入完成：共 ${r.data.total} 条，新增收藏 ${r.data.added} 条`, 'success');
        else toast(r.message || '导入失败', 'error');
      } catch (e) { toast('导入失败：文件不是有效 JSON', 'error'); }
      finally { favImportFile.value = ''; }
    };
  }
  bindSave('prefMinHits', 'change');
  bindSave('prefMaxTags', 'change');
  bindSave('searchStrategyHn', 'change');
  bindSave('searchStrategyHd', 'change');
  bindSave('recommendStrategy', 'change');
  bindSave('hideIrrelevant', 'change');
  bindSave('activeTagLib', 'change');

  // 关于页：加载版本号与数据占用统计（仅子页存在这些元素时执行）
  (function initAboutStats() {
    const vEl = document.getElementById('appVersion');
    const platfEl = document.getElementById('aboutPlatform');
    const userDataEl = document.getElementById('aboutUserData');
    const comicSizeEl = document.getElementById('aboutComicSize');
    const dbFileEl = document.getElementById('aboutDbFile');
    const comicsCntEl = document.getElementById('aboutComicsCount');
    const tagCntEl = document.getElementById('aboutTagCount');
    const expTagCntEl = document.getElementById('aboutExpTagCount');
    // 任一关于页统计元素存在 → 调接口
    const need = vEl || platfEl || userDataEl || comicSizeEl || dbFileEl || comicsCntEl || tagCntEl || expTagCntEl;
    if (!need) return;
    api('GET', '/api/about').then(r => {
      if (!r.success) return;
      const d = r.data || {};
      if (vEl) vEl.textContent = d.version || '—';
      if (platfEl) platfEl.textContent = d.platform || '—';
      if (userDataEl) userDataEl.textContent = d.user_data || '—';
      if (comicSizeEl) comicSizeEl.textContent = d.comic_size || '—';
      if (dbFileEl) dbFileEl.textContent = d.db_file || '—';
      if (comicsCntEl) comicsCntEl.textContent = `${d.comics_count || 0} 本`;
      if (tagCntEl) tagCntEl.textContent = d.tag_count || 0;
      if (expTagCntEl) expTagCntEl.textContent = d.exp_tag_count || 0;
    });
  })();

  // 关于页：检查更新（GitHub Releases，不经过自建服务器）
  (function initUpdateCheck() {
    const btn = document.getElementById('btnCheckUpdate');
    if (!btn) return;
    const out = document.getElementById('updateResult');
    btn.addEventListener('click', async () => {
      btn.disabled = true;
      const old = btn.textContent;
      btn.textContent = '检查中…';
      try {
        const r = await api('GET', '/api/update/check?force=1');
        if (!r || !r.success) {
          out.style.display = 'block';
          out.textContent = '检查失败：' + ((r && r.error) || '网络不通');
        } else {
          const d = r.data || {};
          out.style.display = 'block';
          if (!d.success) {
            out.textContent = '检查失败：' + (d.error || '未知错误');
          } else if (d.has_update) {
            const files = (d.assets || []).map(a => a.name).filter(Boolean).join('、');
            out.innerHTML = '发现新版本 <b>' + escapeHtml(d.latest) + '</b>（当前 ' + escapeHtml(d.current) + '）'
              + (files ? '<br>Release 附件：' + escapeHtml(files) : '')
              + '<br><a href="' + escapeHtml(d.url) + '" target="_blank" rel="noreferrer">打开下载页</a>';
          } else {
            out.textContent = '已是最新版本 ' + d.current;
          }
        }
      } catch (e) {
        out.style.display = 'block';
        out.textContent = '检查失败：' + (e && e.message ? e.message : e);
      } finally {
        btn.disabled = false;
        btn.textContent = old || '检查';
      }
    });
  })();

  /* ── 屏蔽管理 ── */
  const blockListEl = document.getElementById('blockList');
  function renderBlockList(rows, titleMap) {
    if (!blockListEl) return;
    if (!rows.length) { blockListEl.innerHTML = `<div class="manage-empty">还没有屏蔽项</div>`; return; }
    const typeLabels = { work: '作品', author: '作者', tag: '标签' };
    blockListEl.innerHTML = `<div class="block-chips">` + rows.map(b => {
      const isWork = b.block_type === 'work';
      const isTag = b.block_type === 'tag';
      const workTitle = isWork && titleMap ? titleMap[String(b.value)] : null;
      const val = isWork ? (workTitle ? escapeHtml(workTitle) : `JM-${escapeHtml(b.value)}`) : escapeHtml(b.value);
      const typeLabel = typeLabels[b.block_type] || b.block_type;
      return `<span class="block-chip" data-id="${b.id}" data-type="${b.block_type}" data-value="${escapeHtml(b.value)}" title="点击查看预览">
        <span class="block-chip__type">${typeLabel}</span>
        <span class="block-chip__name">${val}</span>
        <button class="block-chip__del" data-id="${b.id}" title="取消屏蔽"><i class="fas fa-times"></i></button>
      </span>`;
    }).join('') + `</div>`;
    blockListEl.querySelectorAll('.block-chip').forEach(chip => {
      chip.addEventListener('click', async (e) => {
        if (e.target.closest('.block-chip__del')) return;
        const type = chip.dataset.type;
        const value = chip.dataset.value;
        await previewBlockItem(type, value);
      });
    });
    blockListEl.querySelectorAll('.block-chip__del').forEach(b => {
      b.onclick = async () => {
        const r = await api('DELETE', `/api/blocklist/${b.dataset.id}`);
        if (r.success) loadBlocklist(); else toast('移除失败', 'error');
      };
    });
  }
  async function previewBlockItem(type, value) {
    openModal({
      title: type === 'work' ? '作品预览' : type === 'author' ? '作者作品预览' : '标签作品预览',
      sub: type === 'work' ? `JM-${value}` : `「${value}」`,
      bodyHtml: `<div id="blockPreviewLoading" style="text-align:center;padding:22px"><i class="fas fa-spinner fa-spin"></i> 加载中…</div><div id="blockPreviewGrid" class="comic-grid stagger"></div><div id="blockPreviewEmpty" class="empty" style="display:none"><div class="empty__icon"><i class="fas fa-search"></i></div><h3>暂无结果</h3></div>`,
      actions: [{ key: 'ok', label: '关闭', cls: 'btn--primary', onClick: c => c() }],
    });
    try {
      const r = await api('GET', `/api/blocklist/preview?type=${encodeURIComponent(type)}&value=${encodeURIComponent(value)}&limit=12`);
      const loading = document.getElementById('blockPreviewLoading');
      if (loading) loading.style.display = 'none';
      const grid = document.getElementById('blockPreviewGrid');
      const empty = document.getElementById('blockPreviewEmpty');
      if (!r.success || !r.data || !r.data.length) {
        if (grid) grid.style.display = 'none';
        if (empty) empty.style.display = 'block';
        return;
      }
      if (grid) {
        renderCards(r.data, false, grid);
        attachRipple(grid);
        setupLazyCovers(grid);
      }
    } catch (e) {
      const loading = document.getElementById('blockPreviewLoading');
      if (loading) loading.innerHTML = '<p>加载失败</p>';
    }
  }
  async function loadBlocklist() {
    const [br, dr] = await Promise.all([
      api('GET', '/api/blocklist'),
      api('GET', '/api/downloaded')
    ]);
    let titleMap = {};
    if (dr.success) (dr.data || []).forEach(c => { if (c.id != null) titleMap[String(c.id)] = c.title; });
    if (br.success) renderBlockList(br.data || [], titleMap);
  }
  const blockAddBtn = document.getElementById('blockAddBtn');
  if (blockAddBtn) blockAddBtn.onclick = async () => {
    const input = document.getElementById('blockInput');
    const type = document.getElementById('blockType').value;
    const v = input.value.trim();
    if (!v) { toast('请输入要屏蔽的内容', 'warn'); return; }
    const r = await api('POST', '/api/blocklist', { type, value: v });
    if (r.success) { toast('已拉黑', 'success'); input.value = ''; loadBlocklist(); }
    else toast(r.message || '操作失败', 'error');
  };
  loadBlocklist();

  /* ── 同义词 / 别名 ── */
  const aliasListEl = document.getElementById('aliasList');
  function renderAliasList(rows) {
    if (!aliasListEl) return;
    if (!rows.length) { aliasListEl.className = 'manage-list'; aliasListEl.innerHTML = `<div class="manage-empty">还没有同义词组</div>`; return; }
    aliasListEl.className = 'alias-grid';
    aliasListEl.innerHTML = rows.map(a => {
      const icon = a.type === 'author' ? 'fa-user-pen' : 'fa-tag';
      const typeLabel = a.type === 'author' ? '作者' : '标签';
      return `<span class="alias-block">
        <i class="fas ${icon}" style="color:var(--accent);width:16px;text-align:center;flex:0 0 auto"></i>
        <span class="alias-block__type">${typeLabel}</span>
        <span class="alias-block__name">${escapeHtml(a.canonical)} <span class="alias-block__eq">=</span> ${escapeHtml(a.alias)}</span>
        <button class="alias-block__del" data-id="${a.id}" title="移除"><i class="fas fa-times"></i></button>
      </span>`;
    }).join('');
    aliasListEl.querySelectorAll('.alias-block__del').forEach(b => {
      b.onclick = async () => {
        const r = await api('DELETE', `/api/aliases/${b.dataset.id}`);
        if (r.success) loadAliases(); else toast('移除失败', 'error');
      };
    });
  }
  async function loadAliases() {
    const r = await api('GET', '/api/aliases');
    if (r.success) renderAliasList(r.data || []);
  }
  const aliasAddBtn = document.getElementById('aliasAddBtn');
  if (aliasAddBtn) aliasAddBtn.onclick = async () => {
    const type = document.getElementById('aliasType').value;
    const canon = document.getElementById('aliasCanon').value.trim();
    const variants = document.getElementById('aliasVariants').value.trim();
    if (!canon || !variants) { toast('请填写标准词与同义词', 'warn'); return; }
    let ok = true;
    for (const alias of variants.split(/[,，]/).map(s => s.trim()).filter(Boolean)) {
      const r = await api('POST', '/api/aliases', { type, canonical: canon, alias });
      if (!r.success) { ok = false; toast(r.message || '添加失败', 'error'); }
    }
    if (ok) { toast('已添加', 'success'); document.getElementById('aliasCanon').value = ''; document.getElementById('aliasVariants').value = ''; loadAliases(); }
  };
  loadAliases();

  /* ── 自动推荐设置 ── */
  let recommendBasis = ['keyword', 'author', 'tag']; // 内存中的推荐依据集合
  let recommendCustom = []; // 内存中的自定义内容 [{type, value}]
  const basisChipEls = document.querySelectorAll('#recommendBasis .basis-chip');
  const recommendCountEl = document.getElementById('recommendCount');
  const recommendEnabledEl = document.getElementById('recommendEnabled');
  const recommendCustomBlocksEl = document.getElementById('recommendCustomBlocks');
  const recommendParamInfoEl = document.getElementById('recommendParamInfo');

  function parseJsonList(str, fallback) {
    if (!str) return fallback;
    try { const v = JSON.parse(str); return Array.isArray(v) ? v : fallback; } catch (_) { return fallback; }
  }
  function renderRecommendParamInfo() {
    if (!recommendParamInfoEl) return;
    const basisLabels = { keyword: '常搜索', author: '作者', tag: '标签' };
    const customTypeLabels = { name: '关键字', tag: '标签', author: '作者' };
    const basisTxt = recommendBasis.map(b => basisLabels[b] || b).join('、') || '无';
    const customTxt = recommendCustom.length
      ? recommendCustom.map(c => `${customTypeLabels[c.type] || c.type}:${c.value}`).join('，')
      : '无';
    const enabled = recommendEnabledEl ? recommendEnabledEl.checked : true;
    recommendParamInfoEl.textContent = `启用：${enabled ? '是' : '否'} · 数量：${recommendCountEl ? recommendCountEl.value : '?'} · 依据：${basisTxt} · 自定义：${customTxt}`;
  }
  function renderRecommendCustomBlocks() {
    if (!recommendCustomBlocksEl) return;
    recommendCustomBlocksEl.querySelectorAll('.rc-block').forEach(block => {
      const type = block.dataset.type;
      const listEl = block.querySelector('[data-list]');
      const emptyEl = block.querySelector('[data-empty]');
      const items = recommendCustom.map((c, globalIdx) => ({ ...c, globalIdx })).filter(c => c.type === type);
      if (!items.length) {
        listEl.innerHTML = '';
        emptyEl.hidden = false;
      } else {
        emptyEl.hidden = true;
        listEl.innerHTML = items.map(c =>
          `<span class="fav-tag">
            <span>${escapeHtml(c.value)}</span>
            <button class="ft-del" data-idx="${c.globalIdx}" title="删除"><i class="fas fa-times"></i></button>
          </span>`).join('');
        listEl.querySelectorAll('.ft-del').forEach(b => {
          b.onclick = () => {
            const idx = parseInt(b.dataset.idx, 10);
            if (idx >= 0 && idx < recommendCustom.length) {
              recommendCustom.splice(idx, 1);
              renderRecommendCustomBlocks(); renderRecommendParamInfo(); saveRecommendSettings(false);
            }
          };
        });
      }
    });
  }
  function bindRecommendCustomBlocks() {
    if (!recommendCustomBlocksEl) return;
    recommendCustomBlocksEl.querySelectorAll('.rc-block').forEach(block => {
      const type = block.dataset.type;
      const input = block.querySelector('[data-add-input]');
      const addBtn = block.querySelector('[data-add-btn]');
      const clearBtn = block.querySelector('[data-clear]');
      function add() {
        const val = input.value.trim();
        if (!val) { toast('请输入内容', 'warn'); return; }
        if (recommendCustom.some(c => c.type === type && c.value === val)) { toast('已添加过', 'warn'); return; }
        recommendCustom.push({ type, value: val });
        input.value = '';
        renderRecommendCustomBlocks(); renderRecommendParamInfo(); saveRecommendSettings(false);
      }
      addBtn.onclick = add;
      input.addEventListener('keydown', e => { if (e.key === 'Enter') add(); });
      clearBtn.onclick = () => {
        if (!recommendCustom.some(c => c.type === type)) return;
        recommendCustom = recommendCustom.filter(c => c.type !== type);
        renderRecommendCustomBlocks(); renderRecommendParamInfo(); saveRecommendSettings(false);
      };
    });
  }
  function renderRecommendBasis() {
    basisChipEls.forEach(el => { el.classList.toggle('on', recommendBasis.includes(el.dataset.basis)); });
  }
  async function saveRecommendSettings(showToast = true) {
    const payload = {
      recommend_enabled: (recommendEnabledEl && recommendEnabledEl.checked) ? '1' : '0',
      recommend_count: String(parseInt(recommendCountEl ? recommendCountEl.value : '20', 10) || 20),
      recommend_basis: recommendBasis,
      recommend_custom: recommendCustom,
    };
    const r = await api('POST', '/api/settings', payload);
    if (r.success) { if (showToast) toast('推荐设置已保存', 'success'); }
    else if (showToast) toast('保存失败', 'error');
    renderRecommendParamInfo();
  }
  // 初始默认值渲染
  renderRecommendBasis(); renderRecommendCustomBlocks(); bindRecommendCustomBlocks(); renderRecommendParamInfo();
  // 从后端读取已保存配置覆盖默认
  api('GET', '/api/settings').then(r => {
    if (!r.success) return; const s = r.data || {};
    if (recommendEnabledEl) recommendEnabledEl.checked = (s.recommend_enabled !== '0' && s.recommend_enabled !== false && s.recommend_enabled !== 'false');
    if (recommendCountEl) { const c = parseInt(s.recommend_count, 10); recommendCountEl.value = (isNaN(c) || c < 4) ? 20 : c; }
    const basis = parseJsonList(s.recommend_basis, null);
    recommendBasis = (basis && basis.length) ? basis.filter(b => ['keyword', 'author', 'tag'].includes(b)) : ['keyword', 'author', 'tag'];
    recommendCustom = parseJsonList(s.recommend_custom, []);
    renderRecommendBasis(); renderRecommendCustomBlocks(); renderRecommendParamInfo();
  });
  // 依据 chip 点击高亮/变暗
  basisChipEls.forEach(el => {
    el.addEventListener('click', () => {
      const b = el.dataset.basis;
      if (recommendBasis.includes(b)) recommendBasis = recommendBasis.filter(x => x !== b);
      else recommendBasis.push(b);
      el.classList.toggle('on');
      renderRecommendParamInfo();
      saveRecommendSettings(false);
    });
  });
  if (recommendEnabledEl) recommendEnabledEl.addEventListener('change', () => { renderRecommendParamInfo(); saveRecommendSettings(false); });
  if (recommendCountEl) recommendCountEl.addEventListener('change', () => { renderRecommendParamInfo(); saveRecommendSettings(false); });
}

/* ═══════════════════════════════════════════════════════
   阅读器（独立全屏页，由 reader.html 调用）
   ═══════════════════════════════════════════════════════ */
function initReader() {
  const jmId = parseInt(location.pathname.split('/').pop());
  const stage = document.getElementById('readerStage');
  const titleEl = document.getElementById('readerTitle');
  const zoomVal = document.getElementById('zoomVal');
  const chapSide = document.getElementById('chapSide');
  const chapTrigger = document.getElementById('chapTrigger');
  const chapList = document.getElementById('chapList');
  const chapInfo = document.getElementById('chapInfo');
  const prevChapBtn = document.getElementById('prevChapBtn');
  const nextChapBtn = document.getElementById('nextChapBtn');
  let pages = [], current = 0, zoom = 1, currentChapter = '1';
  let currentImg = null; // 当前页 <img>，滚轮缩放时复用（不重建，避免重新发请求）
  const MIN_ZOOM = 0.1, MAX_ZOOM = 6;
  // 滚轮行为：'scroll' = 上下连排（多页一列，滚轮滚动=翻页，v2.14 默认）
  //           'page' = 滑动翻页（页内先滑、到底/到顶翻页）；'zoom' = 滚轮缩放
  let wheelMode = Settings.get('reader_wheel_mode', 'scroll');
  let viewInitialized = false; // 首屏是否已按模式默认适配（之后翻页保留用户缩放）
  // ── v2.14 翻新增强：右侧滑轨 / 缩略图览 / 连排模式 ──
  const rail = document.getElementById('readerRail');
  const railFill = rail ? rail.querySelector('i') : null;
  const grid = document.getElementById('readerGrid');
  const overviewBtn = document.getElementById('overviewBtn');
  let overview = false;        // 缩略图览模式开关
  let colEls = [];             // 连排模式：每页 <img> 引用（与 pages 同序）
  let colChapter = null;       // 连排列当前所属章节
  let colAdvancing = false;    // 到底自动接下一章的防重入锁
  let colPendingSeek = -1;     // 建列后待定位的页（-1 = 无）
  let wheelAcc = 0, wheelLockUntil = 0;  // 滚轮累计阈值状态（防触控板小步误触）
  let chapters = [], currentChapterIndex = 0, chapterToastEnabled = true;
  // 章节预加载：key=chapterId, value={pages:[], promise, images:[]}
  let preloadCache = {};
  const preloadSettings = {
    enabled: Settings.get('reader_preload_enabled', true),
    thresholdPages: Math.max(1, parseInt(Settings.get('reader_preload_threshold_pages', 5), 10) || 5),
    thresholdRatio: Math.max(1, Math.min(100, parseInt(Settings.get('reader_preload_threshold_ratio', 25), 10) || 25)),
    preloadCount: Math.max(1, parseInt(Settings.get('reader_preload_count', 10), 10) || 10)
  };

  (async () => {
    const [r, cfg] = await Promise.all([api('GET', `/api/read/${jmId}`), loadAppConfig()]);
    chapterToastEnabled = cfg.reader_chapter_toast !== '0';
    if (!r.success) {
      stage.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-face-sad-tear"></i></div><h3>无法打开</h3><p>${escapeHtml(r.message || '')}</p></div>`;
      return;
    }
    const d = r.data;
    titleEl.textContent = d.title;
    chapters = d.chapters || [];
    currentChapter = (d.current_chapter || '1');
    currentChapterIndex = Math.max(0, chapters.findIndex(c => c.id === currentChapter));
    pages = Array.isArray(d.current_chapter_pages) ? d.current_chapter_pages : [];
    updateChapterUI();
    if (!pages.length) {
      stage.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-circle-exclamation"></i></div><h3>该章节没有图片</h3><p>可能是下载未完成或目录异常</p></div>`;
      return;
    }
    // v2.14 续读：从上次读到的页继续（连排模式先预载前几页拿宽高，定位才准）
    const startPage = Math.min(Math.max(0, d.read_progress || 0), pages.length - 1);
    if (startPage > 0) {
      await preloadImages(currentChapter, pages.slice(0, Math.min(startPage + 2, pages.length))).catch(() => {});
    }
    renderPage(startPage);
  })();

  function updateChapterUI() {
    const hasMulti = chapters.length > 1;
    if (chapSide) chapSide.style.display = hasMulti ? 'flex' : 'none';
    if (chapInfo && chapters[currentChapterIndex]) {
      chapInfo.textContent = `${currentChapterIndex + 1} / ${chapters.length}`;
    }
    if (chapList) {
      chapList.innerHTML = chapters.map((c, idx) =>
        `<button class="reader__chap-item${idx === currentChapterIndex ? ' on' : ''}" data-idx="${idx}">${escapeHtml(c.name || ('第' + (idx + 1) + '章'))}</button>`
      ).join('');
      chapList.querySelectorAll('.reader__chap-item').forEach(b => {
        const idx = parseInt(b.dataset.idx, 10);
        // 悬停即预载该章页码 + 前 N 张图，直接跳章也不再卡
        b.addEventListener('mouseenter', () => preloadChapter(idx).catch(() => {}));
        b.addEventListener('click', async () => {
          if (idx !== currentChapterIndex && await loadChapter(idx)) renderPage(0);
          if (chapSide) chapSide.classList.remove('open');
        });
      });
    }
    if (prevChapBtn) prevChapBtn.disabled = currentChapterIndex <= 0;
    if (nextChapBtn) nextChapBtn.disabled = currentChapterIndex >= chapters.length - 1;
  }

  // ── 并发受限的图片预加载：一次最多 4 张并行，既快速备齐又不阻塞网络 ──
  // v2.14：同时记录真实宽高（dims）——连排模式建列时用作占位比例，定位不漂移
  const PRELOAD_CONCUR = 4;
  function cacheSlot(cid) {
    return preloadCache[cid] || (preloadCache[cid] = { pages: [], images: new Set(), dims: {} });
  }
  async function preloadImages(cid, pageList, count) {
    const slice = pageList.slice(0, Math.max(0, count));
    const cache = cacheSlot(cid);
    let cursor = 0;
    async function worker() {
      while (cursor < slice.length) {
        const p = slice[cursor++];
        if (cache.images.has(p)) continue;
        await new Promise(resolve => {
          const img = new Image();
          img.onload = () => {
            if (img.naturalWidth) cache.dims[p] = { w: img.naturalWidth, h: img.naturalHeight };
            resolve();
          };
          img.onerror = () => resolve();
          img.src = `/api/comic/${jmId}/page/${p}?chapter=${encodeURIComponent(cid)}`;
          cache.images.add(p);
        });
      }
    }
    const workers = [];
    for (let w = 0; w < Math.min(PRELOAD_CONCUR, slice.length); w++) workers.push(worker());
    await Promise.all(workers);
  }

  // 拉取某章页码列表（带缓存），切换章节时跳过 API 往返
  async function fetchChapterPages(index) {
    if (index < 0 || index >= chapters.length) return null;
    const cid = chapters[index].id;
    const c = preloadCache[cid];
    if (c && c.pages.length) return c.pages;
    const r = await api('GET', `/api/read/${jmId}/chapter/${encodeURIComponent(cid)}`);
    if (!r.success) return null;
    const cp = Array.isArray(r.data.current_chapter_pages) ? r.data.current_chapter_pages : [];
    cacheSlot(cid).pages = cp;
    return cp;
  }

  async function loadChapter(index) {
    if (index < 0 || index >= chapters.length) return false;
    const targetCid = chapters[index].id;
    const cached = preloadCache[targetCid];
    if (cached && cached.pages.length) {
      currentChapterIndex = index;
      currentChapter = targetCid;
      pages = cached.pages;
    } else {
      const r = await api('GET', `/api/read/${jmId}/chapter/${encodeURIComponent(targetCid)}`);
      if (!r.success) { toast('章节加载失败', 'error'); return false; }
      pages = Array.isArray(r.data.current_chapter_pages) ? r.data.current_chapter_pages : [];
      const slot = cacheSlot(targetCid);
      slot.pages = pages;
      currentChapterIndex = index;
      currentChapter = targetCid;
    }
    updateChapterUI();
    // 进入新章节后，立刻备好「下一章」页码列表（极轻量），让下次切换零 API 等待
    const nextIdx = index + 1;
    if (nextIdx < chapters.length) fetchChapterPages(nextIdx).catch(() => {});
    return true;
  }

  async function preloadChapter(index) {
    if (index < 0 || index >= chapters.length) return;
    const pagesList = await fetchChapterPages(index);
    if (!pagesList || !pagesList.length) return;
    await preloadImages(chapters[index].id, pagesList, preloadSettings.preloadCount);
  }
  function shouldPreload() {
    if (!preloadSettings.enabled || !pages.length) return false;
    const nextIdx = currentChapterIndex + 1;
    if (nextIdx >= chapters.length) return false;
    const c = preloadCache[chapters[nextIdx].id];
    if (c && c.images.size >= Math.min(preloadSettings.preloadCount, c.pages.length || preloadSettings.preloadCount)) return false;
    const remaining = pages.length - current - 1;
    const ratio = ((current + 1) / pages.length) * 100;
    return remaining <= preloadSettings.thresholdPages || ratio >= preloadSettings.thresholdRatio;
  }
  function maybePreload() {
    if (!shouldPreload()) return;
    preloadChapter(currentChapterIndex + 1).catch(() => {});
  }

  // 章节内预取：边读边把当前章后续 N 页在后台备好，翻页时浏览器直接从缓存取 → 零等待
  function maybePrefetchCurrent() {
    if (!preloadSettings.enabled || !pages.length) return;
    const start = current + 1;
    if (start >= pages.length) return;
    const slice = pages.slice(start, start + preloadSettings.preloadCount);
    if (!slice.length) return;
    preloadImages(currentChapter, slice, slice.length);
  }

  function showBoundaryToast(msg) {
    if (!chapterToastEnabled) return;
    toastClosable(msg, 'info', 3000);
  }

  // 读完自动标记：翻越最后一章末尾时置 finished 位（幂等，仅标记一次）
  let _finishedMarked = false;
  async function markReadFinishedOnce() {
    if (_finishedMarked) return;
    _finishedMarked = true;
    try {
      const r = await api('POST', `/api/status/${jmId}/finished`, { finished: true });
      if (r.success) toast('已标记读完', 'success');
    } catch (_) { _finishedMarked = false; }
  }

  async function renderPage(i) {
    // 翻到上一章最后一页
    if (i < 0) {
      if (currentChapterIndex > 0) {
        if (await loadChapter(currentChapterIndex - 1)) renderPage(pages.length - 1);
      } else {
        showBoundaryToast('已经是第一章了');
      }
      return;
    }
    // 翻到下一章第一页
    if (i >= pages.length) {
      if (currentChapterIndex < chapters.length - 1) {
        if (await loadChapter(currentChapterIndex + 1)) renderPage(0);
      } else {
        showBoundaryToast('已经是最后一章了');
        markReadFinishedOnce();
      }
      return;
    }
    current = i;
    // 览图模式：只更新高亮/页码，不加载大图
    if (overview) {
      document.getElementById('pageInfo').textContent = `${current + 1} / ${pages.length}`;
      updateRail();
      markOverviewCurrent();
      return;
    }
    // 连排模式：定位到列内第 i 页
    if (isCol()) { colSeek(i); return; }
    maybePreload(); // 翻页时检查是否预加载下一章
    maybePrefetchCurrent(); // 章节内后续 N 页后台预取，翻页零等待
    saveProgressDebounced();
    stage.scrollTop = 0;
    stage.innerHTML = '';
    const spinner = document.createElement('div');
    spinner.className = 'reader__loading';
    spinner.innerHTML = '<i class="fas fa-spinner"></i>';
    stage.appendChild(spinner);
    const img = document.createElement('img');
    img.className = 'reader__img';
    img.alt = `第 ${current + 1} 页`;
    // 绝对定位 + 像素宽度驱动缩放：缩放时不重建 img，直接用 transform-friendly 的 width 控制，
    // 配合 stage 滚动实现「以光标为中心」缩放与自由平移。
    img.style.position = 'absolute';
    img.style.left = '0';
    img.style.top = '0';
    img.style.width = (stage.clientWidth * zoom) + 'px';
    img.style.height = 'auto';
    img.style.maxWidth = 'none';
    img.style.maxHeight = 'none';
    img.onload = () => {
      currentImg = img;
      if (spinner.parentNode) spinner.remove();
      if (!viewInitialized) { fitView(); viewInitialized = true; } // 首屏按模式默认适配（整页 / 半页）
    };
    img.src = `/api/comic/${jmId}/page/${pages[i]}?chapter=${encodeURIComponent(currentChapter)}`;
    img.onerror = () => {
      stage.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-image"></i></div><h3>加载失败</h3><p>章节 ${escapeHtml(currentChapter)} · 第 ${current + 1} 页</p></div>`;
    };
    stage.appendChild(img);
    currentImg = img;
    document.getElementById('pageInfo').textContent = `${current + 1} / ${pages.length}`;
    updateRail();
  }

  // 以 (focalX, focalY) 为锚点缩放：focalX/focalY 为相对 stage 视口的像素坐标；
  // 缺省时取视野中心（用于 +/- 按钮）。核心：缩放前后保持光标下的图片内容点不动。
  function applyZoom(newZoom, focalX, focalY) {
    const img = currentImg;
    if (!img) return;
    newZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, newZoom));
    const sw = stage.clientWidth, sh = stage.clientHeight;
    if (focalX == null) { focalX = sw / 2; focalY = sh / 2; }
    const oldW = img.clientWidth, oldH = img.clientHeight;
    let fX = 0, fY = 0;
    if (oldW > 0 && oldH > 0) {
      // 缩放前：光标在图片内容坐标系中的占比
      fX = (stage.scrollLeft + focalX) / oldW;
      fY = (stage.scrollTop + focalY) / oldH;
    }
    zoom = newZoom;
    img.style.width = (sw * zoom) + 'px';
    img.style.height = 'auto';
    const newW = img.clientWidth, newH = img.clientHeight;
    if (newW > 0 && newH > 0) {
      // 让同一内容点回到光标下方
      stage.scrollLeft = fX * newW - focalX;
      stage.scrollTop = fY * newH - focalY;
    }
    zoomVal.textContent = Math.round(zoom * 100) + '%';
  }

  // 按当前滚轮模式计算默认适配：zoom=整页完整显示(contain)；page=宽度铺满（滚轮页内滑、到底翻页）
  function fitView() {
    const img = currentImg;
    if (!img || !img.naturalWidth) return;
    const sw = stage.clientWidth, sh = stage.clientHeight;
    let targetW;
    if (wheelMode === 'page') {
      targetW = sw; // 滑动翻页：宽度铺满视口
    } else if (wheelMode === 'scroll') {
      const r = (sh * 2) / img.naturalHeight; // 旧半页适配（连排模式下不会走到这里）
      targetW = img.naturalWidth * r;
    } else {
      const r = Math.min(sw / img.naturalWidth, sh / img.naturalHeight); // 整页 contain
      targetW = img.naturalWidth * r;
    }
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, targetW / sw));
    img.style.width = (sw * zoom) + 'px';
    img.style.height = 'auto';
    img.style.left = (sw > img.clientWidth ? (sw - img.clientWidth) / 2 : 0) + 'px';
    stage.scrollTop = 0;
    stage.scrollLeft = 0;
    zoomVal.textContent = Math.round(zoom * 100) + '%';
  }

  /* ── v2.14 连排模式（scroll）：整章一列，滚轮原生滚动 = 翻页 ── */
  function isCol() { return wheelMode === 'scroll'; }

  function buildColumn() {
    colChapter = currentChapter;
    colEls = [];
    colAdvancing = false;
    stage.classList.add('is-col');
    stage.innerHTML = '';
    stage.style.setProperty('--pw', Math.round(stage.clientWidth * zoom) + 'px');
    const cache = cacheSlot(currentChapter);
    pages.forEach((p, i) => {
      const wrap = document.createElement('div');
      wrap.className = 'reader__page';
      const img = document.createElement('img');
      img.className = 'reader__img';
      img.loading = 'lazy';
      img.alt = `第 ${i + 1} 页`;
      img.dataset.idx = String(i);
      const dim = cache.dims[p];
      // 占位比例：已知真实宽高用它，未知按 1.4 估（加载后自动纠正，防滚动漂移）
      img.style.aspectRatio = dim ? (dim.w + '/' + dim.h) : '1.4';
      img.onload = () => {
        if (img.naturalWidth) {
          cache.dims[p] = { w: img.naturalWidth, h: img.naturalHeight };
          img.style.aspectRatio = '';
        }
      };
      img.onerror = () => { wrap.classList.add('is-err'); };
      img.src = `/api/comic/${jmId}/page/${p}?chapter=${encodeURIComponent(currentChapter)}`;
      wrap.appendChild(img);
      stage.appendChild(wrap);
      colEls.push(img);
    });
    document.getElementById('pageInfo').textContent = `${current + 1} / ${pages.length}`;
    updateRail();
  }

  function colSeek(i) {
    i = Math.min(Math.max(0, i), Math.max(0, pages.length - 1));
    if (colChapter !== currentChapter || !colEls.length) {
      colPendingSeek = i;
      buildColumn();
      colApplyPendingSeek();
      return;
    }
    const el = colEls[i];
    if (el) {
      stage.scrollTop = Math.max(0, el.offsetTop - 8);
      colSync(i);   // 显式同步（滚动位置若恰好不变，scroll 事件不触发）
    }
  }

  function colApplyPendingSeek() {
    if (colPendingSeek < 0) return;
    const i = colPendingSeek;
    colPendingSeek = -1;
    const el = colEls[i];
    if (el) stage.scrollTop = Math.max(0, el.offsetTop - 8);
    colSync(i);
  }

  function colSync(forceIdx) {
    if (!pages.length) return;
    let idx = (typeof forceIdx === 'number' && forceIdx >= 0)
      ? forceIdx
      : ReaderLogic.columnIndex(
          stage.scrollTop, stage.clientHeight,
          colEls.map(el => el.offsetTop),
          colEls.map(el => el.offsetHeight || 0));
    if (idx < 0) return;
    if (idx !== current) {
      current = idx;
      maybePreload();
      maybePrefetchCurrent();
      saveProgressDebounced();
      if (overview) markOverviewCurrent();
    }
    currentImg = colEls[current] || null;
    document.getElementById('pageInfo').textContent = `${current + 1} / ${pages.length}`;
    updateRail();
    // 到底：非末章自动接下一章；末章标记读完
    const atBottom = stage.scrollTop + stage.clientHeight >= stage.scrollHeight - 4;
    if (atBottom && current >= pages.length - 1) {
      if (currentChapterIndex < chapters.length - 1) {
        if (!colAdvancing) {
          colAdvancing = true;
          loadChapter(currentChapterIndex + 1).then(ok => {
            colAdvancing = false;
            if (ok) { zoom = 1; viewInitialized = false; renderPage(0); }
          });
        }
      } else {
        markReadFinishedOnce();
      }
    }
  }

  function applyColZoom(newZoom) {
    zoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, newZoom));
    stage.style.setProperty('--pw', Math.round(stage.clientWidth * zoom) + 'px');
    zoomVal.textContent = Math.round(zoom * 100) + '%';
  }

  /* ── v2.14 右侧进度滑轨 ── */
  function updateRail() {
    if (!railFill || !pages.length) return;
    railFill.style.height = (((current + 1) / pages.length) * 100).toFixed(2) + '%';
  }

  /* ── v2.14 缩略图览（一屏多页，点击定位） ── */
  function renderOverview() {
    if (!grid) return;
    grid.hidden = false;
    stage.hidden = true;
    grid.innerHTML = ReaderLogic.buildOverviewHtml(jmId, currentChapter, pages, current);
  }

  function markOverviewCurrent() {
    if (!grid) return;
    const cells = grid.querySelectorAll('.reader__grid__cell');
    for (let i = 0; i < cells.length; i++) {
      cells[i].classList.toggle('is-cur', i === current);
    }
    const cur = grid.querySelector('.reader__grid__cell.is-cur');
    if (cur && cur.scrollIntoView) cur.scrollIntoView({ block: 'nearest' });
  }

  function setOverview(on) {
    overview = !!on;
    if (overview) {
      renderOverview();
    } else {
      if (grid) { grid.hidden = true; grid.innerHTML = ''; }
      stage.hidden = false;
    }
    if (overviewBtn) overviewBtn.classList.toggle('on', overview);
    if (rail) rail.hidden = overview;   // 览图模式下隐藏滑轨
  }

  /* ── v2.14 阅读进度防抖落库 ── */
  let _saveTimer = null;
  function saveProgressDebounced() {
    clearTimeout(_saveTimer);
    _saveTimer = setTimeout(() => {
      api('POST', `/api/read/${jmId}/progress`, { page: current }).catch(() => {});
    }, 800);
  }

  document.getElementById('prevBtn').onclick = () => renderPage(current - 1);
  document.getElementById('nextBtn').onclick = () => renderPage(current + 1);
  const pageInfoEl = document.getElementById('pageInfo');
  if (pageInfoEl) pageInfoEl.onclick = () => {
    const p = prompt(`跳转到第几页？（1-${pages.length}）`, String(current + 1));
    if (p === null) return;
    const n = parseInt(p, 10);
    if (isNaN(n) || n < 1 || n > pages.length) { toast(`请输入 1-${pages.length} 之间的页码`, 'warn'); return; }
    renderPage(n - 1);
  };
  if (prevChapBtn) prevChapBtn.onclick = () => { if (currentChapterIndex > 0) { loadChapter(currentChapterIndex - 1).then(() => renderPage(pages.length - 1)); } };
  if (nextChapBtn) nextChapBtn.onclick = () => { if (currentChapterIndex < chapters.length - 1) { loadChapter(currentChapterIndex + 1).then(() => renderPage(0)); } };
  document.getElementById('zoomIn').onclick = () => { if (isCol()) applyColZoom(zoom * 1.2); else applyZoom(zoom * 1.2); };
  document.getElementById('zoomOut').onclick = () => { if (isCol()) applyColZoom(zoom / 1.2); else applyZoom(zoom / 1.2); };
  document.getElementById('closeReader').onclick = () => { history.length > 1 ? history.back() : location.href = '/library'; };

  // ── v2.14 进度滑轨：按下即预览定位，拖动实时预览，松开跳页 ──
  if (rail) {
    let railSeeking = false, railTarget = null;
    const railPreview = (clientY) => {
      if (!pages.length) return;
      const rect = rail.getBoundingClientRect();
      const idx = ReaderLogic.railIndex(clientY, rect.top, rect.height, pages.length);
      if (idx < 0) return;
      railTarget = idx;
      if (railFill) railFill.style.height = (((idx + 1) / pages.length) * 100).toFixed(2) + '%';
      document.getElementById('pageInfo').textContent = `${idx + 1} / ${pages.length}`;
    };
    rail.addEventListener('mousedown', (e) => {
      if (e.button !== 0 || overview) return;
      e.preventDefault();
      railSeeking = true;
      railPreview(e.clientY);
    });
    document.addEventListener('mousemove', (e) => { if (railSeeking) railPreview(e.clientY); });
    document.addEventListener('mouseup', () => {
      if (!railSeeking) return;
      railSeeking = false;
      if (railTarget != null && railTarget !== current) renderPage(railTarget);
      railTarget = null;
    });
  }

  // ── v2.14 览图：切换 + 点击定位 ──
  if (overviewBtn) overviewBtn.onclick = () => setOverview(!overview);
  if (grid) {
    grid.addEventListener('click', (e) => {
      const cell = e.target.closest('[data-idx]');
      if (!cell) return;
      const idx = parseInt(cell.dataset.idx, 10);
      setOverview(false);
      renderPage(idx);
    });
  }

  // 阅读设置（预加载参数）
  const readerSettingsBtn = document.getElementById('readerSettings');
  if (readerSettingsBtn) readerSettingsBtn.onclick = () => {
    openModal({
      title: '阅读设置', sub: '预加载下一章可大幅减少换章卡顿',
      bodyHtml: `
        <div class="setting-row" style="justify-content:space-between">
          <span>启用章节预加载</span>
          <label class="switch"><input type="checkbox" id="rsPreloadEnabled" ${preloadSettings.enabled ? 'checked' : ''}><span class="track"></span></label>
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>剩余页数阈值（页）</span>
          <input type="number" id="rsPreloadPages" value="${preloadSettings.thresholdPages}" min="1" max="50" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>已读比例阈值（%）</span>
          <input type="number" id="rsPreloadRatio" value="${preloadSettings.thresholdRatio}" min="1" max="100" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>预加载页数（张）</span>
          <input type="number" id="rsPreloadCount" value="${preloadSettings.preloadCount}" min="1" max="50" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
        </div>
        <div class="setting-row" style="flex-direction:column;align-items:stretch;gap:8px;margin-top:12px">
          <span>滚轮行为</span>
          <select id="rsWheelMode" style="padding:8px 10px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface-2)">
            <option value="scroll"${wheelMode === 'scroll' ? ' selected' : ''}>上下连排（多页一列，滚轮滚动=翻页）</option>
            <option value="page"${wheelMode === 'page' ? ' selected' : ''}>滑动翻页（页内先滑、到底翻页）</option>
            <option value="zoom"${wheelMode === 'zoom' ? ' selected' : ''}>放大（滚轮缩放，整页默认完整显示）</option>
          </select>
        </div>
      `,
      actions: [
        { key: 'cancel', label: '取消', cls: 'btn--ghost', onClick: c => c() },
        { key: 'ok', label: '保存', cls: 'btn--primary', onClick: c => {
          preloadSettings.enabled = document.getElementById('rsPreloadEnabled').checked;
          preloadSettings.thresholdPages = Math.max(1, parseInt(document.getElementById('rsPreloadPages').value, 10) || 5);
          preloadSettings.thresholdRatio = Math.max(1, Math.min(100, parseInt(document.getElementById('rsPreloadRatio').value, 10) || 25));
          preloadSettings.preloadCount = Math.max(1, parseInt(document.getElementById('rsPreloadCount').value, 10) || 10);
          Settings.set('reader_preload_enabled', preloadSettings.enabled);
          Settings.set('reader_preload_threshold_pages', preloadSettings.thresholdPages);
          Settings.set('reader_preload_threshold_ratio', preloadSettings.thresholdRatio);
          Settings.set('reader_preload_count', preloadSettings.preloadCount);
          // 滚轮行为：即时切换并按新模式重渲染当前页
          const newMode = document.getElementById('rsWheelMode').value;
          Settings.set('reader_wheel_mode', newMode);
          wheelMode = newMode;
          viewInitialized = false;
          if (newMode !== 'scroll') {
            // 退出连排：清列状态，回单页渲染
            stage.classList.remove('is-col');
            colEls = []; colChapter = null;
          }
          stage.hidden = false;
          if (grid) { grid.hidden = true; grid.innerHTML = ''; }
          overview = false;
          if (overviewBtn) overviewBtn.classList.remove('on');
          if (rail) rail.hidden = false;
          renderPage(current);
          toast('阅读设置已保存', 'success');
          c();
        }}
      ]
    });
  };

  // 半隐藏章节侧栏：鼠标靠近左侧触发条（靠中，避开左上角返回键）才滑出
  if (chapTrigger || chapSide) {
    const openChap = () => chapSide && chapSide.classList.add('open');
    const closeChap = () => { if (chapSide && !chapSide.matches(':hover')) chapSide.classList.remove('open'); };
    function inTrigger(e) {
      if (!chapTrigger) return false;
      const r = chapTrigger.getBoundingClientRect();
      return e.clientX >= r.left - 8 && e.clientX <= r.right + 8 && e.clientY >= r.top - 8 && e.clientY <= r.bottom + 8;
    }
    document.addEventListener('mousemove', (e) => {
      if (chapSide && chapSide.matches(':hover')) return;
      if (inTrigger(e)) openChap(); else closeChap();
    });
    if (chapSide) chapSide.addEventListener('mouseleave', closeChap);
    if (chapTrigger) { chapTrigger.addEventListener('mouseenter', openChap); chapTrigger.addEventListener('click', () => chapSide && chapSide.classList.toggle('open')); }
  }

  // 滚轮行为（v2.14）：动作判定交给 ReaderLogic 纯函数（可单测）——
  //   scroll=连排：普通滚轮原生滚动（滚动即翻页）；Ctrl/右键+滚轮=缩放
  //   page=滑动翻页：页内先滑，到底/到顶再翻页
  //   zoom=以光标为锚点缩放
  stage.addEventListener('wheel', (e) => {
    // 连排：普通滚轮直接交给原生滚动（滚动即翻页），不拦截
    if (isCol() && !(e.buttons & 2) && !e.ctrlKey) return;
    // 连排 + 缩放（Ctrl+滚轮 / 右键+滚轮）
    if (isCol()) {
      e.preventDefault();
      applyColZoom(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15));
      return;
    }
    if (!currentImg) return;
    e.preventDefault();
    const rect = stage.getBoundingClientRect();
    const act = ReaderLogic.resolveWheelAction(
      { deltaY: e.deltaY, buttons: e.buttons },
      {
        mode: wheelMode, acc: wheelAcc, lockUntil: wheelLockUntil, now: Date.now(),
        atTop: stage.scrollTop <= 4,
        atBottom: stage.scrollTop + stage.clientHeight >= stage.scrollHeight - 4,
      });
    wheelAcc = act.acc;
    wheelLockUntil = act.lockUntil;
    if (act.action === 'zoom') {
      applyZoom(zoom * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX - rect.left, e.clientY - rect.top);
    } else if (act.action === 'scroll') {
      stage.scrollTop += e.deltaY;
    } else if (act.action === 'scrollPage') {
      stage.scrollTop += act.dir * stage.clientHeight * 0.85;
    } else if (act.action === 'flip') {
      renderPage(current + act.dir);
    }
  }, { passive: false });
  // 右键仅用于配合滚轮缩放，屏蔽系统右键菜单
  stage.addEventListener('contextmenu', (e) => e.preventDefault());
  // 连排模式：滚动即翻页（当前页跟随视口，进度/滑轨/预读全部联动）
  stage.addEventListener('scroll', () => {
    if (isCol() && !overview && colEls.length) colSync();
  }, { passive: true });

  document.addEventListener('keydown', e => {
    if (e.key === 'ArrowLeft') renderPage(current - 1);
    if (e.key === 'ArrowRight') renderPage(current + 1);
    // 放大后竖向平移阅读（Shift+滚轮不可用时的替代，避免与滚轮缩放冲突）
    if (e.key === 'ArrowUp') { if (overview && grid) grid.scrollTop -= 120; else stage.scrollTop -= 120; e.preventDefault(); }
    if (e.key === 'ArrowDown') { if (overview && grid) grid.scrollTop += 120; else stage.scrollTop += 120; e.preventDefault(); }
    if (e.key === 'Escape') {
      if (overview) setOverview(false);   // 先退出览图
      else document.getElementById('closeReader').click();
    }
  });
}

/* ═══════════════════════════════════════════════════════
   下载进度页
   ═══════════════════════════════════════════════════════ */
function initDownloads() {
  const activeWrap = document.getElementById('activeList');
  const histWrap = document.getElementById('historyList');

  function statusBadge(s) {
    const map = {
      starting: ['等待中', 'wait'], downloading: ['下载中', 'run'],
      processing: ['处理中', 'spin'], completed: ['已完成', 'ok'],
      downloaded: ['已完成', 'ok'], error: ['失败', 'err'], canceled: ['已取消', 'err'],
    };
    const [label, kind] = map[s] || ['未知', 'wait'];
    const icon = kind === 'run' ? 'fa-spinner' : kind === 'spin' ? 'fa-arrows-rotate'
      : kind === 'ok' ? 'fa-circle-check' : kind === 'err' ? 'fa-circle-xmark' : 'fa-clock';
    return `<span class="dl-badge dl-badge--${kind}"><i class="fas ${icon}"></i> ${label}</span>`;
  }

  function renderActive(map) {
    const items = Object.entries(map || {}).map(([id, d]) => ({ id, ...d }))
      // 已完成/已下载的任务已写入历史，不应停留在"进行中"区域
      .filter(d => d.status !== 'completed' && d.status !== 'downloaded');
    if (!items.length) {
      activeWrap.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-cloud-arrow-down"></i></div>
        <h3>暂无进行中的下载</h3><p>去「搜索」下载漫画，进度会实时显示在这里</p></div>`;
      return;
    }
    activeWrap.innerHTML = items.map(d => {
      const pct = Math.max(0, Math.min(100, d.progress || 0));
      const title = d.title || ('JM-' + (d.jm_id || ''));
      const isError = d.status === 'error' || d.status === 'canceled';
      return `<div class="dl-card ${isError ? 'dl-card--error' : ''}" data-dlid="${d.id}">
        <div class="dl-card__head">
          <div class="dl-card__title">${escapeHtml(title)}</div>
          <div class="dl-card__badges">${statusBadge(d.status)}${isError ? `<button class="dl-card__del" data-dlid="${d.id}" title="从列表移除"><i class="fas fa-trash"></i></button>` : ''}</div>
        </div>
        <div class="dl-card__msg">${escapeHtml(d.message || '')}</div>
        <div class="dl-progress"><div class="dl-progress__fill" style="width:${pct}%"></div></div>
        <div class="dl-card__meta"><span>${pct}%</span><span>${isError ? '出错/已取消' : '处理中…'}</span></div>
      </div>`;
    }).join('');
    activeWrap.querySelectorAll('.dl-card__del').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const dlid = btn.dataset.dlid;
        if (!confirm('确认从进行中列表移除该任务？')) return;
        const r = await api('DELETE', `/api/download/progress/${dlid}`);
        if (r.success) { toast('已移除', 'success'); tick(); }
        else toast(r.message || '移除失败', 'error');
      });
    });
  }

  function renderHistory(list) {
    const countEl = document.getElementById('historyCount');
    if (countEl) countEl.textContent = list && list.length ? `(${list.length})` : '';
    if (!list || !list.length) {
      histWrap.innerHTML = `<div class="empty"><div class="empty__icon"><i class="fas fa-clock-rotate-left"></i></div>
        <h3>还没有下载记录</h3></div>`;
      return;
    }
    histWrap.innerHTML = list.map(d => {
      const ok = d.download_status === 'completed';
      const missing = ok && d.files_exist === false;
      return `<div class="dl-hist ${ok ? (missing ? 'dl-hist--missing' : 'dl-hist--ok') : 'dl-hist--err'}" data-rid="${d.id}">
        <div class="dl-hist__icon"><i class="fas ${ok ? (missing ? 'fa-triangle-exclamation' : 'fa-circle-check') : 'fa-circle-xmark'}"></i></div>
        <div class="dl-hist__main">
          <div class="dl-hist__title">${escapeHtml(d.title || ('JM-' + d.jm_id))}${missing ? ' <span class="dl-hist__missing-tag">已丢失</span>' : ''}</div>
          <div class="dl-hist__sub">${escapeHtml(d.download_time || '')} · ${ok ? (missing ? '文件已被删除' : '完成') : '失败'}
            ${d.error_message ? (' · ' + escapeHtml(d.error_message)) : ''}</div>
        </div>
        ${missing ? `<button class="dl-hist__redl" data-jmid="${d.jm_id}" title="重新下载"><i class="fas fa-download"></i></button>` : ''}
        <button class="dl-hist__del" data-rid="${d.id}" title="删除该记录"><i class="fas fa-trash"></i></button>
      </div>`;
    }).join('');
    histWrap.querySelectorAll('.dl-hist__redl').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const jmId = parseInt(btn.dataset.jmid);
        if (!confirm('确认重新下载该漫画？将下载到原来的分类。')) return;
        reDownloadFromHistory(jmId);
      });
    });
    histWrap.querySelectorAll('.dl-hist__del').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const rid = parseInt(btn.dataset.rid);
        if (!confirm('确认删除该条下载记录？')) return;
        const r = await api('DELETE', `/api/download/history/${rid}`);
        if (r.success) { toast('已删除', 'success'); loadHistory(); }
        else toast(r.message || '删除失败', 'error');
      });
    });
  }

  async function tick() {
    try {
      const r = await api('GET', '/api/download/progress');
      if (r.success) renderActive(r.data);
    } catch (e) {}
  }
  async function loadHistory() {
    try {
      const r = await api('GET', '/api/download/history');
      if (r.success) renderHistory(r.data);
    } catch (e) {}
  }

  loadHistory();
  tick();
  const timer = setInterval(tick, 1500);
  window.addEventListener('beforeunload', () => clearInterval(timer));

  // 清空全部历史
  const clearBtn = document.getElementById('clearHistoryBtn');
  if (clearBtn) clearBtn.addEventListener('click', async () => {
    if (!confirm('确认清空所有下载记录？此操作不可撤销。')) return;
    const r = await api('DELETE', '/api/download/history');
    if (r.success) { toast(r.message || '已清空', 'success'); loadHistory(); }
    else toast(r.message || '清空失败', 'error');
  });
  // 批量下载入口放在下载管理页
  initBatchBar();

  // 暴露 saveSettings 到全局（占位 redirect 会用到）
  window.__jmRealSaveSettings = saveSettings;
  window.saveSettings = saveSettings;
}

function applyAppIcon(key) {
  // 应用图标：切换侧边栏 logo + 页面 favicon + localStorage（供 base.html 首屏防闪读取）
  const k = /^[a-z0-9]{1,24}$/.test(String(key || '')) ? key : 'default';
  const src = '/static/img/icons/' + k + '.png';
  const img = document.getElementById('appIconImg');
  if (img && img.getAttribute('src') !== src) img.setAttribute('src', src);
  const fav = document.getElementById('appFavicon');
  if (fav && fav.getAttribute('href') !== src) fav.setAttribute('href', src);
  try { localStorage.setItem('jm_app_icon', k); } catch (_) {}
}

function applyGlobalSettings() {
  api('GET', '/api/settings').then(r => {
    if (!r.success) return;
    const s = r.data;
    const pref = String(s.theme || "system").toLowerCase();
    Theme.setPreferred(pref, { start: s.theme_night_start || Theme._nightStart, end: s.theme_night_end || Theme._nightEnd });
    // 应用图标：后端持久化选择同步到前端展示
    if (s.app_icon) applyAppIcon(s.app_icon);
    // 搜索栏行为（search_bar_behavior）即时应用到搜索页 hero：hide → 结果出来滚出视野外时收起 hero 吸附到顶部
    // （应用在 initSearchPage 读取 appConfigCache 生效）
  });
  // system 模式下每 60 秒重算一次（跨时间点跨系统主题自动切换）
  try { clearInterval(window.__jmThemeTicker); } catch (_) {}
  window.__jmThemeTicker = setInterval(() => { try { Theme.apply(); } catch (_) {} }, 60 * 1000);
}

/* 在模块顶部预挂一个 saveSettings 占位，避免在 initSettings() 执行到前（applyGlobalSettings 初始化早于 route 判定）跨函数引用 ReferenceError。
   一旦 initSettings() 执行，会用真实函数覆盖此占位。 */
if (typeof window.saveSettings !== 'function') {
  window.saveSettings = async function __jmSaveSettingsPlaceholder() {
    // 当 saveSettings 真实函数已注入 initSettings() 尾后，这里直接重定向到新引用
    if (window.__jmRealSaveSettings) return window.__jmRealSaveSettings.apply(null, arguments);
    // 否则（还没 initSettings）静默：设置值会在进入设置页时 sync 到后端，不应抛 ReferenceError 打断后续逻辑
    return { ok: false, pending: true };
  };
}

/* ── P2：标签库设置页（settings/tags）交互 ─────────── */
function initTagsPage() {
  // 仅在标签库主页面执行（至少有一个关键容器）
  const statsCard = document.getElementById('statsCard');
  const btnResetInterest = document.getElementById('btnResetInterest');
  const btnClearInterest = document.getElementById('btnClearInterest');
  const btnClearExp = document.getElementById('btnClearExp');
  const btnRefreshStats = document.getElementById('btnRefreshStats');
  const statsBody = document.getElementById('statsBody');
  const statsHead = document.querySelector('.stats-card-head');
  if (!statsCard && !btnResetInterest && !btnClearInterest && !btnClearExp && !btnRefreshStats) return;

  // 1. 汇总统计折叠
  if (statsHead && statsBody) {
    try {
      const wasOpen = localStorage.getItem('jm_stats_open');
      if (wasOpen === '0') {
        statsBody.classList.remove('open');
        statsHead.classList.add('collapsed');
      }
    } catch (_) {}
    statsHead.addEventListener('click', () => {
      const open = statsBody.classList.toggle('open');
      statsHead.classList.toggle('collapsed', !open);
      try { localStorage.setItem('jm_stats_open', open ? '1' : '0'); } catch (_) {}
    });
  }

  // 2. 重置样本（兴趣库）
  if (btnResetInterest) {
    btnResetInterest.addEventListener('click', async () => {
      if (!confirm('确认将标签库重置为系统内置默认样本？\n当前累积的兴趣库标签（搜索结果衍生）将被清除，并用默认种子重建。')) return;
      btnResetInterest.disabled = true;
      try {
        const r = await api('POST', '/api/tag-library/reset-interest');
        if (!r.success) throw new Error(r.message || '重置失败');
        // 刷新顶栏计数
        let tagCnt = r.tag_count || r.data && r.data.tag_count;
        if (tagCnt == null) {
          try { const s = await api('GET', '/api/settings'); if (s.success && s.data) tagCnt = s.data.tag_count || 0; } catch (_) {}
        }
        const cnt = document.getElementById('libTagCount');
        const cnt2 = document.getElementById('libTagCount2');
        if (cnt) cnt.textContent = tagCnt != null ? tagCnt : cnt.textContent;
        if (cnt2) cnt2.textContent = tagCnt != null ? tagCnt : cnt2.textContent;
        toast(r.message || '已重置为默认样本', 'success');
      } catch (e) { toast(e.message || e, 'error'); }
      finally { btnResetInterest.disabled = false; }
    });
  }

  // 3. 清空兴趣库
  if (btnClearInterest) {
    btnClearInterest.addEventListener('click', async () => {
      if (!confirm('确认清空「兴趣库」（搜索累积的全部标签）？\n建议在清空后使用「重置为默认样本」做基础初始化，以免影响智能关联。')) return;
      btnClearInterest.disabled = true;
      try {
        const r = await api('POST', '/api/tag-library/clear', { library: 'interest' });
        if (!r.success) throw new Error(r.message || '清空失败');
        let tagCnt = r.tag_count ?? (r.data && r.data.tag_count);
        if (tagCnt == null) {
          try { const s = await api('GET', '/api/settings'); if (s.success && s.data) tagCnt = s.data.tag_count || 0; } catch (_) {}
        }
        const cnt = document.getElementById('libTagCount');
        const cnt2 = document.getElementById('libTagCount2');
        if (cnt) cnt.textContent = tagCnt != null ? tagCnt : 0;
        if (cnt2) cnt2.textContent = tagCnt != null ? tagCnt : 0;
        toast(r.message || '兴趣库已清空', 'success');
      } catch (e) { toast(e.message || e, 'error'); }
      finally { btnClearInterest.disabled = false; }
    });
  }

  // 4. 清空实验库
  if (btnClearExp) {
    btnClearExp.addEventListener('click', async () => {
      if (!confirm('确认清空「实验库」（JM 号标签全量字典）？\n此库只在切换到「实验库」时才会使用，清空后下次切换会显示 0 条，可重新导入。')) return;
      btnClearExp.disabled = true;
      try {
        const r = await api('POST', '/api/tag-library/clear', { library: 'experimental' });
        if (!r.success) throw new Error(r.message || '清空失败');
        let expCnt = r.exp_tag_count ?? (r.data && r.data.exp_tag_count);
        if (expCnt == null) {
          try { const s = await api('GET', '/api/settings'); if (s.success && s.data) expCnt = s.data.exp_tag_count || 0; } catch (_) {}
        }
        const cnt = document.getElementById('libExpTagCount');
        const cnt2 = document.getElementById('libExpTagCount2');
        if (cnt) cnt.textContent = expCnt != null ? expCnt : 0;
        if (cnt2) cnt2.textContent = expCnt != null ? expCnt : 0;
        toast(r.message || '实验库已清空', 'success');
      } catch (e) { toast(e.message || e, 'error'); }
      finally { btnClearExp.disabled = false; }
    });
  }

  // 5. 加载/刷新汇总统计
  async function loadStats() {
    if (!statsBody) return;
    statsBody.querySelectorAll('[data-chips]').forEach(c => c.innerHTML = '<span class="muted">加载中…</span>');
    const favoCntEl = document.getElementById('statsFavCount');
    const hateCntEl = document.getElementById('statsHateCount');
    if (favoCntEl) favoCntEl.textContent = '…';
    if (hateCntEl) hateCntEl.textContent = '…';
    const r = await api('GET', '/api/tag-stats');
    if (!r.success) {
      statsBody.innerHTML = `<div class="muted" style="padding:12px;text-align:center;">加载失败：${escapeHtml(r.message || '')}</div>`;
      return;
    }
    const d = r.data || {};
    if (favoCntEl) favoCntEl.textContent = `${d.favorite_total || 0} 本`;
    if (hateCntEl) hateCntEl.textContent = `${d.blocked_total || 0} 本`;
    const make = (arr, state) => {
      if (!arr || !arr.length) return '<span class="muted">—— 暂无数据 ——</span>';
      return arr.map(item => {
        const cls = state === 'like' ? 'tag-chip tag-chip--like'
                 : state === 'hate' ? 'tag-chip tag-chip--hate'
                 : 'tag-chip tag-chip--irrel';
        const c = `<span class="tag-chip__count">${item.count}</span>`;
        return `<button class="${cls}" data-cycle="${encodeURIComponent(item.tag)}">${escapeHtml(item.tag)}${c}</button>`;
      }).join('');
    };
    const favChips = document.getElementById('favLikedChips');
    if (favChips) favChips.innerHTML = make(d.favorite_liked, 'like');
    const favHChips = document.getElementById('favHatedChips');
    if (favHChips) favHChips.innerHTML = make(d.favorite_hated, 'hate');
    const hateLChips = document.getElementById('hateLikedChips');
    if (hateLChips) hateLChips.innerHTML = make(d.blocked_liked, 'like');
    const hateHChips = document.getElementById('hateHatedChips');
    if (hateHChips) hateHChips.innerHTML = make(d.blocked_hated, 'hate');
    // cycle 按钮绑定（三态切换）
    statsBody.querySelectorAll('[data-cycle]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const tag = decodeURIComponent(btn.dataset.cycle);
        btn.classList.add('is-locked');
        try {
          const r = await api('POST', '/api/tag-opinions/cycle', { tag });
          if (!r.success) throw new Error(r.message || '切换失败');
          // 刷新整组统计以保证颜色与 count 分组一致
          loadStats();
        } catch (e) { toast(e.message || e, 'error'); btn.classList.remove('is-locked'); }
      });
    });
  }
  if (btnRefreshStats) btnRefreshStats.addEventListener('click', loadStats);
  if (statsBody) loadStats();
}

/* ── P2：标签库管理页（settings/tags-manage）交互 ──── */
function initTagsManagePage() {
  const host = document.getElementById('tagsManageHost');
  if (!host) return;

  const debounce = (fn, delay = 220) => {
    let t; return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), delay); };
  };
  const parseCsvSet = (s) => {
    if (!s) return new Set();
    return new Set(String(s).split(',').map(x => x.trim().toLowerCase()).filter(Boolean));
  };
  const chipClassFor = (tag, likeSet, hateSet, ignoreSet) => {
    const t = String(tag).trim().toLowerCase();
    if (likeSet.has(t)) return 'tag-chip tag-chip--like';
    if (hateSet.has(t)) return 'tag-chip tag-chip--hate';
    if (ignoreSet.has(t)) return 'tag-chip tag-chip--irrel';
    return 'tag-chip';
  };
  const stateIcon = (tag, likeSet, hateSet, ignoreSet) => {
    const t = String(tag).trim().toLowerCase();
    if (likeSet.has(t)) return '<i class="fas fa-heart" style="font-size:10px;"></i>';
    if (hateSet.has(t)) return '<i class="fas fa-ban" style="font-size:10px;"></i>';
    if (ignoreSet.has(t)) return '<i class="fas fa-eye-slash" style="font-size:10px;"></i>';
    return '';
  };

  // 本地状态
  let state = {
    library: 'interest',
    mode: 'all',
    search: '',
    page: 1,
    pageSize: 200,
    total: 0,
    likeSet: new Set(),
    hateSet: new Set(),
    ignoreSet: new Set()
  };

  // —— DOM 引用 ——
  const segInterest = document.getElementById('segInterest');
  const segExp = document.getElementById('segExp');
  const cntLibTag = document.getElementById('libTagCountManage');
  const cntExpTag = document.getElementById('libExpTagCountManage');
  const hideIrrEl = document.getElementById('hideIrrelevantManage');
  const tabsWrap = document.getElementById('filterTabsManage');
  const searchInput = document.getElementById('searchTagManage');
  const emptyWrap = document.getElementById('emptyManage');
  const grid = document.getElementById('gridManage');
  const pageInfoEl = document.getElementById('pageInfoManage');
  const moreBtn = document.getElementById('moreBtnManage');
  const addInput = document.getElementById('addTagInput');
  const addLikeBtn = document.getElementById('addLikeBtn');
  const addHateBtn = document.getElementById('addHateBtn');
  const addIrrelBtn = document.getElementById('addIrrelBtn');
  const addLibBtn = document.getElementById('addLibBtn');

  // —— 初始加载 opinions + 首屏 ——
  async function loadOpinions() {
    const r = await api('GET', '/api/tag-opinions');
    if (r.success) {
      const d = r.data || {};
      state.likeSet = parseCsvSet(d.manual_csv || '');
      state.hateSet = parseCsvSet(d.hated_csv || '');
      state.ignoreSet = parseCsvSet((d.ignored_csv || '') + ',' + (d.user_ignored_csv || ''));
      updateTabBadges();
    }
  }

  function updateTabBadges() {
    // 实际 badge 数字在 query 响应里（后端会带），这里仅做 UI 状态
    const like = document.getElementById('badgeLike');
    const hate = document.getElementById('badgeHate');
    const irrel = document.getElementById('badgeIrrel');
    const all = document.getElementById('badgeAll');
    // 已在模板里预设为 0；真实数据在 loadList 的 total_by_mode 里覆盖
    if (like) like.textContent = state.likeSet.size;
    if (hate) hate.textContent = state.hateSet.size;
    if (irrel) irrel.textContent = state.ignoreSet.size;
    if (all) all.textContent = state.total || 0;
  }

  function setMode(newMode) {
    state.mode = newMode;
    state.page = 1;
    tabsWrap.querySelectorAll('.filter-tab').forEach(t => {
      t.classList.toggle('on', (t.dataset.mode || '') === newMode);
    });
    loadList();
  }
  function setLibrary(newLib) {
    state.library = newLib;
    state.page = 1;
    [segInterest, segExp].forEach(seg => {
      if (!seg) return;
      seg.classList.toggle('on', seg.dataset.lib === newLib);
    });
    // 即时保存到 active_tag_lib（可能 initSettings() 没跑，先取 window 上暴露的版本）
    const _save = typeof window.saveSettings === 'function' ? window.saveSettings : (typeof saveSettings === 'function' ? saveSettings : null);
    if (_save) _save(false);
    loadList();
  }

  async function loadList() {
    emptyWrap.style.display = 'none';
    if (grid) grid.innerHTML = '<span class="muted">加载中…</span>';
    if (moreBtn) moreBtn.style.display = 'none';
    if (pageInfoEl) pageInfoEl.textContent = '';

    const payload = {
      library: state.library,
      mode: state.mode,
      search: String(state.search || '').trim(),
      page: state.page,
      page_size: state.pageSize,
      hide_irrelevant: hideIrrEl ? hideIrrEl.checked : true
    };
    const r = await api('POST', '/api/tag-library/manage', payload);
    if (!r.success) {
      if (grid) grid.innerHTML = `<span class="muted">加载失败：${escapeHtml(r.message || '')}</span>`;
      return;
    }
    const d = r.data || {};
    state.total = d.total || 0;
    // 更新 segmented 计数
    if (cntLibTag) cntLibTag.textContent = d.tag_count ?? state.library === 'interest' ? state.total : (d.tag_count ?? 0);
    if (cntExpTag) cntExpTag.textContent = d.exp_tag_count ?? state.library === 'experimental' ? state.total : (d.exp_tag_count ?? 0);
    // tab badge（使用后端给出的 total_by_mode 更准确）
    const byMode = d.total_by_mode || {};
    const badgeAll = document.getElementById('badgeAll');
    if (badgeAll) badgeAll.textContent = (state.total != null) ? state.total : (byMode.all || 0);
    const likeB = document.getElementById('badgeLike');
    if (likeB) likeB.textContent = byMode.like != null ? byMode.like : state.likeSet.size;
    const hateB = document.getElementById('badgeHate');
    if (hateB) hateB.textContent = byMode.hate != null ? byMode.hate : state.hateSet.size;
    const irrelB = document.getElementById('badgeIrrel');
    if (irrelB) irrelB.textContent = byMode.irrelevant != null ? byMode.irrelevant : state.ignoreSet.size;

    const items = d.items || [];
    const start = (state.page - 1) * state.pageSize + 1;
    const end = Math.min(state.page * state.pageSize, state.total);

    if (!items.length) {
      if (grid) grid.innerHTML = '';
      emptyWrap.style.display = '';
      const tEl = emptyWrap.querySelector('.empty-state__title');
      const dEl = emptyWrap.querySelector('.empty-state__desc');
      if (state.search) {
        if (tEl) tEl.textContent = '找不到匹配的标签';
        if (dEl) dEl.textContent = `试试换个关键词，或在下方手动添加「${escapeHtml(state.search)}」到喜欢/讨厌/无关/当前库。`;
      } else if (state.mode !== 'all') {
        if (tEl) tEl.textContent = '当前视图暂无标签';
        if (dEl) dEl.textContent = '切到「全部」查看完整标签库，或使用手动添加按钮录入。';
      } else {
        if (tEl) tEl.textContent = (state.library === 'experimental' ? '实验库尚未启用' : '标签库是空的');
        if (dEl) dEl.textContent = (state.library === 'experimental'
          ? '实验库需要把 JM 号对应到的标签全量写入字典。目前为空时，可以回到「标签库」页导入，或手动在下方添加。'
          : '在搜索页搜索几个关键词就会自动累积到兴趣库；也可以用下方手动添加。');
      }
      if (pageInfoEl) pageInfoEl.textContent = `共 ${state.total} 条`;
      return;
    }

    // HTML: 第一页直接覆盖，后续 append
    const html = items.map(t => {
      const tag = (typeof t === 'string') ? t : (t.tag || t.name || '');
      const count = (typeof t === 'object' && t.count != null) ? Number(t.count) : 0;
      const cls = chipClassFor(tag, state.likeSet, state.hateSet, state.ignoreSet);
      const countEl = (count > 0) ? `<span class="tag-chip__count">${count}</span>` : '';
      const icon = stateIcon(tag, state.likeSet, state.hateSet, state.ignoreSet);
      return `<button class="${cls}" data-tag="${encodeURIComponent(tag)}">${icon}<span>${escapeHtml(tag)}</span>${countEl}</button>`;
    }).join('');
    if (state.page === 1) grid.innerHTML = html; else grid.insertAdjacentHTML('beforeend', html);

    // 绑定 chip 点击：三态 cycle
    grid.querySelectorAll('button.tag-chip[data-tag]').forEach(btn => {
      if (btn.dataset.bindCycleDone) return;
      btn.dataset.bindCycleDone = '1';
      btn.addEventListener('click', async () => {
        const tag = decodeURIComponent(btn.dataset.tag);
        btn.classList.add('is-locked');
        try {
          const r = await api('POST', '/api/tag-opinions/cycle', { tag });
          if (!r.success) throw new Error(r.message || '切换失败');
          // 更新本地集合
          const ns = r.new_state;
          const t = tag.trim().toLowerCase();
          [state.likeSet, state.hateSet, state.ignoreSet].forEach(s => s.delete(t));
          if (ns === 'like') state.likeSet.add(t);
          else if (ns === 'hate') state.hateSet.add(t);
          else if (ns === 'irrelevant') state.ignoreSet.add(t);
          // 重新渲染该 chip 的样式（用新属性刷新 DOM，不做全量 reload 避免分页抖）
          btn.outerHTML = (() => {
            // 重算 count 文本保留
            const countEl = count > 0 ? `<span class="tag-chip__count">${btn.querySelector('.tag-chip__count')?.textContent || ''}</span>` : '';
            const cls = chipClassFor(t, state.likeSet, state.hateSet, state.ignoreSet);
            const ic = stateIcon(t, state.likeSet, state.hateSet, state.ignoreSet);
            return `<button class="${cls}" data-tag="${encodeURIComponent(t)}" data-bind-cycle-done="1">${ic}<span>${escapeHtml(t)}</span>${countEl}</button>`;
          })();
          // 重新绑定（outerHTML 替换后需要重新挂一次下一次点击）
          const fresh = grid.querySelector(`button.tag-chip[data-tag="${encodeURIComponent(t)}"]:not([data-bind-cycle-done="1"])`);
          // 由于上面已经写了 data-bind-cycle-done=1 新生成 btn 就不再重入了；
          // 更简单：改为走事件委托的方式亦可，这里直接 rebind 一次（替换一次后 rebind）：
          // 放弃事件委托复杂度，刷新整页模式分布数字：
          updateTabBadges();
        } catch (e) { toast(e.message || e, 'error'); btn.classList.remove('is-locked'); }
      });
    });

    // 分页信息 / 更多按钮
    if (pageInfoEl) pageInfoEl.textContent = `第 ${start} - ${end} 条 / 共 ${state.total} 条`;
    if (moreBtn) {
      moreBtn.style.display = (end < state.total) ? '' : 'none';
    }
  }

  // —— 事件：数据源 segmented ——
  if (segInterest) segInterest.addEventListener('click', () => setLibrary('interest'));
  if (segExp) segExp.addEventListener('click', () => setLibrary('experimental'));

  // —— 事件：filter tabs（事件委托） ——
  if (tabsWrap) {
    tabsWrap.addEventListener('click', (e) => {
      const t = e.target.closest('.filter-tab');
      if (!t) return;
      setMode(t.dataset.mode || 'all');
    });
  }

  // —— 事件：搜索 debounce ——
  if (searchInput) {
    searchInput.addEventListener('input', debounce(() => {
      state.search = searchInput.value || '';
      state.page = 1;
      loadList();
    }, 220));
  }

  // —— 事件：无关开关即时 + save ——
  if (hideIrrEl) {
    hideIrrEl.addEventListener('change', () => {
      // 同步到 settings 页主开关
      const master = document.getElementById('hideIrrelevant');
      if (master) master.checked = hideIrrEl.checked;
      state.page = 1;
      loadList();
      saveSettings(false);
    });
  }

  // —— 事件：显示更多 ——
  if (moreBtn) moreBtn.addEventListener('click', () => {
    state.page += 1;
    loadList();
  });

  // —— 事件：手动添加 4 个按钮 ——
  async function addWithOp(op) {
    const raw = (addInput ? addInput.value || '' : '').trim();
    if (!raw) { toast('请先输入要添加的标签', 'warn'); return; }
    const payload = { tags: raw, op };
    if (op === 'add_to_library') payload.library = state.library;
    const r = await api('POST', '/api/tag-opinions/bulk', payload);
    if (!r.success) { toast(r.message || '操作失败', 'error'); return; }
    toast(r.message || '已添加', 'success');
    // 清空输入并同步本地 sets 与顶栏计数
    if (addInput) addInput.value = '';
    await loadOpinions();
    // 数据源计数更新：如果是 add_to_library，后端 message/top 里不一定带新值
    // 重新拉取 /api/settings 里的 tag_count/exp_tag_count：
    try {
      const set = await api('GET', '/api/settings');
      if (set.success && set.data) {
        if (cntLibTag) cntLibTag.textContent = set.data.tag_count || 0;
        if (cntExpTag) cntExpTag.textContent = set.data.exp_tag_count || 0;
      }
    } catch (_) {}
    state.page = 1;
    loadList();
  }
  if (addLikeBtn) addLikeBtn.addEventListener('click', () => addWithOp('like'));
  if (addHateBtn) addHateBtn.addEventListener('click', () => addWithOp('hate'));
  if (addIrrelBtn) addIrrelBtn.addEventListener('click', () => addWithOp('ignore'));
  if (addLibBtn) addLibBtn.addEventListener('click', () => addWithOp('add_to_library'));

  // —— 启动：先 load opinions（知道每个标签的 4 态归属），再拉首屏列表 ——
  (async () => {
    await loadOpinions();
    loadList();
  })();
}

/* ═══════════════════════════════════════════════════════
   启动路由
   ═══════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', () => {
  applyGlobalSettings();
  document.querySelectorAll('.theme-toggle').forEach(tg => {
    Theme.sync(tg);
    tg.onclick = () => { Theme.toggle(); Theme.save(); };
  });
  setupNav();
  attachRipple();

  // 回收站：启动时静默扫一次到期项（对齐 Android App 启动 checkRecycleExpiry）
  api('POST', '/api/recycle/scan').then(r => {
    if (r && r.success && r.count > 0) toast(`回收站：${r.count} 本已到期，本地图片已清理`, 'info');
  }).catch(() => {});

  // 书架右上角回收站入口 + 角标
  const rcEntry = document.getElementById('recycleEntry');
  if (rcEntry) {
    rcEntry.onclick = () => { location.href = '/recycle'; };
    api('GET', '/api/recycle').then(r => {
      const badge = document.getElementById('recycleBadge');
      if (!badge || !r || !r.success) return;
      const n = r.data.items.length;
      if (n > 0) { badge.textContent = n; badge.style.display = 'inline-block'; }
    }).catch(() => {});
  }

  const p = location.pathname;
  if (p.startsWith('/detail/')) initDetail();
  else if (p.startsWith('/recycle')) initRecycle();
  else if (p.startsWith('/downloads')) initDownloads();
  else if (p.startsWith('/reader/')) initReader();
  else if (p.startsWith('/library')) initLibrary();
  else if (p.startsWith('/settings')) initSettings();
  else initSearch();   // /search（含 /home 302 过来的）
});

/* ═══ 回收站（v2.9.0，对齐 Android RecycleScreen / RecycleManager）═══
   两态：
     宽限中 → 本地图片还在，可直接阅读，剩 N 天；再读一次自动续期
     已删本地 → 只剩封面与记录，重新下载即可拾回
   ─────────────────────────────────────────────────────────── */
async function initRecycle() {
  const list = document.getElementById('rcList');
  const countEl = document.getElementById('rcCount');
  if (!list) return;

  function rowHtml(it) {
    const tag = it.trashed
      ? '<span class="rc-tag gone">本地已删 · 需重新下载</span>'
      : `<span class="rc-tag grace">还剩 ${it.remain_days} 天</span>`;
    const cover = it.cover_api
      ? `<img class="rc-cover" src="${it.cover_api}" alt="" loading="lazy"
              onerror="this.style.visibility='hidden'">`
      : '<div class="rc-cover"></div>';
    const primary = it.trashed
      ? '<button class="d-back" data-act="redl" style="padding:5px 12px;font-size:12px;height:32px"><i class="fas fa-download"></i> 重新下载</button>'
      : '<button class="d-back" data-act="read" style="padding:5px 12px;font-size:12px;height:32px"><i class="fas fa-book-open"></i> 阅读</button>';
    return `<div class="rc-row ${it.trashed ? 'is-trashed' : ''}" data-id="${it.jm_id}">
      ${cover}
      <div class="rc-main">
        <div class="rc-title">${escapeHtml(it.title)}</div>
        <div class="rc-sub">${tag}${it.author ? ' · ' + escapeHtml(it.author) : ''} · JM-${it.jm_id}</div>
      </div>
      <div class="rc-acts">
        ${primary}
        <button class="d-back" data-act="restore" style="padding:5px 12px;font-size:12px;height:32px"
                title="宽限中的：取消宽限回到书架 / 已删本地的：提示重新下载"><i class="fas fa-rotate-left"></i> 恢复</button>
        <button class="d-back" data-act="del" style="padding:5px 12px;font-size:12px;height:32px"
                title="记录 + 本地文件一并删除，不可恢复"><i class="fas fa-trash"></i> 彻底删除</button>
      </div>
    </div>`;
  }

  function bindRows() {
    list.querySelectorAll('.rc-row').forEach(row => {
      const id = row.dataset.id;
      const on = (name, fn) => { const b = row.querySelector(`[data-act="${name}"]`); if (b) b.onclick = fn; };
      on('read', () => { location.href = '/reader/' + id; });
      on('redl', async () => {
        const r = await api('POST', `/api/download/${id}`);
        toast(r && r.success ? '已加入下载队列，完成后自动拾回' : ((r && r.message) || '下载失败'),
              r && r.success ? 'success' : 'error');
      });
      on('restore', async () => {
        const r = await api('POST', `/api/recycle/${id}/restore`);
        if (!r || !r.success) { toast((r && r.message) || '恢复失败', 'error'); return; }
        if (r.need_download) { toast(r.message, 'info'); return; }
        toast(r.message || '已恢复到书架', 'success');
        row.remove();
      });
      on('del', async () => {
        if (!confirm('彻底删除：记录与本地文件都会消失，无法恢复。确定继续？')) return;
        const r = await api('DELETE', `/api/recycle/${id}`);
        if (r && r.success) { toast('已彻底删除', 'success'); row.remove(); }
        else toast((r && r.message) || '删除失败', 'error');
      });
    });
  }

  async function load() {
    const r = await api('GET', '/api/recycle');
    if (!r || !r.success) {
      list.innerHTML = `<div class="rc-empty">加载失败：${escapeHtml((r && r.message) || '未知错误')}</div>`;
      return;
    }
    const items = r.data.items || [];
    if (countEl) {
      countEl.textContent = items.length
        ? `宽限中 ${r.data.pending} 本 · 已删本地 ${r.data.trashed} 本` : '';
    }
    if (!items.length) {
      list.innerHTML = '<div class="rc-empty"><i class="fas fa-box-open" style="font-size:22px;display:block;margin-bottom:8px"></i>回收站是空的</div>';
      return;
    }
    list.innerHTML = items.map(rowHtml).join('');
    bindRows();
  }

  const scanBtn = document.getElementById('rcScanBtn');
  if (scanBtn) scanBtn.onclick = async () => {
    const r = await api('POST', '/api/recycle/scan');
    if (r && r.success) {
      toast(r.count ? `已清理 ${r.count} 本到期的本地内容` : '没有需要清理的条目', 'success');
      load();
    } else toast((r && r.message) || '扫描失败', 'error');
  };
  const clearBtn = document.getElementById('rcClearBtn');
  if (clearBtn) clearBtn.onclick = async () => {
    if (!confirm('清空回收站：所有「已删本地」的记录会被彻底删除（不可恢复）。宽限中的条目不受影响。继续？')) return;
    const r = await api('POST', '/api/recycle/clear');
    if (r && r.success) { toast(`已清空 ${r.deleted} 条`, 'success'); load(); }
    else toast((r && r.message) || '清空失败', 'error');
  };
  const backBtn = document.getElementById('rcBackBtn');
  if (backBtn) backBtn.onclick = () => { location.href = '/library'; };

  // 进入回收站页也扫一次到期项，再拉列表（对齐 Android 进入回收站即扫描）
  await api('POST', '/api/recycle/scan').catch(() => {});
  await load();
}

/* ═══ 漫画详情页（v2.10.0，对齐 Android DetailScreen）═════════════
   入口：搜索卡片 / 书架卡片点击 → /detail/<jm_id>
   数据：GET /api/detail/<jm_id>（本地元数据优先，缺字段回落在线详情）
   ─────────────────────────────────────────────────────────── */

function _dtFmtBytes(n) {
  if (!n) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1048576) return (n / 1024).toFixed(0) + ' KB';
  if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
  return (n / 1073741824).toFixed(2) + ' GB';
}

async function initDetail() {
  const root = document.getElementById('dtRoot');
  if (!root) return;
  const jmId = root.dataset.jm;
  const jmEl = document.getElementById('dtJmId');
  if (jmEl) jmEl.textContent = 'JM-' + jmId;

  const backBtn = document.getElementById('dtBack');
  if (backBtn) backBtn.onclick = () => {
    if (history.length > 1) history.back(); else location.href = '/library';
  };

  // 事件委托：标签 → 标签搜索，作者 → 作者搜索，封面 → 新窗口看大图
  root.onclick = (e) => {
    const tagEl = e.target.closest('.dt-tag');
    if (tagEl) { location.href = '/search?tag=' + encodeURIComponent(tagEl.dataset.tag); return; }
    const auEl = e.target.closest('.dt-author b');
    if (auEl) { location.href = '/search?author=' + encodeURIComponent(auEl.dataset.author); return; }
    const cvEl = e.target.closest('.dt-cover');
    if (cvEl && cvEl.src) window.open(cvEl.src, '_blank');
  };

  const r = await api('GET', '/api/detail/' + jmId);
  if (!r || !r.success) {
    root.innerHTML = `<div class="dt-err"><i class="fas fa-triangle-exclamation" style="font-size:22px;display:block;margin-bottom:8px"></i>${escapeHtml((r && r.message) || '加载失败')}</div>`;
    return;
  }
  root.innerHTML = _dtHtml(r.data);
  _dtBind(r.data);
  api('POST', `/api/status/${jmId}/view`).catch(() => {});   // 查看次数 +1
}

function _dtHtml(d) {
  const st = d.status || {}, rc = d.recycle || {}, pg = d.progress || {};

  const badges = [];
  if (st.favorite) badges.push('<span class="dt-badge warn"><i class="fas fa-star"></i> 已收藏</span>');
  if (d.has_local) badges.push('<span class="dt-badge ok"><i class="fas fa-check"></i> 已下载</span>');
  if (st.finished) badges.push('<span class="dt-badge ok"><i class="fas fa-circle-check"></i> 已读完</span>');
  if (rc.trashed) badges.push('<span class="dt-badge bad"><i class="fas fa-trash-can"></i> 本地已删 · 需重新下载</span>');
  else if (rc.recycle_at > 0) badges.push(`<span class="dt-badge warn"><i class="fas fa-hourglass-half"></i> 回收站中 · 还剩 ${rc.remain_days} 天</span>`);
  if (st.blocked) badges.push('<span class="dt-badge bad"><i class="fas fa-ban"></i> 已拉黑作品</span>');

  const acts = [];
  if (d.has_local) acts.push('<button class="dt-btn primary" data-act="read"><i class="fas fa-book-open"></i> 阅读</button>');
  else acts.push('<button class="dt-btn primary" data-act="dl"><i class="fas fa-download"></i> 下载到书架</button>');
  acts.push(`<button class="dt-btn${st.favorite ? ' primary' : ''}" data-act="fav"><i class="fas fa-star"></i> ${st.favorite ? '取消收藏' : '收藏'}</button>`);
  if (d.has_local) {
    acts.push('<button class="dt-btn" data-act="move"><i class="fas fa-folder-plus"></i> 分类</button>');
    acts.push('<button class="dt-btn danger" data-act="del"><i class="fas fa-trash-can"></i> 移入回收站</button>');
  }
  acts.push('<button class="dt-btn danger" data-act="block-author"><i class="fas fa-user-slash"></i> 拉黑作者</button>');
  acts.push('<button class="dt-btn danger" data-act="block-work"><i class="fas fa-ban"></i> 拉黑作品</button>');

  const meta = [`<span>页数 <b>${d.pages || '—'}</b></span>`,
                `<span>章节 <b>${d.chapter_count || '—'}</b></span>`];
  if (d.file_size) meta.push(`<span>占用 <b>${_dtFmtBytes(d.file_size)}</b></span>`);
  if (d.favorites) meta.push(`<span>在线收藏 <b>${d.favorites}</b></span>`);
  meta.push(`<span>JM号 <b>${d.jm_id}</b></span>`);

  const chapHtml = (d.chapters && d.chapters.length)
    ? d.chapters.map((c, i) => `<div class="dt-ch">
        <span class="dt-ch__no">${i + 1}</span>
        <span class="dt-ch__name">${escapeHtml(c.name || '未命名章节')}</span>
        <span class="dt-ch__pages">${c.pages || 0} 页</span>
      </div>`).join('')
    : '<div class="dt-empty">没有本地章节文件（未下载，或已移入回收站）</div>';

  const tagsHtml = (d.tags && d.tags.length)
    ? d.tags.map(t => `<span class="dt-tag" data-tag="${escapeHtml(t)}">${escapeHtml(t)}</span>`).join('')
    : '<span class="dt-empty">没有标签信息</span>';

  const progHtml = (d.has_local && pg.pages)
    ? `<div class="dt-progress">
         <div class="dt-progress__t">阅读进度：第 ${pg.page || 0} / ${pg.pages} 页（${pg.percent || 0}%）</div>
         <div class="dt-bar"><i style="width:${pg.percent || 0}%"></i></div>
       </div>` : '';

  const catsHtml = (d.categories && d.categories.length)
    ? `<div class="dt-sec"><div class="dt-sec__t"><i class="fas fa-folder-tree" style="color:var(--accent);margin-right:6px"></i>分类</div>
         <div class="dt-tags">${d.categories.map(c => `<span class="dt-tag">${escapeHtml(c.name)}</span>`).join('')}</div></div>`
    : '';

  return `
    <div class="dt-hero">
      <img class="dt-cover" src="${d.cover_api}" alt="" onerror="this.style.visibility='hidden'">
      <div class="dt-main">
        <div class="dt-title">${escapeHtml(d.title)}</div>
        ${d.author ? `<div class="dt-author">作者：<b data-author="${escapeHtml(d.author)}">${escapeHtml(d.author)}</b></div>` : ''}
        <div class="dt-badges">${badges.join('') || '<span class="dt-badge">未下载</span>'}</div>
        ${progHtml}
        <div class="dt-meta">${meta.join('')}</div>
        <div class="dt-acts">${acts.join('')}</div>
      </div>
    </div>
    <div class="dt-sec"><div class="dt-sec__t"><i class="fas fa-tags" style="color:var(--accent);margin-right:6px"></i>标签</div>
      <div class="dt-tags">${tagsHtml}</div></div>
    ${catsHtml}
    ${d.description ? `<div class="dt-sec"><div class="dt-sec__t"><i class="fas fa-align-left" style="color:var(--accent);margin-right:6px"></i>简介</div><div class="dt-desc">${escapeHtml(d.description)}</div></div>` : ''}
    <div class="dt-sec"><div class="dt-sec__t"><i class="fas fa-list-ol" style="color:var(--accent);margin-right:6px"></i>章节（${(d.chapters || []).length}）</div>${chapHtml}</div>`;
}

function _dtBind(d) {
  const root = document.getElementById('dtRoot');
  const on = (act, fn) => { const b = root.querySelector(`[data-act="${act}"]`); if (b) b.onclick = fn; };

  on('read', () => { location.href = '/reader/' + d.jm_id; });

  on('dl', () => { pickCategories([], (cats) => downloadComic(d.jm_id, cats, d.title)); });

  on('fav', async () => {
    const r = await api('POST', `/api/status/${d.jm_id}/favorite`);
    if (r && r.success) { toast(r.favorite ? '已收藏' : '已取消收藏', 'success'); initDetail(); }
    else toast((r && r.message) || '操作失败', 'error');
  });

  on('move', () => {
    pickCategories((d.categories || []).map(c => c.id), async (cats) => {
      const res = await api('PUT', `/api/comic/${d.jm_id}/categories`, { category_ids: cats });
      if (res && res.success) { toast('已更新分类', 'success'); initDetail(); }
      else toast((res && res.message) || '更新分类失败', 'error');
    });
  });

  on('del', async () => {
    if (!confirm('移入回收站：立即删除本地图片，记录与封面保留 7 天，期间可重新下载拾回。继续？')) return;
    const r = await api('DELETE', `/api/delete/${d.jm_id}`);
    if (r && r.success) { toast('已移入回收站', 'success'); initDetail(); }
    else toast((r && r.message) || '操作失败', 'error');
  });

  on('block-author', () => blockAuthor(d.author));
  on('block-work', () => blockWork(d.jm_id, d.title));
}

/* ═══════════════════════════════════════════════════════
   WebView2 全局拖拽补丁（v2.14 翻新）
   背景：WebView2 的 HTML5 DnD 半残——dragstart 会触发、原生拖拽会接管
   （期间 mousemove 被抑制），但 drop 永远不会送达 → 所有 draggable 功能
   （标签 chips 拖入筛选 / 快捷标签排序 / 分类树拖拽归类调层级）全部失效。
   对策：原生 dragstart 一律 preventDefault 灭掉；用 mousedown/mousemove/
   mouseup 合成 dragstart / dragover / dragleave / drop / dragend
   （bubbles 的 DragEvent + 可用的合成 DataTransfer）——
   全应用既有拖拽监听器零改动直接工作。
   ═══════════════════════════════════════════════════════ */
(function () {
  'use strict';
  var THRESHOLD = 6;      // 起拖位移（px），小于此视为普通点击
  var sim = null;         // {source, dt, started, x, y, ghost, lastHit}

  function findDraggable(el) {
    while (el && el !== document) {
      if (el.getAttribute && el.getAttribute('draggable') === 'true') return el;
      el = el.parentNode;
    }
    return null;
  }

  function makeDragEvent(type, x, y, dt) {
    try {
      return new DragEvent(type, {
        bubbles: true, cancelable: true, composed: true,
        clientX: x || 0, clientY: y || 0, dataTransfer: dt || null,
      });
    } catch (_) {
      var ev = document.createEvent('CustomEvent');
      ev.initCustomEvent(type, true, true, null);
      ev.dataTransfer = dt || null;
      return ev;
    }
  }

  function cleanup(st) {
    if (st && st.ghost) st.ghost.remove();
    document.body.classList.remove('jm-dragging');
  }

  // 原生 DnD 一律灭掉：WebView2 半残会留僵死残影，真浏览器里与鼠标模拟双路打架
  document.addEventListener('dragstart', function (e) {
    e.preventDefault();
  }, true);

  document.addEventListener('mousedown', function (e) {
    if (e.button !== 0) return;
    var src = findDraggable(e.target);
    if (!src) return;
    sim = { source: src, dt: null, started: false, x: e.clientX, y: e.clientY, ghost: null, lastHit: null };
  }, true);

  document.addEventListener('mousemove', function (e) {
    if (!sim) return;
    if (!sim.started) {
      if (Math.abs(e.clientX - sim.x) + Math.abs(e.clientY - sim.y) < THRESHOLD) return;
      // 起拖：合成 dragstart，让既有监听器把载荷 setData 进我们的 dt
      //（_dragData 之类的模块级状态也会在此时被置好）
      sim.started = true;
      try { sim.dt = new DataTransfer(); } catch (_) { sim.dt = null; }
      sim.source.dispatchEvent(makeDragEvent('dragstart', e.clientX, e.clientY, sim.dt));
      var rect = sim.source.getBoundingClientRect();
      var g = sim.source.cloneNode(true);
      g.style.cssText += ';position:fixed;z-index:99999;pointer-events:none;margin:0;opacity:.85;' +
        'width:' + rect.width + 'px;box-shadow:0 10px 30px rgba(0,0,0,.28);' +
        'left:' + (e.clientX + 12) + 'px;top:' + (e.clientY + 10) + 'px;';
      document.body.appendChild(g);
      sim.ghost = g;
      document.body.classList.add('jm-dragging');
    }
    e.preventDefault();
    if (sim.ghost) {
      sim.ghost.style.left = (e.clientX + 12) + 'px';
      sim.ghost.style.top = (e.clientY + 10) + 'px';
    }
    var hit = document.elementFromPoint(e.clientX, e.clientY);
    if (hit !== sim.lastHit) {
      if (sim.lastHit) sim.lastHit.dispatchEvent(makeDragEvent('dragleave', e.clientX, e.clientY, sim.dt));
      sim.lastHit = hit;
    }
    if (hit) hit.dispatchEvent(makeDragEvent('dragover', e.clientX, e.clientY, sim.dt));
  }, true);

  document.addEventListener('mouseup', function (e) {
    if (!sim) return;
    var st = sim;
    sim = null;
    if (!st.started) return;   // 未超过阈值 = 普通点击，放行
    e.preventDefault();
    var el = document.elementFromPoint(e.clientX, e.clientY);
    if (el) el.dispatchEvent(makeDragEvent('drop', e.clientX, e.clientY, st.dt));
    st.source.dispatchEvent(makeDragEvent('dragend', e.clientX, e.clientY, st.dt));
    cleanup(st);
  }, true);

  // 拖拽中途焦点丢失（切窗/拖出窗口）也要清场
  window.addEventListener('blur', function () {
    if (sim) { cleanup(sim); sim = null; }
  });
})();
