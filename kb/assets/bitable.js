/**
 * KBase Bitable — Feishu-style multidimensional tables (Phases 0–4).
 * Plain script: all public APIs are attached to window.
 * Depends on: esc, toast, uiPrompt, uiConfirm, TabManager, fetch, DOMPurify (optional).
 */
'use strict';

/* ===== STATE ===== */
var bitableCurrentId = null;
var bitableListCache = [];
var bitableRenderCache = null;
var bitablePageMode = 'table';
var bitableSearchQuery = '';
const bitableSelectedRows = new Set();

const _kbDbCache = new Map();
let _bitableDrawerRow = null;
let _bitableDrawerDbId = null;

/* Solid accents (legacy / borders / progress). */
const KB_SELECT_COLORS = {
  '1': '#3370FF', '2': '#34C724', '3': '#FF8800', '4': '#F54A45',
  '5': '#7B67EE', '6': '#14C0FF', '7': '#F5319D', '8': '#8F959E',
};
/* Pastel pill pairs: bg + text (Feishu-like). */
const KB_SELECT_PILLS = {
  '1': { bg: '#E8F3FF', fg: '#245BDB' },
  '2': { bg: '#E4F7E4', fg: '#1A7F1A' },
  '3': { bg: '#FFF3E0', fg: '#C25A00' },
  '4': { bg: '#FFECEC', fg: '#D92D20' },
  '5': { bg: '#F0ECFF', fg: '#5B4BCC' },
  '6': { bg: '#E0F7FF', fg: '#0A7EA4' },
  '7': { bg: '#FFE8F3', fg: '#C2185B' },
  '8': { bg: '#F2F3F5', fg: '#646A73' },
};

const KB_FILTER_OPS = [
  { id: 'contains', label: '包含' },
  { id: 'eq', label: '等于' },
  { id: 'neq', label: '不等于' },
  { id: 'empty', label: '为空' },
  { id: 'not_empty', label: '不为空' },
];

const KB_FIELD_TYPES = [
  { id: 'text', label: '单行文本', icon: 'Aa', kanbanGroup: true, readonly: false },
  { id: 'longtext', label: '多行文本', icon: '¶', kanbanGroup: false, readonly: false },
  { id: 'number', label: '数字', icon: '#', kanbanGroup: false, readonly: false },
  { id: 'currency', label: '货币', icon: '¥', kanbanGroup: false, readonly: false },
  { id: 'percent', label: '百分比', icon: '%', kanbanGroup: false, readonly: false },
  { id: 'progress', label: '进度', icon: '▰', kanbanGroup: false, readonly: false },
  { id: 'rating', label: '评分', icon: '★', kanbanGroup: false, readonly: false },
  { id: 'date', label: '日期', icon: '📅', kanbanGroup: false, readonly: false },
  { id: 'datetime', label: '日期时间', icon: '🕐', kanbanGroup: false, readonly: false },
  { id: 'select', label: '单选', icon: '◉', kanbanGroup: true, readonly: false },
  { id: 'mselect', label: '多选', icon: '☑', kanbanGroup: true, readonly: false },
  { id: 'checkbox', label: '复选框', icon: '☐', kanbanGroup: false, readonly: false },
  { id: 'url', label: '超链接', icon: '🔗', kanbanGroup: false, readonly: false },
  { id: 'email', label: '邮箱', icon: '@', kanbanGroup: false, readonly: false },
  { id: 'phone', label: '电话', icon: '☎', kanbanGroup: false, readonly: false },
  { id: 'person', label: '人员', icon: '👤', kanbanGroup: true, readonly: false },
  { id: 'attachment', label: '附件', icon: '📎', kanbanGroup: false, readonly: false },
  { id: 'autonumber', label: '自动编号', icon: '#', kanbanGroup: false, readonly: true },
  { id: 'link', label: '关联', icon: '↗', kanbanGroup: false, readonly: false },
  { id: 'lookup', label: '查找引用', icon: '⌕', kanbanGroup: false, readonly: true },
  { id: 'rollup', label: '汇总', icon: 'Σ', kanbanGroup: false, readonly: true },
  { id: 'formula', label: '公式', icon: 'ƒ', kanbanGroup: false, readonly: true },
  { id: 'created_time', label: '创建时间', icon: '🕐', kanbanGroup: false, readonly: true },
  { id: 'modified_time', label: '修改时间', icon: '🕐', kanbanGroup: false, readonly: true },
  { id: 'ai_text', label: 'AI 文本', icon: '✨', kanbanGroup: false, readonly: false },
];

const KB_READONLY_TYPES = new Set(
  KB_FIELD_TYPES.filter(t => t.readonly).map(t => t.id)
);

/* ===== STYLE INJECTION ===== */
(function _injectBitableStyles() {
  if (document.getElementById('bitable-extra-styles')) return;
  const s = document.createElement('style');
  s.id = 'bitable-extra-styles';
  s.textContent = `
#bitable-view{
  --bt-line:#E5E6EB;--bt-line-soft:#F2F3F5;--bt-head:#F7F8FA;--bt-canvas:#F5F6F7;
  --bt-select:#E8F3FF;--bt-select-fg:#1F2329;--bt-accent:#3370FF;
  --bt-pill-radius:4px;--bt-card-radius:8px;
}
[data-theme="dark"] #bitable-view,[data-theme="night"] #bitable-view,.theme-dark #bitable-view{
  --bt-line:var(--border);--bt-line-soft:var(--surface-2);--bt-head:var(--surface-2);
  --bt-select:var(--list-active);--bt-select-fg:var(--text-strong);--bt-accent:var(--primary);
}
#bitable-row-drawer{position:fixed;top:0;right:0;width:420px;max-width:92vw;height:100vh;background:var(--surface);border-left:1px solid var(--bt-line,var(--border));box-shadow:var(--shadow-xl);z-index:10002;display:flex;flex-direction:column;transform:translateX(100%);transition:transform .22s ease}
#bitable-row-drawer.open{transform:translateX(0)}
#bitable-row-drawer .drawer-head{padding:14px 16px;border-bottom:1px solid var(--bt-line,var(--border));display:flex;align-items:center;justify-content:space-between;gap:8px;flex-shrink:0}
#bitable-row-drawer .drawer-head h3{margin:0;font-size:15px;font-weight:600;color:var(--text-strong);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#bitable-row-drawer .drawer-body{flex:1;overflow-y:auto;padding:12px 16px}
#bitable-row-drawer .drawer-field{margin-bottom:14px}
#bitable-row-drawer .drawer-field label{display:block;font-size:11px;color:var(--text-3);margin-bottom:4px;font-weight:500}
#bitable-row-drawer .drawer-field input,#bitable-row-drawer .drawer-field textarea,#bitable-row-drawer .drawer-field select{width:100%;box-sizing:border-box;border:1px solid var(--bt-line,var(--border));border-radius:6px;padding:7px 10px;font-size:13px;background:var(--surface);color:var(--text);outline:none}
#bitable-row-drawer .drawer-field input:focus,#bitable-row-drawer .drawer-field textarea:focus{border-color:var(--bt-accent,var(--primary))}
#bitable-row-drawer .drawer-field .readonly-val{font-size:13px;color:var(--text-2);padding:6px 0}
#bitable-row-drawer .drawer-foot{padding:12px 16px;border-top:1px solid var(--bt-line,var(--border));display:flex;gap:8px;justify-content:flex-end;flex-shrink:0}
#bitable-filter-modal{position:fixed;inset:0;z-index:10003;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.35)}
#bitable-filter-modal.open{display:flex}
#bitable-filter-modal .modal-box{background:var(--surface);border:1px solid var(--bt-line,var(--border));border-radius:10px;box-shadow:var(--shadow-xl);width:520px;max-width:94vw;max-height:80vh;display:flex;flex-direction:column}
#bitable-filter-modal .modal-head{padding:14px 16px;border-bottom:1px solid var(--bt-line,var(--border));font-weight:600;font-size:14px;color:var(--text-strong)}
#bitable-filter-modal .modal-body{flex:1;overflow-y:auto;padding:12px 16px}
#bitable-filter-modal .modal-foot{padding:12px 16px;border-top:1px solid var(--bt-line,var(--border));display:flex;gap:8px;justify-content:flex-end}
.bitable-filter-row{display:flex;gap:6px;align-items:center;margin-bottom:8px;flex-wrap:wrap}
.bitable-filter-row select,.bitable-filter-row input{flex:1;min-width:80px;border:1px solid var(--bt-line,var(--border));border-radius:6px;padding:5px 8px;font-size:12px;background:var(--surface);color:var(--text)}
.bitable-col-resize{position:absolute;top:0;right:0;bottom:0;width:5px;cursor:col-resize;z-index:4}
.bitable-col-resize:hover,.bitable-col-resize.resizing{background:var(--bt-accent,var(--primary));opacity:.35}
.bitable-grid th.frozen,.bitable-grid td.frozen{position:sticky;z-index:2}
.bitable-grid th.frozen{z-index:4;background:var(--bt-head,#F7F8FA)!important}
.bitable-grid td.frozen{background:var(--surface,#fff)!important}
.bitable-grid td.row-check,.bitable-grid th.row-check{min-width:36px;max-width:36px;width:36px;text-align:center}
.bitable-group-header td{background:var(--bt-head,var(--surface-2))!important;font-weight:600;color:var(--text-strong);padding:8px 12px!important}
.bitable-group-header .group-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px;vertical-align:middle}
.bitable-calendar{padding:16px;display:flex;flex-direction:column;gap:16px}
.bitable-calendar-day{border:1px solid var(--bt-line,var(--border));border-radius:8px;overflow:hidden;background:var(--surface)}
.bitable-calendar-day-head{padding:8px 12px;background:var(--bt-head,var(--surface-2));font-weight:600;font-size:13px;border-bottom:1px solid var(--bt-line,var(--border))}
.bitable-calendar-item{padding:8px 12px;border-bottom:1px solid var(--bt-line-soft,var(--border));cursor:pointer;font-size:13px}
.bitable-calendar-item:last-child{border-bottom:none}
.bitable-calendar-item:hover{background:var(--list-hover)}
.bitable-form{max-width:640px;margin:24px auto;padding:0 16px}
.bitable-form-field{margin-bottom:16px}
.bitable-form-field label{display:block;font-size:12px;color:var(--text-3);margin-bottom:6px;font-weight:500}
.bitable-form-field input,.bitable-form-field textarea,.bitable-form-field select{width:100%;box-sizing:border-box;border:1px solid var(--bt-line,var(--border));border-radius:6px;padding:8px 12px;font-size:13px;background:var(--surface);color:var(--text)}
.bitable-search-results{position:absolute;top:100%;left:0;right:0;background:var(--surface);border:1px solid var(--bt-line,var(--border));border-radius:8px;box-shadow:var(--shadow-lg);max-height:240px;overflow-y:auto;z-index:50;font-size:12px}
.bitable-search-results div{padding:8px 12px;cursor:pointer;border-bottom:1px solid var(--bt-line-soft,var(--border))}
.bitable-search-results div:hover{background:var(--list-hover)}
.kb-db-widget-tabs{display:flex;gap:4px;padding:4px 8px;border-bottom:1px solid var(--bt-line,var(--border));background:var(--bt-head,var(--surface-2))}
.kb-db-widget-tab{border:none;background:transparent;padding:4px 10px;font-size:11px;border-radius:6px;cursor:pointer;color:var(--text-3)}
.kb-db-widget-tab.active{background:var(--bt-select,var(--list-active));color:var(--bt-accent,var(--primary));font-weight:500}
.kb-db-link-chip{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:var(--bt-pill-radius,4px);font-size:11px;background:var(--bt-select,#E8F3FF);color:var(--bt-accent,#3370FF);margin:2px 4px 2px 0;cursor:pointer}
.bitable-batch-bar{display:flex;align-items:center;gap:8px;padding:6px 16px;background:var(--bt-select,#E8F3FF);border-bottom:1px solid var(--bt-line,var(--border));font-size:12px;color:var(--bt-accent,#3370FF);flex-shrink:0}
.bitable-modal{position:fixed;inset:0;z-index:10004;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.35)}
.bitable-modal.open{display:flex}
.bitable-modal-card{background:var(--surface);border:1px solid var(--bt-line,var(--border));border-radius:10px;box-shadow:var(--shadow-xl);width:520px;max-width:94vw;padding:14px 16px}
/* Feishu chrome */
#bitable-chrome{display:flex;flex-direction:column;flex-shrink:0;background:var(--surface);border-bottom:1px solid var(--bt-line,var(--border))}
#bitable-chrome-top{display:flex;align-items:center;gap:8px;padding:8px 12px 0;flex-wrap:nowrap;min-height:40px}
#bitable-chrome-tabs{display:flex;align-items:flex-end;gap:0;flex:1;min-width:0;overflow-x:auto;padding:0;scrollbar-width:thin}
#bitable-chrome-actions{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:4px 10px 6px;flex-wrap:nowrap;border-top:1px solid var(--bt-line-soft,#F2F3F5)}
#bitable-chrome-actions .bitable-actions-left{display:flex;align-items:center;gap:2px;flex-wrap:wrap;min-width:0}
#bitable-chrome-actions .bitable-actions-right{display:flex;align-items:center;gap:4px;flex-shrink:0;margin-left:8px}
#bitable-title-input{border:none;background:transparent;font-size:16px;font-weight:600;color:var(--text-strong);outline:none;min-width:80px;max-width:220px;letter-spacing:-.2px;padding:0}
.bitable-view-tab{display:inline-flex;align-items:center;gap:4px;padding:7px 10px;margin:0;border:none;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:var(--text-2);font-size:13px;cursor:pointer;white-space:nowrap;font-weight:400}
.bitable-view-tab:hover{color:var(--text-strong);background:transparent}
.bitable-view-tab.active{color:var(--bt-accent,#3370FF);border-bottom-color:var(--bt-accent,#3370FF);font-weight:500;background:transparent}
.bitable-toolbar-btn{background:transparent;border:none;color:var(--text-2);border-radius:4px;padding:4px 8px;font-size:12px;cursor:pointer;line-height:20px}
.bitable-toolbar-btn:hover{background:var(--bt-line-soft,#F2F3F5);color:var(--text-strong)}
.bitable-toolbar-btn.primary{background:var(--bt-accent,#3370FF);border:none;color:#fff;font-weight:500;margin-left:6px}
.bitable-toolbar-btn.primary:hover{filter:brightness(1.06);background:var(--bt-accent,#3370FF);color:#fff}
.bitable-toolbar-search{position:relative;min-width:120px;max-width:160px;flex-shrink:0}
.bitable-toolbar-search input{border:1px solid var(--bt-line,var(--border));border-radius:6px;padding:4px 8px;font-size:12px;background:var(--surface);color:var(--text);width:100%;box-sizing:border-box;height:28px}
.bitable-table-scroll{overflow:auto;flex:1;min-height:0;display:flex;flex-direction:column;background:var(--bt-canvas,#F5F6F7)}
.bitable-table-scroll .bitable-grid{background:var(--surface,#fff);flex:0 0 auto;min-height:100%}
.bitable-table-footer{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:4px 8px 4px 12px;background:var(--surface,#fff);border:1px solid #DEE0E3;border-top:none;min-height:34px;flex-shrink:0;width:max-content;min-width:100%;box-sizing:border-box;position:sticky;bottom:0;z-index:2;box-shadow:0 -1px 0 #DEE0E3}
.bitable-table-add{border:none;background:transparent;color:var(--text-3);font-size:13px;cursor:pointer;padding:4px 8px;border-radius:4px;display:inline-flex;align-items:center;gap:6px}
.bitable-table-add:hover{background:var(--bt-line-soft,#F2F3F5);color:var(--text-strong)}
.bitable-table-count{font-size:12px;color:var(--text-3);padding-right:8px;font-variant-numeric:tabular-nums}
.bitable-grid tr.bitable-empty-row td{background:var(--surface,#fff)!important;cursor:pointer}
.bitable-grid tr.bitable-empty-row:hover td{background:rgba(51,112,255,.03)!important}
.bitable-grid tr.bitable-empty-row td.row-head{color:transparent}
.bitable-list-item{display:flex;align-items:center;gap:8px;padding:7px 10px;margin:1px 8px;border-radius:6px;cursor:pointer;font-size:13px;color:var(--text-2);transition:background .12s}
.bitable-list-item:hover{background:var(--bt-line-soft,#F2F3F5);color:var(--text)}
.bitable-list-item.active{background:var(--bt-select,#E8F3FF)!important;color:var(--bt-select-fg,#1F2329);font-weight:500;box-shadow:none!important;border-left:none!important}
.bitable-list-item .icon{font-size:15px;opacity:.85;width:20px;text-align:center;flex-shrink:0}
.bitable-list-item .info{flex:1;min-width:0}
.bitable-list-item .title{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bitable-list-item .meta{font-size:11px;color:var(--text-3);margin-top:1px}
.kb-db-pill{display:inline-flex;align-items:center;max-width:100%;padding:1px 8px;border-radius:var(--bt-pill-radius,4px);font-size:12px;line-height:20px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;vertical-align:middle;margin:1px 3px 1px 0;cursor:pointer;border:none;user-select:none}
.kb-db-pill.empty{background:transparent;color:var(--text-3);font-weight:400;padding-left:0;opacity:.7}
.kb-db-pill-wrap{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-start;gap:2px;padding:4px 10px;min-height:36px;box-sizing:border-box;position:relative}
.kb-db-pill-menu{position:absolute;left:8px;top:calc(100% - 2px);z-index:40;min-width:140px;max-width:240px;background:var(--surface);border:1px solid var(--bt-line,var(--border));border-radius:8px;box-shadow:0 8px 24px rgba(31,35,41,.12);padding:4px;max-height:220px;overflow-y:auto}
.kb-db-pill-menu button{display:flex;width:100%;align-items:center;gap:8px;border:none;background:transparent;padding:6px 8px;border-radius:4px;cursor:pointer;font-size:12px;color:var(--text);text-align:left}
.kb-db-pill-menu button:hover{background:var(--bt-line-soft,#F2F3F5)}
.kb-db-progress{display:flex;align-items:center;gap:8px;min-width:80px}
.kb-db-progress-track{flex:1;height:6px;background:var(--bt-line-soft,#F2F3F5);border-radius:999px;overflow:hidden;min-width:48px}
.kb-db-progress-track>span{display:block;height:100%;background:var(--bt-accent,#3370FF);border-radius:999px;transition:width .15s}
.kb-db-progress-pct{font-size:11px;color:var(--text-3);min-width:36px;text-align:right;font-variant-numeric:tabular-nums}
.kb-db-progress-edit{width:100%;margin-top:4px;display:none}
.kb-db-progress.editing .kb-db-progress-edit{display:block}
.bitable-grid tr:hover td{background:var(--bt-line-soft,#F2F3F5)}
.bitable-grid tr:hover td.row-head,.bitable-grid tr:hover td.frozen{background:var(--bt-line-soft,#F2F3F5)!important}
.bitable-kanban{display:flex;gap:12px;padding:14px 16px;height:100%;align-items:flex-start;overflow-x:auto;box-sizing:border-box;background:var(--bt-canvas,#F5F6F7)}
.bitable-kanban-col{min-width:272px;max-width:300px;flex-shrink:0;background:transparent;border:none;border-radius:10px;display:flex;flex-direction:column;max-height:calc(100% - 4px);padding:8px;box-sizing:border-box}
.bitable-kanban-col-head{padding:4px 4px 10px;font-size:13px;font-weight:500;border:none;display:flex;justify-content:space-between;align-items:center;background:transparent;gap:8px}
.bitable-kanban-col-badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:4px;font-size:12px;font-weight:500;line-height:20px}
.bitable-kanban-col-count{font-size:12px;color:var(--text-3);font-variant-numeric:tabular-nums}
.bitable-kanban-col-body{padding:0;display:flex;flex-direction:column;gap:8px;overflow-y:auto;flex:1;min-height:48px}
.bitable-kanban-card{padding:12px;border:1px solid var(--bt-line,#E5E6EB);border-radius:var(--bt-card-radius,8px);background:var(--surface,#fff);cursor:pointer;box-shadow:0 1px 2px rgba(31,35,41,.04);transition:box-shadow .15s,border-color .15s}
.bitable-kanban-card:hover{border-color:#C9CDD4;box-shadow:0 4px 12px rgba(31,35,41,.08)}
.bitable-kanban-card-title{font-weight:600;font-size:13px;margin-bottom:8px;color:var(--text-strong);line-height:1.4;word-break:break-word}
.bitable-kanban-card-pills{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}
.bitable-kanban-card-meta{font-size:12px;color:var(--text-3);line-height:1.45;margin-bottom:6px}
.bitable-kanban-card-progress{margin-top:4px}
.bitable-kanban-add{border:none;background:transparent;color:var(--text-3);border-radius:6px;padding:8px;font-size:16px;cursor:pointer;text-align:left;width:100%;line-height:1}
.bitable-kanban-add:hover{background:rgba(0,0,0,.04);color:var(--text-strong)}
.bitable-kanban-card.dragging{opacity:.45}
.bitable-kanban-col-body.drag-over{outline:2px dashed var(--bt-accent,#3370FF);outline-offset:2px;border-radius:8px;background:rgba(51,112,255,.06)}
`;
  document.head.appendChild(s);
})();

/* ===== DOM HELPERS ===== */
function _ensureBitableDrawer() {
  let el = document.getElementById('bitable-row-drawer');
  if (el) return el;
  el = document.createElement('aside');
  el.id = 'bitable-row-drawer';
  el.innerHTML = `
    <div class="drawer-head">
      <h3 id="bitable-drawer-title">记录详情</h3>
      <button type="button" class="bitable-toolbar-btn" id="bitable-drawer-close">✕</button>
    </div>
    <div class="drawer-body" id="bitable-drawer-body"></div>
    <div class="drawer-foot">
      <button type="button" class="bitable-toolbar-btn" id="bitable-drawer-delete" style="color:var(--rose);margin-right:auto">删除记录</button>
      <button type="button" class="bitable-toolbar-btn primary" id="bitable-drawer-save">保存</button>
    </div>`;
  document.body.appendChild(el);
  el.querySelector('#bitable-drawer-close').onclick = () => _closeBitableDrawer();
  el.querySelector('#bitable-drawer-save').onclick = () => _saveBitableDrawer();
  el.querySelector('#bitable-drawer-delete').onclick = () => _deleteBitableDrawerRow();
  return el;
}

function _ensureFilterModal() {
  let el = document.getElementById('bitable-filter-modal');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'bitable-filter-modal';
  el.innerHTML = `
    <div class="modal-box">
      <div class="modal-head">筛选条件 <span style="font-weight:400;color:var(--text-3);font-size:12px">（AND 组合）</span></div>
      <div class="modal-body" id="bitable-filter-rows"></div>
      <div class="modal-foot">
        <button type="button" class="bitable-toolbar-btn" id="bitable-filter-add">＋ 添加条件</button>
        <button type="button" class="bitable-toolbar-btn" id="bitable-filter-cancel">取消</button>
        <button type="button" class="bitable-toolbar-btn primary" id="bitable-filter-apply">应用</button>
      </div>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => { if (e.target === el) el.classList.remove('open'); });
  el.querySelector('#bitable-filter-cancel').onclick = () => el.classList.remove('open');
  el.querySelector('#bitable-filter-add').onclick = () => _appendFilterRow();
  el.querySelector('#bitable-filter-apply').onclick = () => _applyFilterModal();
  return el;
}

function _closeBitableDrawer() {
  const el = document.getElementById('bitable-row-drawer');
  if (el) el.classList.remove('open');
  _bitableDrawerRow = null;
  _bitableDrawerDbId = null;
}

function _kbHideDbMenu() {
  document.querySelectorAll('.kb-db-menu').forEach(el => el.remove());
}

function _kbShowDbMenu(e, items) {
  _kbHideDbMenu();
  const menu = document.createElement('div');
  menu.className = 'kb-db-menu';
  items.forEach(([label, fn, danger]) => {
    const btn = document.createElement('button');
    btn.textContent = label;
    if (danger) btn.className = 'danger';
    btn.onclick = () => { fn(); _kbHideDbMenu(); };
    menu.appendChild(btn);
  });
  menu.style.left = (e.clientX || 0) + 'px';
  menu.style.top = (e.clientY || 0) + 'px';
  document.body.appendChild(menu);
  requestAnimationFrame(() => {
    document.addEventListener('click', function dismiss(ev) {
      if (!menu.contains(ev.target)) { _kbHideDbMenu(); document.removeEventListener('click', dismiss); }
    });
  });
}

function _kbShowFieldPickerMenu(e, onPick) {
  _kbHideDbMenu();
  const menu = document.createElement('div');
  menu.className = 'kb-db-menu bitable-field-picker';
  KB_FIELD_TYPES.forEach(ft => {
    const btn = document.createElement('button');
    btn.innerHTML = `<span class="fp-icon">${esc(ft.icon)}</span><span>${esc(ft.label)}</span>`;
    btn.onclick = () => { onPick(ft.id); _kbHideDbMenu(); };
    menu.appendChild(btn);
  });
  menu.style.left = (e.clientX || window.innerWidth / 2) + 'px';
  menu.style.top = (e.clientY || 120) + 'px';
  document.body.appendChild(menu);
  requestAnimationFrame(() => {
    document.addEventListener('click', function dismiss(ev) {
      if (!menu.contains(ev.target)) { _kbHideDbMenu(); document.removeEventListener('click', dismiss); }
    });
  });
}

function _kbInvalidateDb(dbId) {
  _kbDbCache.delete(dbId);
}

async function _kbFetchDatabase(dbId, viewId, query) {
  let url = '/api/databases/' + encodeURIComponent(dbId) + '?render=1&ts=' + Date.now();
  if (viewId) url += '&view=' + encodeURIComponent(viewId);
  if (query) url += '&q=' + encodeURIComponent(query);
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(await resp.text());
  const data = await resp.json();
  _kbDbCache.set(dbId, data);
  return data;
}

async function _kbUpdateCell(dbId, rowId, colId, value) {
  _kbInvalidateDb(dbId);
  const resp = await fetch(`/api/databases/${encodeURIComponent(dbId)}/rows/${encodeURIComponent(rowId)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cells: { [colId]: value } }),
  });
  if (!resp.ok) throw new Error(await resp.text());
}

function kbDbColTypeLabel(type) {
  return (KB_FIELD_TYPES.find(t => t.id === type) || {}).label || type;
}

function bitableViewIcon(view) {
  if (!view) return '☰';
  const icons = { kanban: '▦', gallery: '▣', calendar: '📅', form: '📝' };
  return icons[view.type] || '☰';
}

function kbDbFormatDisplay(col, val) {
  if (val === null || val === undefined || val === '') return '';
  const t = col.type || 'text';
  if (t === 'checkbox') return val ? '是' : '否';
  if (t === 'mselect' && Array.isArray(val)) return val.join(', ');
  if (t === 'link' && Array.isArray(val)) return val.map(v => String(v).split(':').pop()).join(', ');
  if (t === 'attachment' && Array.isArray(val)) return val.map(a => a.name || a.url).filter(Boolean).join(', ');
  if (t === 'percent' && val !== null) return val + '%';
  if (t === 'progress' && val !== null) return val + '%';
  if (t === 'currency' && val !== null) return '¥' + val;
  if (t === 'rating' && val !== null) return '★'.repeat(Number(val) || 0);
  return String(val);
}

function _optionPillPair(colorKey) {
  const key = String(colorKey || '');
  return KB_SELECT_PILLS[key] || { bg: '#F2F3F5', fg: '#646A73' };
}

function _findOption(col, val) {
  if (!col || val == null || val === '') return null;
  return (col.options || []).find(o => o.name === val) || null;
}

function _optionPillStyle(opt) {
  if (!opt) return { bg: '#F2F3F5', fg: '#8F959E' };
  return _optionPillPair(opt.color);
}

function _optionColorStyle(col, val) {
  // Legacy: used for conditional row tint; keep solid accent soft wash.
  const opt = _findOption(col, val);
  if (!opt || !opt.color) return '';
  const c = KB_SELECT_COLORS[String(opt.color)] || '';
  return c ? `background:${c}14` : '';
}

function _makePillEl(label, opt, { empty } = {}) {
  const pill = document.createElement('span');
  pill.className = 'kb-db-pill' + (empty ? ' empty' : '');
  pill.textContent = label || '选择';
  if (!empty && opt) {
    const pair = _optionPillStyle(opt);
    pill.style.background = pair.bg;
    pill.style.color = pair.fg;
  }
  return pill;
}

function _closeAllPillMenus(except) {
  document.querySelectorAll('.kb-db-pill-menu').forEach(m => {
    if (m !== except) m.remove();
  });
}

function _openPillMenu(anchorWrap, options, { multi, selected, onPick }) {
  _closeAllPillMenus();
  const menu = document.createElement('div');
  menu.className = 'kb-db-pill-menu';
  const sel = multi
    ? (Array.isArray(selected) ? selected.slice() : (selected ? [String(selected)] : []))
    : selected;
  const addBtn = (label, opt, isOn) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    const pill = _makePillEl(label, opt, { empty: !opt });
    btn.appendChild(pill);
    if (isOn) {
      const check = document.createElement('span');
      check.textContent = '✓';
      check.style.cssText = 'margin-left:auto;color:var(--bt-accent,#3370FF);font-size:12px';
      btn.appendChild(check);
    }
    btn.onclick = (e) => {
      e.stopPropagation();
      onPick(label, opt);
      if (!multi) menu.remove();
    };
    menu.appendChild(btn);
  };
  if (!multi) addBtn('（空）', null, !sel);
  (options || []).forEach(o => {
    const on = multi ? sel.includes(o.name) : sel === o.name;
    addBtn(o.name, o, on);
  });
  anchorWrap.appendChild(menu);
  const dismiss = (ev) => {
    if (!menu.contains(ev.target) && ev.target !== anchorWrap && !anchorWrap.contains(ev.target)) {
      menu.remove();
      document.removeEventListener('mousedown', dismiss, true);
    }
  };
  setTimeout(() => document.addEventListener('mousedown', dismiss, true), 0);
  return menu;
}

function _applyConditionalFormat(tr, row, view, columns) {
  const formats = view?.conditionalFormats || [];
  formats.forEach(fmt => {
    const col = columns.find(c => c.id === fmt.column);
    if (!col) return;
    const val = (row.cells || {})[col.id];
    if (fmt.op === 'eq' && val === fmt.value && fmt.color) {
      const pair = _optionPillPair(fmt.color);
      tr.style.background = pair.bg;
    }
  });
}

function _applyBitablePageChrome() {
  const titleEl = document.getElementById('bitable-page-title');
  const sidebarTitle = document.querySelector('#bitable-sidebar-header .title');
  const emptyTitle = document.querySelector('#bitable-empty h3');
  const emptyDesc = document.querySelector('#bitable-empty p');
  const createBtn = document.querySelector('#bitable-topbar-right .sy-btn-primary');
  const sidebarCreate = document.querySelector('#bitable-sidebar-header .sy-btn-primary');
  const isKanban = bitablePageMode === 'kanban';
  if (titleEl) titleEl.textContent = isKanban ? '看板' : '多维表格';
  if (sidebarTitle) sidebarTitle.textContent = isKanban ? '全部看板' : '全部表格';
  if (emptyTitle) emptyTitle.textContent = isKanban ? '看板' : '多维表格';
  if (emptyDesc) {
    emptyDesc.textContent = isKanban
      ? '按分组字段管理任务与记录，支持拖拽与筛选'
      : '类似飞书多维表格：独立管理结构化数据，支持表格视图与看板视图';
  }
  if (createBtn) createBtn.style.display = isKanban ? 'none' : '';
  if (sidebarCreate) sidebarCreate.style.display = isKanban ? 'none' : '';
}

/* ===== CELL BUILDER ===== */
function kbDbBuildCell(dbId, col, row, opts) {
  opts = opts || {};
  const td = document.createElement('td');
  td.dataset.colId = col.id;
  td.dataset.rowId = row.id;
  const val = (row.cells || {})[col.id];
  const t = col.type || 'text';
  const readonly = KB_READONLY_TYPES.has(t) || opts.readonly;
  const save = (v) => _kbUpdateCell(dbId, row.id, col.id, v)
    .then(() => { if (opts.onSave) opts.onSave(v); })
    .catch(e => toast('保存失败', 'error'));

  if (readonly) {
    const inner = document.createElement('div');
    inner.className = 'cell-inner readonly-val';
    inner.textContent = kbDbFormatDisplay(col, val);
    td.appendChild(inner);
    return td;
  }

  if (t === 'checkbox') {
    const inner = document.createElement('div');
    inner.className = 'cell-inner';
    inner.style.textAlign = 'center';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = !!val;
    cb.onchange = () => save(cb.checked);
    inner.appendChild(cb);
    td.appendChild(inner);
  } else if (t === 'select') {
    const wrap = document.createElement('div');
    wrap.className = 'kb-db-pill-wrap';
    const paint = (cur) => {
      wrap.innerHTML = '';
      const opt = _findOption(col, cur);
      const pill = _makePillEl(cur || '选择', opt, { empty: !cur });
      wrap.appendChild(pill);
      wrap.onclick = (e) => {
        e.stopPropagation();
        _openPillMenu(wrap, col.options || [], {
          multi: false,
          selected: cur || '',
          onPick: (name) => {
            const next = name === '（空）' ? '' : name;
            save(next || null);
            paint(next);
          },
        });
      };
    };
    paint(val || '');
    td.appendChild(wrap);
  } else if (t === 'mselect') {
    const wrap = document.createElement('div');
    wrap.className = 'kb-db-pill-wrap';
    let selected = Array.isArray(val) ? val.slice() : (val ? [String(val)] : []);
    const paint = () => {
      wrap.innerHTML = '';
      if (!selected.length) {
        wrap.appendChild(_makePillEl('选择', null, { empty: true }));
      } else {
        selected.forEach(name => {
          wrap.appendChild(_makePillEl(name, _findOption(col, name)));
        });
      }
    };
    wrap.onclick = (e) => {
      e.stopPropagation();
      _openPillMenu(wrap, col.options || [], {
        multi: true,
        selected,
        onPick: (name) => {
          if (selected.includes(name)) selected = selected.filter(x => x !== name);
          else selected = [...selected, name];
          save(selected.slice());
          paint();
          _closeAllPillMenus();
        },
      });
    };
    paint();
    td.appendChild(wrap);
  } else if (t === 'progress') {
    const wrap = document.createElement('div');
    wrap.className = 'cell-inner';
    const pct = val === null || val === undefined ? 0 : Number(val) || 0;
    const max = Number(col.max || 100) || 100;
    const box = document.createElement('div');
    box.className = 'kb-db-progress';
    box.innerHTML = `<div class="kb-db-progress-track"><span style="width:${Math.min(100, (pct / max) * 100)}%"></span></div><span class="kb-db-progress-pct">${esc(String(pct))}%</span>`;
    const inp = document.createElement('input');
    inp.type = 'range';
    inp.className = 'kb-db-progress-edit';
    inp.min = '0';
    inp.max = String(max);
    inp.value = String(pct);
    inp.onclick = (e) => e.stopPropagation();
    inp.onchange = () => {
      const n = Number(inp.value) || 0;
      save(n);
      box.querySelector('.kb-db-progress-track > span').style.width = Math.min(100, (n / max) * 100) + '%';
      box.querySelector('.kb-db-progress-pct').textContent = n + '%';
      box.classList.remove('editing');
    };
    box.appendChild(inp);
    box.onclick = (e) => {
      e.stopPropagation();
      box.classList.toggle('editing');
    };
    wrap.appendChild(box);
    td.appendChild(wrap);
  } else if (t === 'rating') {
    const wrap = document.createElement('div');
    wrap.className = 'cell-inner kb-db-rating';
    const max = col.max || 5;
    let cur = Number(val) || 0;
    const paint = () => {
      wrap.innerHTML = '';
      for (let i = 1; i <= max; i++) {
        const star = document.createElement('span');
        star.textContent = '★';
        if (i > cur) star.className = 'dim';
        star.onclick = (e) => {
          e.stopPropagation();
          cur = (i === cur) ? 0 : i;
          save(cur || null);
          paint();
        };
        wrap.appendChild(star);
      }
    };
    paint();
    td.appendChild(wrap);
  } else if (t === 'date' || t === 'datetime') {
    const inp = document.createElement('input');
    inp.type = t === 'datetime' ? 'datetime-local' : 'date';
    inp.value = val ? String(val).slice(0, t === 'datetime' ? 16 : 10) : '';
    inp.style.cssText = 'width:100%;border:none;background:transparent;font-size:13px;padding:6px 10px;outline:none';
    inp.onchange = () => save(inp.value);
    td.appendChild(inp);
  } else if (t === 'url' || t === 'email' || t === 'phone') {
    const inner = document.createElement('div');
    inner.className = 'cell-inner';
    inner.contentEditable = 'true';
    inner.textContent = val === null || val === undefined ? '' : String(val);
    inner.onblur = () => save(inner.textContent.trim());
    td.appendChild(inner);
  } else if (t === 'attachment') {
    const wrap = document.createElement('div');
    wrap.className = 'cell-inner';
    const items = Array.isArray(val) ? val : [];
    items.forEach(a => {
      const link = document.createElement('a');
      link.href = a.url || '#';
      link.textContent = a.name || a.url || '附件';
      link.target = '_blank';
      link.style.cssText = 'display:block;font-size:12px;color:var(--primary);margin:2px 0';
      wrap.appendChild(link);
    });
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.textContent = '＋ 添加链接';
    addBtn.style.cssText = 'border:none;background:none;color:var(--primary);cursor:pointer;font-size:11px;padding:4px 0';
    addBtn.onclick = async () => {
      const url = await uiPrompt('附件 URL', '链接', 'https://');
      if (url === null || !url.trim()) return;
      const name = await uiPrompt('显示名称', '名称', url.trim().split('/').pop() || '附件');
      save([...items, { name: (name || url).trim(), url: url.trim() }]);
      if (opts.onRefresh) opts.onRefresh();
    };
    const upBtn = document.createElement('button');
    upBtn.type = 'button';
    upBtn.textContent = '⬆ 上传文件';
    upBtn.style.cssText = 'border:none;background:none;color:var(--primary);cursor:pointer;font-size:11px;padding:4px 8px 4px 0';
    upBtn.onclick = () => {
      const input = document.createElement('input');
      input.type = 'file';
      input.onchange = async () => {
        const file = input.files && input.files[0];
        if (!file) return;
        const fd = new FormData();
        fd.append('file', file);
        try {
          const resp = await fetch(`/api/databases/${encodeURIComponent(dbId)}/attachments`, { method: 'POST', body: fd });
          if (!resp.ok) throw new Error(await resp.text());
          const att = await resp.json();
          save([...items, { name: att.name || file.name, url: att.url }]);
          if (opts.onRefresh) opts.onRefresh();
        } catch (e) {
          toast('上传失败', 'error');
        }
      };
      input.click();
    };
    wrap.appendChild(addBtn);
    wrap.appendChild(upBtn);
    td.appendChild(wrap);
  } else if (t === 'link') {
    const wrap = document.createElement('div');
    wrap.className = 'cell-inner';
    const refs = Array.isArray(val) ? val : [];
    refs.forEach(ref => {
      const chip = document.createElement('span');
      chip.className = 'kb-db-link-chip';
      chip.textContent = String(ref).split(':').pop() || ref;
      chip.title = ref;
      chip.onclick = (e) => { e.stopPropagation(); _openLinkRef(ref); };
      wrap.appendChild(chip);
    });
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.textContent = '＋ 关联';
    addBtn.style.cssText = 'border:none;background:none;color:var(--primary);cursor:pointer;font-size:11px';
    addBtn.onclick = async (e) => {
      e.stopPropagation();
      const picked = await _pickLinkRecord(col.linkDatabase);
      if (!picked) return;
      const ref = `${picked.dbId}:${picked.rowId}`;
      if (!refs.includes(ref)) save([...refs, ref]);
      if (opts.onRefresh) opts.onRefresh();
    };
    wrap.appendChild(addBtn);
    td.appendChild(wrap);
  } else if (t === 'ai_text') {
    const wrap = document.createElement('div');
    wrap.className = 'cell-inner';
    wrap.textContent = val === null || val === undefined ? '' : String(val);
    wrap.style.minHeight = '32px';
    const genBtn = document.createElement('button');
    genBtn.type = 'button';
    genBtn.textContent = '✨ AI 生成';
    genBtn.style.cssText = 'border:none;background:var(--primary-subtle);color:var(--primary);cursor:pointer;font-size:11px;padding:2px 8px;border-radius:999px;margin-top:4px';
    genBtn.onclick = async (e) => {
      e.stopPropagation();
      genBtn.disabled = true;
      genBtn.textContent = '生成中…';
      try {
        const resp = await fetch(`/api/databases/${encodeURIComponent(dbId)}/ai-generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rowId: row.id, columnId: col.id }),
        });
        if (!resp.ok) throw new Error(await resp.text());
        const data = await resp.json();
        wrap.textContent = data.text || '';
        if (opts.onRefresh) opts.onRefresh();
        toast('AI 生成完成', 'success');
      } catch (err) {
        toast('AI 生成失败', 'error');
      } finally {
        genBtn.disabled = false;
        genBtn.textContent = '✨ AI 生成';
      }
    };
    td.appendChild(wrap);
    td.appendChild(genBtn);
  } else if (t === 'longtext') {
    const ta = document.createElement('textarea');
    ta.value = val === null || val === undefined ? '' : String(val);
    ta.style.cssText = 'width:100%;min-height:48px;border:none;background:transparent;font-size:13px;padding:6px 10px;resize:vertical;outline:none;font-family:inherit';
    ta.onblur = () => save(ta.value);
    td.appendChild(ta);
  } else if (t === 'number' || t === 'currency' || t === 'percent') {
    const inner = document.createElement('div');
    inner.className = 'cell-inner';
    inner.contentEditable = 'true';
    inner.textContent = val === null || val === undefined ? '' : String(val);
    inner.onblur = () => {
      const raw = inner.textContent.trim();
      save(raw === '' ? null : Number(raw));
    };
    td.appendChild(inner);
  } else {
    const inner = document.createElement('div');
    inner.className = 'cell-inner';
    inner.contentEditable = 'true';
    inner.textContent = val === null || val === undefined ? '' : String(val);
    inner.onblur = () => save(inner.textContent.trim());
    td.appendChild(inner);
  }

  td.ondblclick = (e) => {
    if (e.target.closest('button,input,select,textarea')) return;
    bitableOpenRowDrawer(dbId, row, opts.allColumns || [col]);
  };
  return td;
}

async function _openLinkRef(ref) {
  const parts = String(ref).split(':');
  if (parts.length < 2) return;
  const dbId = parts[0];
  const rowId = parts.slice(1).join(':');
  try {
    const data = await _kbFetchDatabase(dbId);
    const row = (data.rows || []).find(r => r.id === rowId);
    if (row) {
      openBitableDatabase(dbId);
      setTimeout(() => bitableOpenRowDrawer(dbId, row, data.allColumns || data.columns), 300);
    }
  } catch (e) {
    toast('无法打开关联记录', 'error');
  }
}

async function _pickLinkRecord(linkDbId) {
  if (!linkDbId) {
    toast('请先在字段设置中配置关联表', 'error');
    return null;
  }
  try {
    const data = await _kbFetchDatabase(linkDbId);
    const titleCol = (data.columns || [])[0];
    const names = (data.rows || []).map(r => {
      const title = titleCol ? ((r.cells || {})[titleCol.id] || r.id) : r.id;
      return `${title} (${r.id})`;
    });
    if (!names.length) {
      toast('关联表无记录', 'error');
      return null;
    }
    const pick = await uiPrompt('选择关联记录', '输入序号或记录 ID', names[0]);
    if (pick === null) return null;
    const row = (data.rows || []).find(r => r.id === pick.trim() || names.some((n, i) => n.startsWith(pick.trim()) && data.rows[i].id === r.id));
    if (!row) {
      const idx = parseInt(pick, 10);
      if (idx >= 1 && idx <= data.rows.length) return { dbId: linkDbId, rowId: data.rows[idx - 1].id };
      toast('未找到记录', 'error');
      return null;
    }
    return { dbId: linkDbId, rowId: row.id };
  } catch (e) {
    toast('加载关联表失败', 'error');
    return null;
  }
}

/* ===== ROW DRAWER ===== */
function bitableOpenRowDrawer(dbId, row, columns) {
  _ensureBitableDrawer();
  _bitableDrawerDbId = dbId;
  _bitableDrawerRow = row;
  const drawer = document.getElementById('bitable-row-drawer');
  const titleCol = (columns || [])[0];
  const title = titleCol ? ((row.cells || {})[titleCol.id] || '未命名') : row.id;
  document.getElementById('bitable-drawer-title').textContent = String(title);
  const body = document.getElementById('bitable-drawer-body');
  body.innerHTML = '';
  (columns || []).forEach(col => {
    const field = document.createElement('div');
    field.className = 'drawer-field';
    field.dataset.colId = col.id;
    const label = document.createElement('label');
    label.textContent = col.name + ' · ' + kbDbColTypeLabel(col.type);
    field.appendChild(label);
    const val = (row.cells || {})[col.id];
    const t = col.type || 'text';
    if (KB_READONLY_TYPES.has(t)) {
      const ro = document.createElement('div');
      ro.className = 'readonly-val';
      ro.textContent = kbDbFormatDisplay(col, val);
      field.appendChild(ro);
    } else if (t === 'checkbox') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = !!val;
      cb.dataset.field = '1';
      field.appendChild(cb);
    } else if (t === 'select') {
      const sel = document.createElement('select');
      sel.dataset.field = '1';
      sel.innerHTML = '<option value=""></option>' + (col.options || []).map(o =>
        `<option value="${esc(o.name)}"${val === o.name ? ' selected' : ''}>${esc(o.name)}</option>`
      ).join('');
      field.appendChild(sel);
    } else if (t === 'mselect') {
      const wrap = document.createElement('div');
      const selected = Array.isArray(val) ? val.slice() : [];
      (col.options || []).forEach(o => {
        const tag = document.createElement('label');
        tag.style.cssText = 'display:inline-flex;align-items:center;gap:4px;margin-right:10px;font-size:12px;cursor:pointer';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = selected.includes(o.name);
        cb.dataset.opt = o.name;
        cb.dataset.field = 'mselect';
        tag.appendChild(cb);
        tag.appendChild(document.createTextNode(o.name));
        wrap.appendChild(tag);
      });
      field.appendChild(wrap);
    } else if (t === 'longtext') {
      const ta = document.createElement('textarea');
      ta.rows = 4;
      ta.value = val === null || val === undefined ? '' : String(val);
      ta.dataset.field = '1';
      field.appendChild(ta);
    } else if (t === 'date' || t === 'datetime') {
      const inp = document.createElement('input');
      inp.type = t === 'datetime' ? 'datetime-local' : 'date';
      inp.value = val ? String(val).slice(0, t === 'datetime' ? 16 : 10) : '';
      inp.dataset.field = '1';
      field.appendChild(inp);
    } else if (t === 'ai_text') {
      const ta = document.createElement('textarea');
      ta.rows = 4;
      ta.value = val === null || val === undefined ? '' : String(val);
      ta.dataset.field = '1';
      field.appendChild(ta);
      const genBtn = document.createElement('button');
      genBtn.type = 'button';
      genBtn.className = 'bitable-toolbar-btn';
      genBtn.textContent = '✨ AI 生成';
      genBtn.style.marginTop = '6px';
      genBtn.onclick = async () => {
        genBtn.disabled = true;
        try {
          const resp = await fetch(`/api/databases/${encodeURIComponent(dbId)}/ai-generate`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rowId: row.id, columnId: col.id }),
          });
          if (!resp.ok) throw new Error(await resp.text());
          const data = await resp.json();
          ta.value = data.text || '';
          toast('AI 生成完成', 'success');
        } catch (e) {
          toast('AI 生成失败', 'error');
        } finally {
          genBtn.disabled = false;
        }
      };
      field.appendChild(genBtn);
    } else if (t === 'link') {
      const refs = Array.isArray(val) ? val : [];
      const wrap = document.createElement('div');
      refs.forEach(ref => {
        const chip = document.createElement('span');
        chip.className = 'kb-db-link-chip';
        chip.textContent = String(ref);
        wrap.appendChild(chip);
      });
      const addBtn = document.createElement('button');
      addBtn.type = 'button';
      addBtn.className = 'bitable-toolbar-btn';
      addBtn.textContent = '＋ 添加关联';
      addBtn.style.marginTop = '6px';
      addBtn.onclick = async () => {
        const picked = await _pickLinkRecord(col.linkDatabase);
        if (picked) {
          const ref = `${picked.dbId}:${picked.rowId}`;
          if (!refs.includes(ref)) refs.push(ref);
          wrap.innerHTML = '';
          refs.forEach(r => {
            const c = document.createElement('span');
            c.className = 'kb-db-link-chip';
            c.textContent = r;
            wrap.insertBefore(c, addBtn);
          });
        }
      };
      wrap.appendChild(addBtn);
      wrap.dataset.field = 'link';
      wrap.dataset.refs = JSON.stringify(refs);
      field.appendChild(wrap);
    } else {
      const inp = document.createElement('input');
      inp.type = 'text';
      inp.value = val === null || val === undefined ? '' : String(val);
      inp.dataset.field = '1';
      field.appendChild(inp);
    }
    body.appendChild(field);
  });
  drawer.classList.add('open');
}

async function _saveBitableDrawer() {
  if (!_bitableDrawerDbId || !_bitableDrawerRow) return;
  const body = document.getElementById('bitable-drawer-body');
  const cells = {};
  body.querySelectorAll('.drawer-field').forEach(field => {
    const colId = field.dataset.colId;
    const msel = field.querySelector('input[data-field="mselect"]');
    if (msel) {
      cells[colId] = [...field.querySelectorAll('input[data-field="mselect"]:checked')].map(cb => cb.dataset.opt);
      return;
    }
    const linkWrap = field.querySelector('[data-field="link"]');
    if (linkWrap) {
      try { cells[colId] = JSON.parse(linkWrap.dataset.refs || '[]'); } catch (e) { cells[colId] = []; }
      return;
    }
    const cb = field.querySelector('input[type="checkbox"][data-field]');
    if (cb) { cells[colId] = cb.checked; return; }
    const sel = field.querySelector('select[data-field]');
    if (sel) { cells[colId] = sel.value; return; }
    const ta = field.querySelector('textarea[data-field]');
    if (ta) { cells[colId] = ta.value; return; }
    const inp = field.querySelector('input[data-field]');
    if (inp) { cells[colId] = inp.value; return; }
  });
  try {
    await fetch(`/api/databases/${encodeURIComponent(_bitableDrawerDbId)}/rows/${encodeURIComponent(_bitableDrawerRow.id)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cells }),
    });
    _kbInvalidateDb(_bitableDrawerDbId);
    _closeBitableDrawer();
    await bitableRefreshEditor();
    toast('已保存', 'success');
  } catch (e) {
    toast('保存失败', 'error');
  }
}

async function _deleteBitableDrawerRow() {
  if (!_bitableDrawerDbId || !_bitableDrawerRow) return;
  if (!(await uiConfirm('确定删除此记录？'))) return;
  try {
    await fetch(`/api/databases/${encodeURIComponent(_bitableDrawerDbId)}/rows/${encodeURIComponent(_bitableDrawerRow.id)}`, { method: 'DELETE' });
    _closeBitableDrawer();
    await bitableRefreshEditor();
    toast('已删除', 'success');
  } catch (e) {
    toast('删除失败', 'error');
  }
}

function bitableEditRowDialog(dbId, row, columns) {
  bitableOpenRowDrawer(dbId, row, columns);
}

/* ===== FILTER BUILDER ===== */
function _appendFilterRow(container, filter) {
  const host = container || document.getElementById('bitable-filter-rows');
  if (!host) return;
  const cols = bitableRenderCache?.columns || [];
  const row = document.createElement('div');
  row.className = 'bitable-filter-row';
  const colSel = document.createElement('select');
  colSel.className = 'filter-col';
  cols.forEach(c => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = c.name;
    if (filter && filter.column === c.id) opt.selected = true;
    colSel.appendChild(opt);
  });
  const opSel = document.createElement('select');
  opSel.className = 'filter-op';
  KB_FILTER_OPS.forEach(o => {
    const opt = document.createElement('option');
    opt.value = o.id;
    opt.textContent = o.label;
    if (filter && filter.op === o.id) opt.selected = true;
    opSel.appendChild(opt);
  });
  const valInp = document.createElement('input');
  valInp.className = 'filter-val';
  valInp.placeholder = '值';
  valInp.value = (filter && filter.value) || '';
  const rmBtn = document.createElement('button');
  rmBtn.type = 'button';
  rmBtn.className = 'bitable-toolbar-btn';
  rmBtn.textContent = '×';
  rmBtn.onclick = () => row.remove();
  row.appendChild(colSel);
  row.appendChild(opSel);
  row.appendChild(valInp);
  row.appendChild(rmBtn);
  host.appendChild(row);
}

function _kbShowFilterBuilder() {
  _ensureFilterModal();
  const modal = document.getElementById('bitable-filter-modal');
  const host = document.getElementById('bitable-filter-rows');
  host.innerHTML = '';
  const existing = bitableRenderCache?.view?.filters || [];
  if (existing.length) existing.forEach(f => _appendFilterRow(host, f));
  else _appendFilterRow(host);
  modal.classList.add('open');
}

async function _applyFilterModal() {
  const host = document.getElementById('bitable-filter-rows');
  const filters = [];
  host.querySelectorAll('.bitable-filter-row').forEach(row => {
    const column = row.querySelector('.filter-col')?.value;
    const op = row.querySelector('.filter-op')?.value || 'contains';
    const value = row.querySelector('.filter-val')?.value || '';
    if (column) filters.push({ column, op, value: value.trim() });
  });
  document.getElementById('bitable-filter-modal').classList.remove('open');
  await bitableUpdateView({ filters });
}

/* ===== NAVIGATION ===== */
function switchToBitableView(dbId) {
  TabManager.openTab('bitable-main', 'bitable', '📊 多维表格', { dbId: dbId || null }, true);
}

function switchToKanbanView(dbId) {
  TabManager.openTab('kanban-main', 'kanban', '📋 看板', { dbId: dbId || null }, true);
}

function backFromBitableView() {
  const tabId = TabManager.activeTabId;
  if (tabId === 'bitable-main' || tabId === 'kanban-main') {
    TabManager.closeTab(tabId);
  } else {
    TabManager.activateTab('home');
  }
}

async function loadBitableList(selectId) {
  try {
    let url = '/api/databases?ts=' + Date.now();
    if (bitableSearchQuery) url += '&search=' + encodeURIComponent(bitableSearchQuery);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    if (data.results) {
      _renderCrossSearchResults(data.results);
      return;
    }
    bitableListCache = data.databases || [];
    renderBitableList();
    if (selectId) {
      openBitableDatabase(selectId);
    } else if (bitableCurrentId) {
      openBitableDatabase(bitableCurrentId);
    } else if (bitableListCache.length) {
      openBitableDatabase(bitableListCache[0].id);
    } else {
      const empty = document.getElementById('bitable-empty');
      const editor = document.getElementById('bitable-editor');
      if (empty) empty.style.display = '';
      if (editor) editor.style.display = 'none';
    }
  } catch (e) {
    toast('加载多维表格失败：' + (e.message || e), 'error');
  }
}

function _renderCrossSearchResults(results) {
  const host = document.getElementById('bitable-list');
  if (!host) return;
  if (!results.length) {
    host.innerHTML = '<div style="padding:20px 14px;font-size:12px;color:var(--text-3)">无匹配结果</div>';
    return;
  }
  host.innerHTML = results.map(r => `
    <div class="bitable-list-item" onclick="openBitableDatabase('${esc(r.database_id)}')">
      <span class="icon">🔍</span>
      <div class="info">
        <div class="title">${esc(r.database_name || r.database_id)}</div>
        <div class="meta">${esc(r.snippet || '')}</div>
      </div>
    </div>`).join('');
}

function renderBitableList() {
  const host = document.getElementById('bitable-list');
  if (!host) return;
  const items = bitableListCache.slice();
  if (!items.length) {
    host.innerHTML = `<div style="padding:20px 14px;font-size:12px;color:var(--text-3)">${bitablePageMode === 'kanban' ? '暂无表格，请先在多维表格中创建并添加看板视图' : '暂无表格，点击 ＋ 创建'}</div>`;
    return;
  }
  host.innerHTML = items.map(db => `
    <div class="bitable-list-item${db.id === bitableCurrentId ? ' active' : ''}" onclick="openBitableDatabase('${esc(db.id)}')">
      <span class="icon">📄</span>
      <div class="info">
        <div class="title">${esc(db.name || 'Untitled')}</div>
      </div>
    </div>`).join('');
}

async function bitableCreateDatabase() {
  const name = await uiPrompt('新建多维表格', '表格名称', '未命名表格');
  if (name === null) return;
  try {
    const resp = await fetch('/api/databases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() || '未命名表格' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const db = await resp.json();
    bitableSearchQuery = '';
    await loadBitableList(db.id);
    toast('已创建多维表格', 'success');
  } catch (e) {
    toast('创建失败：' + (e.message || e), 'error');
  }
}

async function openBitableDatabase(dbId) {
  bitableCurrentId = dbId;
  bitableSelectedRows.clear();
  renderBitableList();
  document.getElementById('bitable-empty').style.display = 'none';
  document.getElementById('bitable-editor').style.display = 'flex';
  const content = document.getElementById('bitable-content');
  if (content) content.innerHTML = '<div class="kb-db-loading" style="padding:40px;text-align:center">加载中…</div>';
  try {
    _kbInvalidateDb(dbId);
    const data = await _kbFetchDatabase(dbId, null, bitableSearchQuery);
    bitableRenderCache = data;
    if (bitablePageMode === 'kanban') {
      const views = data.views || [data.view].filter(Boolean);
      const kanbanView = views.find(v => v.type === 'kanban');
      if (kanbanView && data.view?.id !== kanbanView.id) {
        await bitableSwitchView(kanbanView.id);
        return;
      }
    }
    renderBitableEditor(data);
  } catch (e) {
    if (content) content.innerHTML = '<div class="kb-db-error" style="padding:40px">加载失败: ' + esc(e.message || e) + '</div>';
  }
}

async function bitableRefreshEditor() {
  if (!bitableCurrentId) return;
  _kbInvalidateDb(bitableCurrentId);
  const viewId = bitableRenderCache?.view?.id;
  const url = '/api/databases/' + encodeURIComponent(bitableCurrentId) + '?render=1' +
    (viewId ? '&view=' + encodeURIComponent(viewId) : '') +
    (bitableSearchQuery ? '&q=' + encodeURIComponent(bitableSearchQuery) : '');
  const resp = await fetch(url + '&ts=' + Date.now());
  if (!resp.ok) throw new Error(await resp.text());
  bitableRenderCache = await resp.json();
  renderBitableEditor(bitableRenderCache);
  await loadBitableList();
}

function _bitableGroupableColumns(data) {
  return (data.columns || []).filter(c => KB_FIELD_TYPES.some(ft => ft.id === c.type && ft.kanbanGroup));
}

function renderBitableEditor(data) {
  const toolbar = document.getElementById('bitable-toolbar');
  const filterBar = document.getElementById('bitable-filter-bar');
  const content = document.getElementById('bitable-content');
  if (!toolbar || !content) return;

  const views = data.views || [data.view].filter(Boolean);
  const activeView = data.view || views[0];
  const batchCount = bitableSelectedRows.size;

  toolbar.innerHTML = `
    <div id="bitable-chrome">
      <div id="bitable-chrome-top">
        <span style="font-size:22px;line-height:1">${esc(data.icon || '📊')}</span>
        <input id="bitable-title-input" value="${esc(data.name || 'Untitled')}" title="表格名称">
        <div class="bitable-toolbar-search" id="bitable-global-search-wrap">
          <input id="bitable-global-search" placeholder="搜索…" value="${esc(bitableSearchQuery)}">
          <div class="bitable-search-results" id="bitable-search-dropdown" style="display:none"></div>
        </div>
        <div id="bitable-chrome-tabs">${views.map(v => `
          <span class="bitable-view-tab${v.id === activeView.id ? ' active' : ''}"
            onclick="bitableSwitchView('${esc(v.id)}')"
            oncontextmenu="bitableShowViewContextMenu(event,'${esc(v.id)}')"
            title="${esc(v.type || 'table')}">${bitableViewIcon(v)} ${esc(v.name)}</span>
        `).join('')}
          <button type="button" class="bitable-toolbar-btn" onclick="bitableAddViewMenu(event)" title="添加视图">＋</button>
        </div>
      </div>
      <div id="bitable-chrome-actions">
        <div class="bitable-actions-left">
          ${activeView.type === 'kanban' ? '<button class="bitable-toolbar-btn" onclick="bitableConfigureKanbanView()">分组依据</button>' : ''}
          ${activeView.type === 'gallery' ? '<button class="bitable-toolbar-btn" onclick="bitableConfigureGalleryView()">封面字段</button>' : ''}
          <button class="bitable-toolbar-btn" onclick="bitableShowFilterDialog()">筛选</button>
          <button class="bitable-toolbar-btn" onclick="bitableShowSortDialog()">排序</button>
          <button class="bitable-toolbar-btn" onclick="bitableAddColumnPrompt(event)">字段配置</button>
          <button class="bitable-toolbar-btn" onclick="bitableSearch()">表内搜索</button>
          <button class="bitable-toolbar-btn" onclick="bitableExportCsv()">导出</button>
          <button class="bitable-toolbar-btn" onclick="bitableImportCsv()">导入</button>
          <button class="bitable-toolbar-btn" onclick="bitableShowHistory()">历史</button>
          ${batchCount ? `<button class="bitable-toolbar-btn" style="color:var(--rose)" onclick="bitableBatchDelete()">删除 ${batchCount} 条</button>` : ''}
        </div>
        <div class="bitable-actions-right">
          <button class="bitable-toolbar-btn primary" onclick="bitableAddRow()">＋ 新建记录</button>
          <button class="bitable-toolbar-btn" onclick="bitableDeleteDatabase()" title="删除表格" style="color:var(--rose)">🗑</button>
        </div>
      </div>
    </div>`;

  document.getElementById('bitable-title-input').addEventListener('change', async (e) => {
    try {
      await fetch(`/api/databases/${encodeURIComponent(data.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: e.target.value.trim() || 'Untitled' }),
      });
      _kbInvalidateDb(data.id);
      await loadBitableList();
    } catch (err) {
      toast('重命名失败', 'error');
    }
  });

  const searchInp = document.getElementById('bitable-global-search');
  const searchDrop = document.getElementById('bitable-search-dropdown');
  let searchTimer = null;
  searchInp.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const q = searchInp.value.trim();
      if (!q) { searchDrop.style.display = 'none'; return; }
      try {
        const resp = await fetch('/api/databases?search=' + encodeURIComponent(q));
        const payload = await resp.json();
        const results = payload.results || [];
        if (!results.length) {
          searchDrop.innerHTML = '<div style="padding:10px;color:var(--text-3)">无结果</div>';
        } else {
          searchDrop.innerHTML = results.map(r =>
            `<div data-db="${esc(r.database_id)}" data-row="${esc(r.row_id || '')}">${esc(r.database_name)} — ${esc(r.snippet || '')}</div>`
          ).join('');
          searchDrop.querySelectorAll('div[data-db]').forEach(el => {
            el.onclick = () => {
              searchDrop.style.display = 'none';
              openBitableDatabase(el.dataset.db);
            };
          });
        }
        searchDrop.style.display = 'block';
      } catch (e) { searchDrop.style.display = 'none'; }
    }, 300);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#bitable-global-search-wrap')) searchDrop.style.display = 'none';
  });

  const filters = activeView.filters || [];
  if (filterBar) {
    if (filters.length) {
      filterBar.style.display = 'flex';
      filterBar.innerHTML = '<span>筛选：</span>' + filters.map((f, i) => {
        const col = (data.columns || []).find(c => c.id === (f.column || f.id));
        const opLabel = (KB_FILTER_OPS.find(o => o.id === f.op) || {}).label || f.op;
        return `<span class="bitable-filter-tag">${esc(col?.name || f.column)} ${esc(opLabel)} ${esc(f.value || '')}<button onclick="bitableRemoveFilter(${i})">×</button></span>`;
      }).join('') + `<button class="bitable-toolbar-btn" onclick="bitableClearFilters()">清除</button>`;
    } else {
      filterBar.style.display = 'none';
      filterBar.innerHTML = '';
    }
  }

  let batchBar = document.getElementById('bitable-batch-bar');
  if (batchCount && activeView.type === 'table') {
    if (!batchBar) {
      batchBar = document.createElement('div');
      batchBar.id = 'bitable-batch-bar';
      batchBar.className = 'bitable-batch-bar';
      toolbar.parentNode.insertBefore(batchBar, content);
    }
    batchBar.style.display = 'flex';
    batchBar.innerHTML = `已选 ${batchCount} 条 <button class="bitable-toolbar-btn" onclick="bitableSelectedRows.clear();bitableRefreshEditor()">取消</button> <button class="bitable-toolbar-btn" style="color:var(--rose)" onclick="bitableBatchDelete()">批量删除</button>`;
  } else if (batchBar) {
    batchBar.style.display = 'none';
  }

  content.innerHTML = '';
  const vtype = activeView.type || 'table';
  if (vtype === 'kanban') content.appendChild(bitableRenderKanban(data));
  else if (vtype === 'gallery') content.appendChild(bitableRenderGallery(data));
  else if (vtype === 'calendar') content.appendChild(bitableRenderCalendar(data));
  else if (vtype === 'form') content.appendChild(bitableRenderForm(data));
  else content.appendChild(bitableRenderTable(data));
}

function _attachColumnResize(th, dbId, col, view) {
  th.style.position = 'relative';
  const handle = document.createElement('div');
  handle.className = 'bitable-col-resize';
  th.appendChild(handle);
  let startX = 0;
  let startW = 0;
  const onMove = (e) => {
    const w = Math.max(60, startW + e.clientX - startX);
    th.style.minWidth = w + 'px';
    th.style.width = w + 'px';
    const colId = th.dataset.colId;
    th.closest('table')?.querySelectorAll(`td[data-col-id="${colId}"]`).forEach(td => {
      td.style.minWidth = w + 'px';
      td.style.width = w + 'px';
    });
  };
  const onUp = async () => {
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    handle.classList.remove('resizing');
    const w = parseInt(th.style.width || th.offsetWidth, 10);
    const widths = { ...(view.columnWidths || {}), [col.id]: w };
    await bitableUpdateView({ columnWidths: widths }, true);
  };
  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    startX = e.clientX;
    startW = th.offsetWidth;
    handle.classList.add('resizing');
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
}

function _buildTableRow(data, row, idx, columns, activeView) {
  const tr = document.createElement('tr');
  tr.dataset.rowId = row.id;
  const tdCheck = document.createElement('td');
  tdCheck.className = 'row-head row-check';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = bitableSelectedRows.has(row.id);
  cb.onchange = () => {
    if (cb.checked) bitableSelectedRows.add(row.id);
    else bitableSelectedRows.delete(row.id);
    renderBitableEditor(bitableRenderCache);
  };
  tdCheck.appendChild(cb);
  tr.appendChild(tdCheck);
  const tdIdx = document.createElement('td');
  tdIdx.className = 'row-head';
  tdIdx.textContent = String(idx + 1);
  tr.appendChild(tdIdx);
  const frozenCount = (activeView.frozenColumns || 0) + 1;
  columns.forEach((col, ci) => {
    const cell = kbDbBuildCell(data.id, col, row, {
      onRefresh: () => bitableRefreshEditor(),
      allColumns: data.allColumns || data.columns,
    });
    if (ci < frozenCount) cell.classList.add('frozen');
    tr.appendChild(cell);
  });
  _applyConditionalFormat(tr, row, activeView, columns);
  tr.oncontextmenu = (ev) => {
    ev.preventDefault();
    _kbShowDbMenu(ev, [
      ['打开详情', () => bitableOpenRowDrawer(data.id, row, data.allColumns || data.columns)],
      ['删除记录', async () => {
        await fetch(`/api/databases/${encodeURIComponent(data.id)}/rows/${encodeURIComponent(row.id)}`, { method: 'DELETE' });
        await bitableRefreshEditor();
      }, true],
    ]);
  };
  tr.ondblclick = (e) => {
    if (e.target.closest('input,button,select,textarea')) return;
    bitableOpenRowDrawer(data.id, row, data.allColumns || data.columns);
  };
  return tr;
}

function bitableRenderTable(data) {
  const wrap = document.createElement('div');
  wrap.className = 'bitable-table-scroll';
  const table = document.createElement('table');
  table.className = 'bitable-grid';
  const activeView = data.view || {};
  const columns = data.columns || [];
  const widths = activeView.columnWidths || {};
  const frozenCount = (activeView.frozenColumns || 0) + 1;
  let leftOffset = 84;

  const thead = document.createElement('thead');
  const hr = document.createElement('tr');
  const thCheck = document.createElement('th');
  thCheck.className = 'row-head row-check';
  thCheck.innerHTML = '<input type="checkbox" title="全选">';
  thCheck.querySelector('input').onchange = (e) => {
    (data.rows || []).forEach(r => {
      if (e.target.checked) bitableSelectedRows.add(r.id);
      else bitableSelectedRows.delete(r.id);
    });
    renderBitableEditor(data);
  };
  hr.appendChild(thCheck);
  const thIdx = document.createElement('th');
  thIdx.className = 'row-head';
  thIdx.textContent = '#';
  thIdx.style.left = '36px';
  hr.appendChild(thIdx);

  columns.forEach((col, ci) => {
    const th = document.createElement('th');
    th.dataset.colId = col.id;
    const w = widths[col.id] || col.width || 140;
    th.style.minWidth = w + 'px';
    th.style.width = w + 'px';
    if (ci < frozenCount) {
      th.classList.add('frozen');
      th.style.left = leftOffset + 'px';
      leftOffset += w;
    }
    th.innerHTML = esc(col.name) + `<span class="bitable-col-type-label">${esc(kbDbColTypeLabel(col.type))}</span>`;
    th.oncontextmenu = (ev) => { ev.preventDefault(); bitableShowColumnMenu(ev, col); };
    _attachColumnResize(th, data.id, col, activeView);
    hr.appendChild(th);
  });
  thead.appendChild(hr);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  const allCols = data.allColumns || columns;

  if (data.groups && data.groups.length) {
    data.groups.forEach((grp, gi) => {
      const gtr = document.createElement('tr');
      gtr.className = 'bitable-group-header';
      const gtd = document.createElement('td');
      gtd.colSpan = columns.length + 2;
      const dotColor = grp.color ? (KB_SELECT_COLORS[grp.color] || '') : '';
      gtd.innerHTML = (dotColor ? `<span class="group-dot" style="background:${dotColor}"></span>` : '') +
        esc(grp.name) + ` <span style="font-weight:400;color:var(--text-3)">(${(grp.rows || []).length})</span>`;
      gtr.appendChild(gtd);
      tbody.appendChild(gtr);
      (grp.rows || []).forEach((row, idx) => tbody.appendChild(_buildTableRow(data, row, idx, columns, activeView)));
    });
  } else {
    (data.rows || []).forEach((row, idx) => tbody.appendChild(_buildTableRow(data, row, idx, columns, activeView)));
  }

  const realCount = (data.rows || []).length || (data.groups || []).reduce((n, g) => n + ((g.rows || []).length), 0);

  table.appendChild(tbody);
  wrap.appendChild(table);

  const footer = document.createElement('div');
  footer.className = 'bitable-table-footer';
  footer.innerHTML = `<button type="button" class="bitable-table-add" onclick="bitableAddRow()">＋ 添加记录</button><span class="bitable-table-count">${realCount} 条记录</span>`;
  wrap.appendChild(footer);

  const syncGhostRows = () => {
    const rowH = 36;
    const headH = table.querySelector('thead')?.offsetHeight || 34;
    const footH = footer.offsetHeight || 34;
    const wrapH = wrap.clientHeight || 0;
    // Prefer measured scrollport; fall back to viewport estimate before first layout.
    const avail = wrapH > 80
      ? Math.max(0, wrapH - headH - footH)
      : Math.max(240, (window.innerHeight || 800) - 260);
    const realRows = tbody.querySelectorAll('tr:not(.bitable-empty-row)').length;
    const need = Math.max(8, Math.ceil(avail / rowH) - realRows + 1);
    const ghosts = tbody.querySelectorAll('tr.bitable-empty-row');
    if (ghosts.length === need) return;
    ghosts.forEach(tr => tr.remove());
    for (let i = 0; i < need; i++) {
      tbody.appendChild(_buildEmptyTableRow(columns, activeView, frozenCount));
    }
  };
  syncGhostRows();
  requestAnimationFrame(() => requestAnimationFrame(syncGhostRows));
  if (typeof ResizeObserver !== 'undefined') {
    let t = 0;
    const ro = new ResizeObserver(() => {
      clearTimeout(t);
      t = setTimeout(syncGhostRows, 40);
    });
    ro.observe(wrap);
  }
  return wrap;
}

function _buildEmptyTableRow(columns, activeView, frozenCount) {
  const tr = document.createElement('tr');
  tr.className = 'bitable-empty-row';
  tr.title = '点击添加记录';
  tr.onclick = () => bitableAddRow();
  const tdCheck = document.createElement('td');
  tdCheck.className = 'row-head row-check';
  tr.appendChild(tdCheck);
  const tdIdx = document.createElement('td');
  tdIdx.className = 'row-head';
  tr.appendChild(tdIdx);
  columns.forEach((col, ci) => {
    const td = document.createElement('td');
    td.dataset.colId = col.id;
    const w = (activeView.columnWidths || {})[col.id] || col.width || 140;
    td.style.minWidth = w + 'px';
    td.style.width = w + 'px';
    if (ci < frozenCount) td.classList.add('frozen');
    tr.appendChild(td);
  });
  return tr;
}

function bitableRenderKanban(data) {
  const board = document.createElement('div');
  board.className = 'bitable-kanban';
  const kanban = data.kanban || { columns: [], groupColumn: '', groupType: 'select' };
  const titleCol = (data.columns || [])[0];
  const groupColId = kanban.groupColumn || '';
  const groupType = kanban.groupType || 'select';
  const canDrag = groupType !== 'mselect';
  const activeView = data.view || {};
  const groupCol = (data.allColumns || data.columns || []).find(c => c.id === groupColId);
  const progressCol = (data.columns || []).find(c => c.type === 'progress');
  const pillCols = (data.columns || []).filter(c =>
    c.id !== (titleCol && titleCol.id) &&
    c.id !== groupColId &&
    (c.type === 'select' || c.type === 'mselect')
  ).slice(0, 3);
  const textMetaCols = (data.columns || []).filter(c =>
    c.id !== (titleCol && titleCol.id) &&
    c.id !== groupColId &&
    c.type !== 'select' && c.type !== 'mselect' && c.type !== 'progress' &&
    (c.type === 'text' || c.type === 'longtext' || c.type === 'date' || c.type === 'url')
  ).slice(0, 2);

  (kanban.columns || []).forEach(col => {
    const column = document.createElement('div');
    const colorKey = col.color || (_findOption(groupCol, col.name || col.id) || {}).color || '';
    const pair = colorKey ? _optionPillPair(colorKey) : { bg: '#F2F3F5', fg: '#646A73' };
    column.className = 'bitable-kanban-col';
    column.dataset.groupKey = col.id;
    // Soften column wash vs badge (Feishu: pale column, clearer badge).
    column.style.background = `color-mix(in srgb, ${pair.bg} 72%, white)`;
    const head = document.createElement('div');
    head.className = 'bitable-kanban-col-head';
    head.innerHTML = `<span class="bitable-kanban-col-badge" style="background:${pair.bg};color:${pair.fg}">${esc(col.name)}</span><span class="bitable-kanban-col-count">${(col.rows || []).length}</span>`;
    column.appendChild(head);
    const body = document.createElement('div');
    body.className = 'bitable-kanban-col-body';
    if (canDrag && groupColId) {
      body.ondragover = (e) => { e.preventDefault(); body.classList.add('drag-over'); };
      body.ondragleave = () => body.classList.remove('drag-over');
      body.ondrop = async (e) => {
        e.preventDefault();
        body.classList.remove('drag-over');
        const rowId = e.dataTransfer.getData('text/row-id');
        if (!rowId) return;
        const value = col.id === '__ungrouped__' ? '' : col.id;
        try {
          if (groupColId) await _kbUpdateCell(data.id, rowId, groupColId, value);
          const order = { ...(activeView.kanbanOrder || {}) };
          Object.keys(order).forEach(k => { order[k] = (order[k] || []).filter(id => id !== rowId); });
          const key = col.id;
          order[key] = [...(order[key] || []).filter(id => id !== rowId), rowId];
          await bitableUpdateView({ kanbanOrder: order }, true);
          await bitableRefreshEditor();
        } catch (err) {
          toast('移动失败', 'error');
        }
      };
    }
    (col.rows || []).forEach(row => {
      const card = document.createElement('div');
      card.className = 'bitable-kanban-card';
      if (canDrag) {
        card.draggable = true;
        card.ondragstart = (e) => {
          e.dataTransfer.setData('text/row-id', row.id);
          e.dataTransfer.setData('text/from-group', col.id);
          card.classList.add('dragging');
        };
        card.ondragend = () => card.classList.remove('dragging');
      }
      const title = titleCol ? ((row.cells || {})[titleCol.id] || '未命名') : row.id;
      let html = `<div class="bitable-kanban-card-title">${esc(String(title))}</div>`;
      const pillsHtml = [];
      pillCols.forEach(c => {
        const v = (row.cells || {})[c.id];
        if (c.type === 'mselect' && Array.isArray(v)) {
          v.forEach(name => {
            const opt = _findOption(c, name);
            const p = _optionPillStyle(opt);
            pillsHtml.push(`<span class="kb-db-pill" style="background:${p.bg};color:${p.fg}">${esc(name)}</span>`);
          });
        } else if (v) {
          const opt = _findOption(c, v);
          const p = _optionPillStyle(opt);
          pillsHtml.push(`<span class="kb-db-pill" style="background:${p.bg};color:${p.fg}">${esc(String(v))}</span>`);
        }
      });
      // Also show group status as a pill on the card (like Feishu).
      if (groupCol && col.id !== '__ungrouped__') {
        const gOpt = _findOption(groupCol, col.name) || _findOption(groupCol, col.id) || { name: col.name, color: colorKey };
        const gp = _optionPillStyle(gOpt);
        pillsHtml.push(`<span class="kb-db-pill" style="background:${gp.bg};color:${gp.fg}">${esc(col.name)}</span>`);
      }
      if (pillsHtml.length) html += `<div class="bitable-kanban-card-pills">${pillsHtml.join('')}</div>`;
      const metaParts = textMetaCols.map(c => {
        const s = kbDbFormatDisplay(c, (row.cells || {})[c.id]);
        return s ? esc(s) : '';
      }).filter(Boolean);
      if (metaParts.length) html += `<div class="bitable-kanban-card-meta">${metaParts.join(' · ')}</div>`;
      if (progressCol) {
        const pv = Number((row.cells || {})[progressCol.id]);
        if (!Number.isNaN(pv) && (row.cells || {})[progressCol.id] != null && (row.cells || {})[progressCol.id] !== '') {
          const max = Number(progressCol.max || 100) || 100;
          html += `<div class="bitable-kanban-card-progress"><div class="kb-db-progress"><div class="kb-db-progress-track"><span style="width:${Math.min(100, Math.max(0, (pv / max) * 100))}%"></span></div><span class="kb-db-progress-pct">${esc(String(pv))}%</span></div></div>`;
        }
      }
      card.innerHTML = html;
      card.onclick = (e) => {
        if (!e.defaultPrevented) bitableOpenRowDrawer(data.id, row, data.allColumns || data.columns);
      };
      body.appendChild(card);
    });
    const addBtn = document.createElement('button');
    addBtn.type = 'button';
    addBtn.className = 'bitable-kanban-add';
    addBtn.textContent = '＋';
    addBtn.title = '在此列新建记录';
    addBtn.onclick = (e) => {
      e.stopPropagation();
      const cells = {};
      if (groupColId && col.id !== '__ungrouped__') cells[groupColId] = col.id;
      bitableAddRow(cells);
    };
    body.appendChild(addBtn);
    column.appendChild(body);
    board.appendChild(column);
  });
  return board;
}

function _coverUrl(cover) {
  if (!cover) return '';
  if (/^https?:\/\//i.test(cover)) return cover;
  if (cover.startsWith('/') || cover.startsWith('articles/')) return cover.startsWith('/') ? cover : '/' + cover;
  return cover;
}

function bitableRenderGallery(data) {
  const grid = document.createElement('div');
  grid.className = 'bitable-gallery';
  const gallery = data.gallery || { items: [] };
  (gallery.items || []).forEach(item => {
    const card = document.createElement('div');
    card.className = 'bitable-gallery-card';
    const row = item.row;
    const cover = document.createElement('div');
    cover.className = 'bitable-gallery-cover';
    const coverSrc = _coverUrl(item.cover);
    if (coverSrc && (/^https?:\/\//i.test(coverSrc) || coverSrc.startsWith('/') || coverSrc.startsWith('articles/'))) {
      cover.innerHTML = `<img src="${esc(coverSrc)}" alt="">`;
    } else {
      cover.textContent = item.coverText || '📋';
    }
    const body = document.createElement('div');
    body.className = 'bitable-gallery-body';
    body.innerHTML = `<div class="bitable-gallery-title">${esc(String(item.title || '未命名'))}</div>`;
    const meta = (item.fields || []).map(f => {
      const col = { type: f.type, name: f.name };
      const s = kbDbFormatDisplay(col, f.value);
      return s ? esc(f.name) + ': ' + esc(s) : '';
    }).filter(Boolean).join('<br>');
    if (meta) body.innerHTML += `<div class="bitable-gallery-meta">${meta}</div>`;
    card.appendChild(cover);
    card.appendChild(body);
    card.onclick = () => bitableOpenRowDrawer(data.id, row, data.allColumns || data.columns);
    grid.appendChild(card);
  });
  if (!gallery.items || !gallery.items.length) {
    grid.innerHTML = '<div style="grid-column:1/-1;padding:40px;text-align:center;color:var(--text-3)">暂无记录，点击「＋ 新建记录」添加</div>';
  }
  return grid;
}

function bitableRenderCalendar(data) {
  const wrap = document.createElement('div');
  wrap.className = 'bitable-calendar';
  const cal = data.calendar || { days: [] };
  (cal.days || []).forEach(day => {
    const block = document.createElement('div');
    block.className = 'bitable-calendar-day';
    block.innerHTML = `<div class="bitable-calendar-day-head">${esc(day.label || day.date || '')}</div>`;
    (day.items || []).forEach(item => {
      const el = document.createElement('div');
      el.className = 'bitable-calendar-item';
      el.textContent = String(item.title || '未命名');
      el.onclick = () => bitableOpenRowDrawer(data.id, item.row, data.allColumns || data.columns);
      block.appendChild(el);
    });
    wrap.appendChild(block);
  });
  if (!cal.days || !cal.days.length) {
    wrap.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-3)">暂无带日期的记录</div>';
  }
  return wrap;
}

function bitableRenderForm(data) {
  const wrap = document.createElement('div');
  wrap.className = 'bitable-form';
  const form = data.form || { fields: [] };
  const fieldsWrap = document.createElement('div');
  fieldsWrap.id = 'bitable-form-fields';
  (form.fields || []).forEach(f => {
    const field = document.createElement('div');
    field.className = 'bitable-form-field';
    field.dataset.colId = f.id;
    const label = document.createElement('label');
    label.textContent = f.name;
    field.appendChild(label);
    if (f.type === 'longtext') {
      const ta = document.createElement('textarea');
      ta.rows = 3;
      field.appendChild(ta);
    } else if (f.type === 'select') {
      const sel = document.createElement('select');
      sel.innerHTML = '<option value=""></option>' + (f.options || []).map(o =>
        `<option value="${esc(o.name)}">${esc(o.name)}</option>`
      ).join('');
      field.appendChild(sel);
    } else if (f.type === 'checkbox') {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      field.appendChild(cb);
    } else if (f.type === 'date' || f.type === 'datetime') {
      const inp = document.createElement('input');
      inp.type = f.type === 'datetime' ? 'datetime-local' : 'date';
      field.appendChild(inp);
    } else {
      const inp = document.createElement('input');
      inp.type = 'text';
      field.appendChild(inp);
    }
    fieldsWrap.appendChild(field);
  });
  wrap.appendChild(fieldsWrap);
  const submit = document.createElement('button');
  submit.type = 'button';
  submit.className = 'bitable-toolbar-btn primary';
  submit.textContent = '提交记录';
  submit.style.marginTop = '8px';
  submit.onclick = async () => {
    const cells = {};
    fieldsWrap.querySelectorAll('.bitable-form-field').forEach(field => {
      const cid = field.dataset.colId;
      const cb = field.querySelector('input[type="checkbox"]');
      if (cb) { cells[cid] = cb.checked; return; }
      const sel = field.querySelector('select');
      if (sel) { cells[cid] = sel.value; return; }
      const ta = field.querySelector('textarea');
      if (ta) { cells[cid] = ta.value; return; }
      const inp = field.querySelector('input');
      if (inp) { cells[cid] = inp.value; return; }
    });
    try {
      await fetch(`/api/databases/${encodeURIComponent(data.id)}/rows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cells }),
      });
      toast('已提交', 'success');
      fieldsWrap.querySelectorAll('input,textarea,select').forEach(el => {
        if (el.type === 'checkbox') el.checked = false;
        else el.value = '';
      });
      await bitableRefreshEditor();
    } catch (e) {
      toast('提交失败', 'error');
    }
  };
  wrap.appendChild(submit);
  return wrap;
}

function bitableShowViewContextMenu(e, viewId) {
  e.preventDefault();
  e.stopPropagation();
  const views = bitableRenderCache?.views || [];
  if (views.length <= 1) {
    _kbShowDbMenu(e, [
      ['重命名', async () => {
        const name = await uiPrompt('视图名称', '名称', '');
        if (name === null || !name.trim()) return;
        await bitableUpdateView({ name: name.trim() });
      }],
    ]);
    return;
  }
  _kbShowDbMenu(e, [
    ['重命名', async () => {
      const v = views.find(x => x.id === viewId);
      const name = await uiPrompt('视图名称', '名称', v?.name || '');
      if (name === null || !name.trim()) return;
      await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/views/${encodeURIComponent(viewId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() }),
      });
      await bitableRefreshEditor();
    }],
    ['删除视图', async () => {
      if (!(await uiConfirm('确定删除此视图？'))) return;
      await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/views/${encodeURIComponent(viewId)}`, { method: 'DELETE' });
      if (bitableRenderCache?.view?.id === viewId) {
        const next = views.find(v => v.id !== viewId);
        if (next) await bitableSwitchView(next.id);
        else await bitableRefreshEditor();
      } else {
        await bitableRefreshEditor();
      }
    }, true],
  ]);
}

async function bitableShowColumnMenu(e, col) {
  e.preventDefault();
  const items = [
    ['编辑选项…', async () => {
      if (col.type !== 'select' && col.type !== 'mselect') {
        toast('仅单选/多选字段支持选项编辑', 'error');
        return;
      }
      const raw = await uiPrompt('选项列表（每行一个）', '选项', (col.options || []).map(o => o.name).join('\n'));
      if (raw === null) return;
      const names = raw.split('\n').map(s => s.trim()).filter(Boolean);
      const colors = ['1', '2', '3', '4', '5', '6', '7', '8'];
      const options = names.map((n, i) => ({ name: n, color: colors[i % colors.length] }));
      await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/columns/${encodeURIComponent(col.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ options }),
      });
      await bitableRefreshEditor();
    }],
    ['隐藏列', async () => {
      const hidden = [...(bitableRenderCache.view.hiddenColumns || [])];
      if (!hidden.includes(col.id)) hidden.push(col.id);
      await bitableUpdateView({ hiddenColumns: hidden });
    }],
    ['分组依据', async () => {
      await bitableUpdateView({ groupBy: col.id });
      toast('已按「' + col.name + '」分组', 'success');
    }],
    ['删除字段', async () => {
      if (!(await uiConfirm('确定删除字段「' + col.name + '」？'))) return;
      await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/columns/${encodeURIComponent(col.id)}`, { method: 'DELETE' });
      await bitableRefreshEditor();
    }, true],
  ];
  if (col.type === 'link') {
    items.unshift(['配置关联表…', async () => {
      const dbs = bitableListCache.length ? bitableListCache : (await (await fetch('/api/databases')).json()).databases || [];
      const pick = await uiPrompt('关联表 ID', 'db_xxx', col.linkDatabase || '');
      if (pick === null) return;
      await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/columns/${encodeURIComponent(col.id)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ linkDatabase: pick.trim(), bidirectional: false }),
      });
      await bitableRefreshEditor();
    }]);
  }
  _kbShowDbMenu(e, items);
}

async function bitableConfigureKanbanView() {
  if (!bitableCurrentId || !bitableRenderCache) return;
  const cols = _bitableGroupableColumns(bitableRenderCache);
  if (!cols.length) {
    toast('请先添加单选、多选或人员等可分组字段', 'error');
    return;
  }
  const names = cols.map(c => c.name).join(' / ');
  const pick = await uiPrompt('分组字段名称', '可选: ' + names, cols[0].name);
  if (pick === null) return;
  const col = cols.find(c => c.name === pick.trim()) || cols[0];
  await bitableUpdateView({ groupColumn: col.id });
  toast('看板已按「' + col.name + '」分组', 'success');
}

async function bitableConfigureGalleryView() {
  if (!bitableCurrentId || !bitableRenderCache) return;
  const cols = bitableRenderCache.allColumns || bitableRenderCache.columns || [];
  const pick = await uiPrompt('封面字段名称', '字段', cols[0]?.name || '');
  if (pick === null) return;
  const col = cols.find(c => c.name === pick.trim()) || cols[0];
  if (!col) return;
  await bitableUpdateView({ coverColumn: col.id });
}

async function bitableSwitchView(viewId) {
  if (!bitableCurrentId) return;
  try {
    await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ viewID: viewId }),
    });
    _kbInvalidateDb(bitableCurrentId);
    bitableRenderCache = await _kbFetchDatabase(bitableCurrentId, viewId, bitableSearchQuery);
    renderBitableEditor(bitableRenderCache);
  } catch (e) {
    toast('切换视图失败', 'error');
  }
}

async function bitableAddRow(initialCells) {
  if (!bitableCurrentId) return;
  try {
    // Clear active filters so the new empty row remains visible
    // (Feishu-style: newly created records should appear immediately).
    const view = bitableRenderCache?.view;
    if (view && (view.filters || []).length) {
      await bitableUpdateView({ filters: [] }, true);
    }
    await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/rows`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cells: initialCells && typeof initialCells === 'object' ? initialCells : {} }),
    });
    await bitableRefreshEditor();
  } catch (e) {
    toast('添加记录失败', 'error');
  }
}

async function _bitableConfigureColumnExtra(type) {
  const extra = {};
  if (type === 'link') {
    const pick = await uiPrompt('关联表 ID', 'db_xxx', '');
    if (pick === null) return null;
    extra.linkDatabase = pick.trim();
  } else if (type === 'lookup') {
    const lc = await uiPrompt('关联字段 ID', 'c_xxx', '');
    const lk = await uiPrompt('引用字段 ID', 'c_xxx', '');
    if (lc === null || lk === null) return null;
    extra.linkColumn = lc.trim();
    extra.lookupColumn = lk.trim();
  } else if (type === 'rollup') {
    const lc = await uiPrompt('关联字段 ID', 'c_xxx', '');
    const rc = await uiPrompt('汇总字段 ID（可空）', 'c_xxx', '');
    const fn = await uiPrompt('汇总函数', 'count/sum/avg/min/max', 'count');
    if (lc === null) return null;
    extra.linkColumn = lc.trim();
    extra.rollupColumn = (rc || '').trim();
    extra.rollupFn = (fn || 'count').trim();
  } else if (type === 'formula') {
    const expr = await uiPrompt('公式表达式', '例如 CONCAT(title, "-", status)', '');
    if (expr === null) return null;
    extra.expression = expr.trim();
  } else if (type === 'ai_text') {
    const prompt = await uiPrompt('AI 提示词', '根据本行字段生成…', '根据本行其他字段生成摘要');
    if (prompt === null) return null;
    extra.aiPrompt = prompt.trim();
  }
  return extra;
}

async function bitableAddColumnPrompt(e) {
  if (!bitableCurrentId) return;
  const ev = e || { clientX: window.innerWidth / 2, clientY: 120 };
  _kbShowFieldPickerMenu(ev, async (type) => {
    const name = await uiPrompt('新字段名称', '字段名', kbDbColTypeLabel(type));
    if (name === null) return;
    const extra = await _bitableConfigureColumnExtra(type);
    if (extra === null && ['link', 'lookup', 'rollup', 'formula', 'ai_text'].includes(type)) return;
    try {
      await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/columns`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim() || kbDbColTypeLabel(type), type, ...(extra || {}) }),
      });
      await bitableRefreshEditor();
    } catch (err) {
      toast('添加字段失败：' + (err.message || err), 'error');
    }
  });
}

function bitableAddViewMenu(e) {
  e.stopPropagation();
  const data = bitableRenderCache;
  const groupCols = data ? _bitableGroupableColumns(data) : [];
  const items = [
    ['☰ 表格视图', () => bitableCreateView('table', '表格')],
    ['▣ 画廊视图', () => bitableCreateView('gallery', '画廊')],
    ['📅 日历视图', () => bitableCreateView('calendar', '日历')],
    ['📝 表单视图', () => bitableCreateView('form', '表单')],
  ];
  groupCols.forEach(c => {
    items.push([`▦ 看板 · ${c.name}`, () => bitableCreateView('kanban', `看板 · ${c.name}`, c.id)]);
  });
  if (!groupCols.length) items.push(['▦ 看板视图', () => bitableCreateView('kanban', '看板')]);
  _kbShowDbMenu(e, items);
}

async function bitableCreateView(type, name, groupColumn) {
  if (!bitableCurrentId) return;
  try {
    const body = { type, name };
    if (type === 'kanban' && groupColumn) body.groupColumn = groupColumn;
    const resp = await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/views`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(await resp.text());
    const view = await resp.json();
    await bitableSwitchView(view.id);
    toast('已创建「' + (view.name || name) + '」', 'success');
  } catch (e) {
    toast('创建视图失败', 'error');
  }
}

function bitableShowFilterDialog() {
  _kbShowFilterBuilder();
}

async function bitableShowSortDialog() {
  if (!bitableCurrentId || !bitableRenderCache) return;
  const cols = bitableRenderCache.columns || [];
  const raw = await uiPrompt(
    '多列排序（每行：字段名,asc|desc）',
    '例如：\n状态,asc\n截止日期,asc',
    (bitableRenderCache.view.sorts || []).map(s => {
      const c = cols.find(x => x.id === s.column);
      return (c?.name || s.column) + ',' + (s.order || 'asc');
    }).join('\n')
  );
  if (raw === null) return;
  const sorts = [];
  raw.split('\n').forEach(line => {
    const parts = line.split(',');
    if (parts.length < 1) return;
    const col = cols.find(c => c.name === parts[0].trim()) || cols.find(c => c.id === parts[0].trim());
    if (!col) return;
    sorts.push({ column: col.id, order: (parts[1] || 'asc').trim().toLowerCase() });
  });
  await bitableUpdateView({ sorts });
}

async function bitableUpdateView(fields, skipRefresh) {
  const viewId = bitableRenderCache?.view?.id;
  if (!viewId) return;
  try {
    await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/views/${encodeURIComponent(viewId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
    if (!skipRefresh) await bitableRefreshEditor();
  } catch (e) {
    toast('更新视图失败', 'error');
  }
}

async function bitableRemoveFilter(index) {
  const filters = [...(bitableRenderCache.view.filters || [])];
  filters.splice(index, 1);
  await bitableUpdateView({ filters });
}

async function bitableClearFilters() {
  await bitableUpdateView({ filters: [] });
}

async function bitableSearch() {
  const q = await uiPrompt('表内搜索', '关键词', bitableSearchQuery || '');
  if (q === null) return;
  bitableSearchQuery = q.trim();
  await bitableRefreshEditor();
}

async function bitableExportCsv() {
  if (!bitableCurrentId) return;
  const viewId = bitableRenderCache?.view?.id || '';
  window.location.href = `/api/databases/${encodeURIComponent(bitableCurrentId)}/export?format=csv&view=${encodeURIComponent(viewId)}`;
}

async function bitableImportCsv() {
  if (!bitableCurrentId) return;
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.csv,text/csv';
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const csv = await file.text();
    const mode = (await uiConfirm('清空现有数据后导入？\n确定=替换，取消=追加')) ? 'replace' : 'append';
    try {
      const resp = await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/import`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ csv, mode }),
      });
      if (!resp.ok) throw new Error(await resp.text());
      const data = await resp.json();
      toast('已导入 ' + (data.added || 0) + ' 行', 'success');
      await bitableRefreshEditor();
    } catch (e) {
      toast('导入失败', 'error');
    }
  };
  input.click();
}

async function bitableShowHistory() {
  if (!bitableCurrentId) return;
  try {
    const resp = await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/history`);
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    const items = data.history || [];
    if (!items.length) {
      toast('暂无历史版本', 'info');
      return;
    }
    let modal = document.getElementById('bitable-history-modal');
    if (!modal) {
      modal = document.createElement('div');
      modal.id = 'bitable-history-modal';
      modal.className = 'bitable-modal';
      modal.innerHTML = `
        <div class="bitable-modal-card" style="max-width:420px">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
            <strong>历史版本</strong>
            <button type="button" class="bitable-toolbar-btn" data-close>关闭</button>
          </div>
          <div id="bitable-history-list" style="max-height:320px;overflow:auto;display:flex;flex-direction:column;gap:6px"></div>
        </div>`;
      document.body.appendChild(modal);
      modal.addEventListener('click', (e) => {
        if (e.target === modal || e.target.closest('[data-close]')) modal.classList.remove('open');
      });
    }
    const list = modal.querySelector('#bitable-history-list');
    list.innerHTML = items.map(h => `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:var(--surface)">
        <div style="min-width:0">
          <div style="font-size:12px;font-weight:600">${esc(h.id || '')}</div>
          <div style="font-size:11px;color:var(--text-3)">${esc(h.mtime || '')}</div>
        </div>
        <button type="button" class="bitable-toolbar-btn" data-snap="${esc(h.id || '')}">恢复</button>
      </div>`).join('');
    list.querySelectorAll('button[data-snap]').forEach(btn => {
      btn.onclick = async () => {
        const snapId = btn.getAttribute('data-snap');
        if (!(await uiConfirm('确定恢复到该历史版本？'))) return;
        try {
          await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/history/${encodeURIComponent(snapId)}`, { method: 'POST' });
          modal.classList.remove('open');
          await bitableRefreshEditor();
          toast('已恢复', 'success');
        } catch (e) {
          toast('历史恢复失败', 'error');
        }
      };
    });
    modal.classList.add('open');
  } catch (e) {
    toast('历史恢复失败', 'error');
  }
}

async function bitableBatchDelete() {
  if (!bitableCurrentId || !bitableSelectedRows.size) return;
  if (!(await uiConfirm('确定删除选中的 ' + bitableSelectedRows.size + ' 条记录？'))) return;
  try {
    await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}/rows/batch-delete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [...bitableSelectedRows] }),
    });
    bitableSelectedRows.clear();
    await bitableRefreshEditor();
    toast('已删除', 'success');
  } catch (e) {
    toast('批量删除失败', 'error');
  }
}

async function bitableDeleteDatabase() {
  if (!bitableCurrentId) return;
  if (!(await uiConfirm('确定删除此多维表格？数据不可恢复。'))) return;
  try {
    await fetch(`/api/databases/${encodeURIComponent(bitableCurrentId)}`, { method: 'DELETE' });
    bitableCurrentId = null;
    bitableRenderCache = null;
    document.getElementById('bitable-editor').style.display = 'none';
    document.getElementById('bitable-empty').style.display = 'flex';
    document.getElementById('bitable-content').innerHTML = '';
    await loadBitableList();
    toast('已删除', 'success');
  } catch (e) {
    toast('删除失败', 'error');
  }
}

/* ===== NOTE EMBED WIDGET ===== */
function _kbBuildDbRow(dbId, columns, row, wrap) {
  const tr = document.createElement('tr');
  tr.dataset.rowId = row.id;
  columns.forEach(col => {
    tr.appendChild(kbDbBuildCell(dbId, col, row, {
      onRefresh: () => { if (wrap) _kbRefreshDatabaseWidget(wrap, dbId); },
      allColumns: columns,
    }));
  });
  tr.oncontextmenu = (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    _kbShowDbMenu(ev, [
      ['打开详情', () => bitableOpenRowDrawer(dbId, row, columns)],
      ['删除行', async () => {
        await fetch(`/api/databases/${encodeURIComponent(dbId)}/rows/${encodeURIComponent(row.id)}`, { method: 'DELETE' });
        _kbInvalidateDb(dbId);
        if (wrap) await _kbRefreshDatabaseWidget(wrap, dbId);
      }, true],
    ]);
  };
  return tr;
}

async function _kbRefreshDatabaseWidget(wrap, dbId, viewId) {
  const data = await _kbFetchDatabase(dbId, viewId);
  wrap._kbDbData = data;
  const titleInput = wrap.querySelector('.kb-db-title');
  if (titleInput) titleInput.value = data.name || 'Untitled';
  const viewTag = wrap.querySelector('.kb-db-view-tag');
  if (viewTag) viewTag.textContent = (data.view && data.view.name) || '表格';

  const tabs = wrap.querySelector('.kb-db-widget-tabs');
  if (tabs) {
    tabs.innerHTML = (data.views || []).map(v =>
      `<button type="button" class="kb-db-widget-tab${v.id === data.view?.id ? ' active' : ''}" data-view="${esc(v.id)}">${bitableViewIcon(v)} ${esc(v.name)}</button>`
    ).join('');
    tabs.querySelectorAll('.kb-db-widget-tab').forEach(btn => {
      btn.onclick = () => _kbRefreshDatabaseWidget(wrap, dbId, btn.dataset.view);
    });
  }

  const bodyHost = wrap.querySelector('.kb-db-widget-body');
  if (!bodyHost) return;
  bodyHost.innerHTML = '';
  const vtype = data.view?.type || 'table';
  if (vtype === 'kanban') bodyHost.appendChild(bitableRenderKanban(data));
  else if (vtype === 'gallery') bodyHost.appendChild(bitableRenderGallery(data));
  else {
    const tableWrap = document.createElement('div');
    tableWrap.className = 'kb-db-table-wrap';
    const table = document.createElement('table');
    table.className = 'kb-db-table';
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    (data.columns || []).forEach(col => {
      const th = document.createElement('th');
      th.textContent = col.name || col.id;
      th.style.minWidth = (col.width || 120) + 'px';
      th.dataset.colId = col.id;
      th.oncontextmenu = (ev) => {
        ev.preventDefault();
        ev.stopPropagation();
        _kbShowDbMenu(ev, [
          ['删除列', async () => {
            await fetch(`/api/databases/${encodeURIComponent(dbId)}/columns/${encodeURIComponent(col.id)}`, { method: 'DELETE' });
            _kbInvalidateDb(dbId);
            await _kbRefreshDatabaseWidget(wrap, dbId, viewId);
          }, true],
        ]);
      };
      hr.appendChild(th);
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    (data.rows || []).forEach(row => tbody.appendChild(_kbBuildDbRow(dbId, data.columns, row, wrap)));
    table.appendChild(tbody);
    tableWrap.appendChild(table);
    bodyHost.appendChild(tableWrap);
  }
}

function _kbMountDatabaseWidget(placeholder, dbId, data) {
  const wrap = document.createElement('div');
  wrap.className = 'kb-database';
  wrap.dataset.dbId = dbId;
  wrap.contentEditable = 'false';

  const header = document.createElement('div');
  header.className = 'kb-db-header';
  header.innerHTML = `<span class="kb-db-icon">🗃</span><input class="kb-db-title" value="${esc(data.name || 'Untitled')}" title="数据库名称"><span class="kb-db-view-tag">${esc((data.view && data.view.name) || '表格')}</span><button type="button" class="kb-db-add-col">＋列</button><button type="button" class="kb-db-open-full" title="在工作区打开">↗</button>`;
  wrap.appendChild(header);

  const tabs = document.createElement('div');
  tabs.className = 'kb-db-widget-tabs';
  wrap.appendChild(tabs);

  const bodyHost = document.createElement('div');
  bodyHost.className = 'kb-db-widget-body';
  wrap.appendChild(bodyHost);

  const footer = document.createElement('div');
  footer.className = 'kb-db-footer';
  footer.innerHTML = '<button type="button" class="kb-db-add-row">＋ 新建行</button>';
  wrap.appendChild(footer);

  header.querySelector('.kb-db-title').addEventListener('change', async (e) => {
    try {
      await fetch(`/api/databases/${encodeURIComponent(dbId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: e.target.value.trim() || 'Untitled' }),
      });
      _kbInvalidateDb(dbId);
    } catch (err) {
      toast('重命名失败：' + err.message, 'error');
    }
  });

  header.querySelector('.kb-db-open-full').onclick = () => switchToBitableView(dbId);

  header.querySelector('.kb-db-add-col').onclick = (e) => {
    bitableCurrentId = dbId;
    bitableAddColumnPrompt(e);
  };

  footer.querySelector('.kb-db-add-row').onclick = async () => {
    try {
      await fetch(`/api/databases/${encodeURIComponent(dbId)}/rows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cells: {} }),
      });
      _kbInvalidateDb(dbId);
      await _kbRefreshDatabaseWidget(wrap, dbId);
    } catch (err) {
      toast('添加行失败：' + err.message, 'error');
    }
  };

  placeholder.replaceWith(wrap);
  _kbRefreshDatabaseWidget(wrap, dbId);
}

function _kbEnhanceDatabases(container) {
  if (!container) return;
  container.querySelectorAll('pre[data-language="kbase-db"], pre[data-language="Kbase-db"]').forEach(pre => {
    if (pre.dataset.kbDbRendered === '1' || pre.closest('.kb-database')) return;
    const code = pre.querySelector('code');
    const dbId = (code ? code.textContent : pre.textContent).trim().split('\n').map(s => s.trim()).filter(Boolean)[0];
    if (!/^db_[a-zA-Z0-9_-]+$/.test(dbId)) return;
    pre.dataset.kbDbRendered = '1';
    const placeholder = document.createElement('div');
    placeholder.className = 'kb-db-loading';
    placeholder.textContent = '加载数据库…';
    pre.replaceWith(placeholder);
    _kbFetchDatabase(dbId).then(data => {
      _kbMountDatabaseWidget(placeholder, dbId, data);
    }).catch(err => {
      placeholder.textContent = '数据库加载失败: ' + (err.message || err);
      placeholder.className = 'kb-db-error';
    });
  });
}

function attachKbDatabaseBlocks(editor, container) {
  if (!container) return;
  const run = () => _kbEnhanceDatabases(container);
  run();
  const ir = container.querySelector('.vditor-ir');
  if (ir && !ir._kbDbObserver) {
    ir._kbDbObserver = new MutationObserver(run);
    ir._kbDbObserver.observe(ir, { childList: true, subtree: true });
  }
}

/* ===== GLOBAL EXPORTS (onclick + TabManager) ===== */
Object.assign(window, {
  switchToBitableView,
  switchToKanbanView,
  backFromBitableView,
  loadBitableList,
  openBitableDatabase,
  bitableCreateDatabase,
  bitableRefreshEditor,
  renderBitableEditor,
  renderBitableList,
  bitableSwitchView,
  bitableAddRow,
  bitableAddColumnPrompt,
  bitableAddViewMenu,
  bitableCreateView,
  bitableShowFilterDialog,
  bitableShowSortDialog,
  bitableUpdateView,
  bitableRemoveFilter,
  bitableClearFilters,
  bitableDeleteDatabase,
  bitableEditRowDialog,
  bitableOpenRowDrawer,
  bitableConfigureKanbanView,
  bitableConfigureGalleryView,
  bitableShowViewContextMenu,
  bitableShowColumnMenu,
  bitableSearch,
  bitableExportCsv,
  bitableImportCsv,
  bitableShowHistory,
  bitableBatchDelete,
  kbDbColTypeLabel,
  kbDbFormatDisplay,
  kbDbBuildCell,
  bitableViewIcon,
  attachKbDatabaseBlocks,
  _applyBitablePageChrome,
});
