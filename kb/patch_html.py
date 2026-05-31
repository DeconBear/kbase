import sys

with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

def find_line(lines, search_str):
    for i, line in enumerate(lines):
        if search_str in line:
            return i
    return -1

# 1. Add Left Sidebar CSS
css_idx = find_line(lines, '</style>')
if css_idx != -1:
    css_code = """
  /* ===== LEFT SIDEBAR (Codex Style) ===== */
  #left-sidebar {
    width: 260px;
    background: #f8f9fa;
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    height: 100%;
    z-index: 10;
  }
  .ls-header {
    padding: 16px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .ls-header h2 {
    font-size: 16px; margin: 0; font-weight: 600; color: var(--text-strong);
  }
  .ls-new-btn {
    width: calc(100% - 32px);
    margin: 16px;
    padding: 10px;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 6px;
    cursor: pointer;
    font-weight: 500;
    font-size: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
  }
  .ls-new-btn:hover {
    background: #4752c4;
  }
  .ls-section {
    flex: 1;
    overflow-y: auto;
    padding: 8px 16px;
  }
  .ls-section-title {
    font-size: 12px;
    color: var(--text-3);
    margin: 16px 0 8px 0;
    text-transform: uppercase;
    font-weight: 600;
    letter-spacing: 0.5px;
  }
  .ls-item {
    padding: 8px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
    color: var(--text-2);
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }
  .ls-item:hover {
    background: #e9ecef;
  }
  .ls-item.active {
    background: #e3e5e8;
    color: var(--text-strong);
    font-weight: 500;
  }
  .ls-item .icon { font-size: 14px; }
  .ls-item .actions { margin-left: auto; opacity: 0; }
  .ls-item:hover .actions { opacity: 1; }
  
  /* ===== Explorer Tree Styles ===== */
  #explorer-tree { padding: 12px; font-size: 13px; color: var(--text-2); overflow-y: auto; flex:1;}
  .explorer-section { margin-bottom: 24px; }
  .explorer-section-title { font-size: 12px; font-weight: bold; margin-bottom: 8px; color: var(--text-3); text-transform: uppercase; }
  .explorer-folder { margin-bottom: 8px; }
  .explorer-folder-title { font-weight: 600; cursor: pointer; padding: 4px; border-radius: 4px; }
  .explorer-folder-title:hover { background: var(--surface-hover); }
  .explorer-folder-content { padding-left: 16px; margin-top: 4px; }
  .explorer-item { display: flex; align-items: center; padding: 4px; cursor: pointer; border-radius: 4px; gap: 8px;}
  .explorer-item:hover { background: var(--surface-hover); color: var(--accent); }
  .explorer-checkbox { cursor: pointer; }
"""
    lines.insert(css_idx, css_code)

# 2. Add Left Sidebar HTML & views-container restructuring
app_body_idx = find_line(lines, '<div id="app-body">')
if app_body_idx != -1:
    left_sidebar_html = """
    <aside id="left-sidebar">
      <div class="ls-header">
        <h2>KBase AI</h2>
        <button class="icon-btn" title="设置">⚙️</button>
      </div>
      <button class="ls-new-btn" onclick="openNewTab()">
        <span>+</span> 新建对话
      </button>
      <div class="ls-section">
        <div class="ls-section-title">工作空间</div>
        <div id="ls-workspaces-list">
          <!-- workspaces from explorer.js will be injected here -->
        </div>
        
        <div class="ls-section-title">最近对话</div>
        <div id="ls-chats-list">
          <div class="ls-item active"><span class="icon">💬</span> 文献研读：GPT-4架构</div>
          <div class="ls-item"><span class="icon">💬</span> 总结：2024 AI 趋势</div>
        </div>
      </div>
    </aside>
"""
    lines.insert(app_body_idx + 1, left_sidebar_html)

# 3. Add Explorer Tree to Right Sidebar
library_grid_idx = find_line(lines, '<div id="library-grid"></div>')
if library_grid_idx != -1:
    explorer_html = """
        <div id="library-tabs-switcher" style="display:flex; gap:8px; margin-bottom:8px; padding:0 16px;">
            <button class="ctrl-btn active" onclick="document.getElementById('library-grid').style.display='grid'; document.getElementById('explorer-tree').style.display='none';">所有资料</button>
            <button class="ctrl-btn" onclick="document.getElementById('library-grid').style.display='none'; document.getElementById('explorer-tree').style.display='block';">知识库目录</button>
        </div>
        <div id="explorer-tree" style="display:none;"></div>
"""
    lines.insert(library_grid_idx, explorer_html)

# 4. Extract reader-view, global-chat-handle, and notes-view to move them
def extract_block(lines, start_str, end_str=None, end_offset=0):
    start_idx = find_line(lines, start_str)
    if start_idx == -1: return []
    if end_str:
        end_idx = start_idx
        while end_idx < len(lines):
            if end_str in lines[end_idx]:
                break
            end_idx += 1
    else:
        end_idx = start_idx
    block = lines[start_idx:end_idx + end_offset + 1]
    for i in range(start_idx, end_idx + end_offset + 1):
        lines[i] = "" # Blank them out instead of deleting to avoid index shift
    return block

reader_block = extract_block(lines, '<!-- ==================== READER VIEW ==================== -->', '<div class="resize-handle" id="global-chat-handle"', end_offset=-1)
chat_handle_block = extract_block(lines, '<div class="resize-handle" id="global-chat-handle"')
notes_block = extract_block(lines, '<!-- ==================== NOTES VIEW ==================== -->', '<!-- ==================== FLOATING NOTE ==================== -->', end_offset=-1)

# Clean up empty lines
lines = [l for l in lines if l != ""]

# Find views-container and insert the extracted blocks inside it
views_container_idx = find_line(lines, '<div id="views-container">')
if views_container_idx != -1:
    lines = lines[:views_container_idx + 1] + reader_block + chat_handle_block + notes_block + lines[views_container_idx + 1:]

# 5. Add explorer.js script tag
body_end_idx = find_line(lines, '</body>')
if body_end_idx != -1:
    lines.insert(body_end_idx, '  <script src="explorer.js"></script>\n')

with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'w', encoding='utf-8') as f:
    f.writelines(lines)
    print("Done")
