let workspaces = [];

async function loadWorkspaces() {
  try {
    const resp = await fetch('/api/workspaces');
    const data = await resp.json();
    workspaces = data.workspaces || [];
  } catch(e) {
    console.error(e);
  }
}

async function renderExplorer() {
  const tree = document.getElementById('explorer-tree');
  if(!tree) return;
  
  await Promise.all([
    loadArticles(),
    loadNotesIndex(),
    loadWorkspaces()
  ]);
  
  let html = '';
  
  // 1. Workspaces (Moved to Left Sidebar)
  const wsList = document.getElementById('ls-workspaces-list');
  if (wsList) {
    let wsHtml = '';
    for(let ws of workspaces) {
      wsHtml += `<div class="ls-item" onclick="openWorkspace('${ws.id}')">
        <span class="icon">💬</span> ${esc(ws.name)}
        <span class="actions" onclick="event.stopPropagation(); deleteWorkspace('${ws.id}')">❌</span>
      </div>`;
    }
    wsList.innerHTML = wsHtml;
  }
  
  // 2. Notes (Folder Tree)
  html += `<div class="explorer-section">
    <div class="explorer-section-title">📝 笔记</div>`;
  
  let noteFolders = {};
  for(let n of notesIndex) {
    let folder = n.folder || '未分类';
    if(!noteFolders[folder]) noteFolders[folder] = [];
    noteFolders[folder].push(n);
  }
  for(let folder in noteFolders) {
    html += `<div class="explorer-folder">
      <div class="explorer-folder-title" onclick="const content = this.nextElementSibling; const isHidden = content.style.display === 'none'; content.style.display = isHidden ? 'block' : 'none'; this.innerHTML = (isHidden ? '📂 ' : '📁 ') + '${esc(folder)}';">📂 ${esc(folder)}</div>
      <div class="explorer-folder-content">`;
    for(let n of noteFolders[folder]) {
      html += `<div class="explorer-item" onclick="openNoteTab('${n.id}')">
        <input type="checkbox" class="explorer-checkbox" data-type="note" data-id="${n.id}" onclick="event.stopPropagation()">
        <span class="icon">📄</span> ${esc(n.title)}
      </div>`;
    }
    html += `</div></div>`;
  }
  html += `</div>`;

  // 3. Articles (Category Tree)
  html += `<div class="explorer-section">
    <div class="explorer-section-title">📚 论文</div>`;
  let catFolders = {};
  for(let a of articles) {
    let cat = a.category || '未分类';
    if(!catFolders[cat]) catFolders[cat] = [];
    catFolders[cat].push(a);
  }
  for(let cat in catFolders) {
    html += `<div class="explorer-folder">
      <div class="explorer-folder-title" onclick="const content = this.nextElementSibling; const isHidden = content.style.display === 'none'; content.style.display = isHidden ? 'block' : 'none'; this.innerHTML = (isHidden ? '📂 ' : '📁 ') + '${esc(cat)}';">📂 ${esc(cat)}</div>
      <div class="explorer-folder-content">`;
    for(let a of catFolders[cat]) {
      html += `<div class="explorer-item" onclick="openArticleTab('${a.id}')">
        <input type="checkbox" class="explorer-checkbox" data-type="paper" data-id="${a.id}" onclick="event.stopPropagation()">
        <span class="icon">📑</span> ${esc(a.title)}
      </div>`;
    }
    html += `</div></div>`;
  }
  html += `</div>`;
  
  tree.innerHTML = html;
}

function filterExplorer() {
  const q = document.getElementById('searchInput').value.toLowerCase();
  const items = document.querySelectorAll('.explorer-item');
  items.forEach(el => {
    if(el.textContent.toLowerCase().includes(q)) {
      el.style.display = '';
    } else {
      el.style.display = 'none';
    }
  });
}

async function showCreateWorkspaceDialog() {
  const name = prompt("请输入新工作空间名称:");
  if(!name) return;
  const resp = await fetch('/api/workspaces', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name})
  });
  if(resp.ok) {
    await renderExplorer();
  }
}

async function deleteWorkspace(id) {
  if(!confirm('确定删除该工作空间吗？这不会删除文件。')) return;
  await fetch('/api/workspaces/' + id, {method: 'DELETE'});
  await renderExplorer();
}

window.openNoteTab = function(id) {
  let note = typeof notesIndex !== 'undefined' ? notesIndex.find(n => n.id === id) : null;
  let title = note ? '📝 ' + note.title : '笔记';
  TabManager.openTab('note-'+id, 'note', title, {id});
}

window.openArticleTab = function(id) {
  let article = typeof articles !== 'undefined' ? articles.find(a => a.id === id) : null;
  if (article) {
    TabManager.openTab('article-' + id, 'article', '📄 ' + article.title, {article});
  } else {
    TabManager.openTab('article-'+id, 'article', '论文', {id});
  }
}

window.openWorkspace = function(ws_id) {
  let ws = workspaces.find(w => w.id === ws_id);
  const id = 'ws-' + ws_id;
  
  // If tab doesn't exist, inject its HTML before opening
  if (!document.getElementById('iframe-' + id)) {
    const wsDiv = document.createElement('div');
    wsDiv.className = 'iframe-tab';
    wsDiv.id = 'iframe-' + id;
    wsDiv.style.backgroundColor = 'var(--bg)';
    wsDiv.style.display = 'none';
    wsDiv.innerHTML = `
      <main style="flex:1; display:flex; flex-direction:column; height:100%;">
        <div style="height:54px; padding:0 18px; border-bottom:1px solid var(--border); display:flex; align-items:center; background:var(--topbar-bg); box-shadow:var(--shadow-sm); z-index:10;">
          <h2 style="font-size:16px; font-weight:600;">💬 ${esc(ws.name)}</h2>
        </div>
        <div id="ws-chat-messages-${ws_id}" style="flex:1; overflow-y:auto; padding:22px; display:flex; flex-direction:column; gap:18px;">
           <div class="global-welcome">
             <h3>工作空间：${esc(ws.name)}</h3>
             <p>此对话的 AI 检索范围已被限制为您选定的 ${ws.items.length} 个文件。</p>
           </div>
        </div>
        <div style="padding:18px; border-top:1px solid var(--border); background:var(--surface);">
           <div style="display:flex; gap:10px; align-items:center;">
             <textarea id="ws-chat-input-${ws_id}" style="flex:1; padding:12px; border-radius:8px; border:1px solid var(--border);" placeholder="在当前工作空间提问..." rows="1" onkeydown="if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();sendWorkspaceChat('${ws_id}');}"></textarea>
             <button style="padding:12px 24px; border-radius:8px; background:var(--accent); color:white; border:none; cursor:pointer;" onclick="sendWorkspaceChat('${ws_id}')">➤</button>
           </div>
        </div>
      </main>
    `;
    document.getElementById('views-container').appendChild(wsDiv);
  }
  
  TabManager.openTab(id, 'workspace', '💬 ' + ws.name, {ws_id});
}

window.sendWorkspaceChat = async function(ws_id) {
  const input = document.getElementById('ws-chat-input-' + ws_id);
  const q = input.value.trim();
  if(!q) return;
  input.value = '';
  
  const msgContainer = document.getElementById('ws-chat-messages-' + ws_id);
  msgContainer.innerHTML += `<div style="align-self:flex-end; background:var(--accent); color:white; padding:12px; border-radius:12px;">${esc(q)}</div>`;
  msgContainer.scrollTop = msgContainer.scrollHeight;
  
  // Use library-chat API but pass workspace_id
  const payload = {
    question: q,
    session_id: 'session_' + ws_id,
    workspace_id: ws_id
  };
  
  try {
      const resp = await fetch('/api/library-chat/ask', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if(data.error) throw new Error(data.error);
      
      const md = window.markdownit ? window.markdownit() : {render: t=>t};
      const answerHtml = md.render(data.answer || "");
      msgContainer.innerHTML += `<div style="align-self:flex-start; background:var(--surface); border:1px solid var(--border); padding:12px; border-radius:12px; max-width:80%;">${answerHtml}</div>`;
      msgContainer.scrollTop = msgContainer.scrollHeight;
  } catch(e) {
      msgContainer.innerHTML += `<div style="align-self:flex-start; color:var(--rose);">Error: ${e.message}</div>`;
  }
}

function getSelectedExplorerItems() {
  const checkboxes = document.querySelectorAll('.explorer-checkbox:checked');
  let items = [];
  checkboxes.forEach(cb => {
    items.push({
      item_type: cb.getAttribute('data-type'),
      item_id: cb.getAttribute('data-id')
    });
  });
  return items;
}

async function batchDeleteSelection() {
  const items = getSelectedExplorerItems();
  if(!items.length) return uiAlert('未选中任何文件');
  if(!confirm(`确定删除这 ${items.length} 个文件吗？`)) return;
  
  await fetch('/api/batch/delete', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({items})
  });
  await renderExplorer();
}

async function batchExportSelection() {
  const items = getSelectedExplorerItems();
  if(!items.length) return uiAlert('未选中任何文件');
  
  const resp = await fetch('/api/batch/export', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({items})
  });
  if(!resp.ok) return uiAlert('导出失败');
  
  const blob = await resp.blob();
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `kbase_export_${Date.now()}.zip`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function batchImportMd() {
  // Not implemented in backend yet, show alert
  uiAlert('批量导入功能即将开放！您可以先通过【上传资料】逐个上传。');
}

document.addEventListener('DOMContentLoaded', () => {
  renderExplorer();
});

async function batchAddToWorkspace() {
  const items = getSelectedExplorerItems();
  if(!items.length) return uiAlert('未选中任何文件');
  
  if(!workspaces.length) return uiAlert('请先在管理面板新建一个工作空间');
  
  let msg = "请输入要加入的工作空间编号:\n";
  workspaces.forEach((w, i) => {
    msg += `[${i+1}] ${w.name}\n`;
  });
  const input = prompt(msg);
  if(!input) return;
  
  const index = parseInt(input) - 1;
  if(isNaN(index) || index < 0 || index >= workspaces.length) return uiAlert('输入无效');
  
  const ws = workspaces[index];
  
  const resp = await fetch('/api/workspaces/' + encodeURIComponent(ws.id) + '/items', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({items})
  });
  if(resp.ok) {
    uiAlert(`成功将 ${items.length} 个文件加入工作空间: ${ws.name}`);
    await renderExplorer();
  } else {
    uiAlert('加入工作空间失败');
  }
}
