import re

with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove empty placeholders
content = content.replace('<div id="reader-view" class="iframe-tab"></div>\n', '')
content = content.replace('<div id="reader-view" class="iframe-tab"></div>', '')
content = content.replace('<div id="notes-view" class="iframe-tab"></div>\n', '')
content = content.replace('<div id="notes-view" class="iframe-tab"></div>', '')

# Extract reader-view block
reader_start_str = '<!-- ==================== READER VIEW ==================== -->\n<div id="reader-view"'
reader_start_idx = content.find(reader_start_str)

reader_end_str = '</div>\n\n  <div class="resize-handle" id="global-chat-handle"'
reader_end_idx = content.find(reader_end_str, reader_start_idx)

reader_block = content[reader_start_idx:reader_end_idx] + '</div>\n'
content = content[:reader_start_idx] + content[reader_end_idx+7:]

# Now reader_block is extracted. Let's add class="iframe-tab" to its first line.
reader_block = reader_block.replace('<div id="reader-view" ondragover', '<div id="reader-view" class="iframe-tab" ondragover')

# Extract global-chat-handle and chat-column
chat_start_str = '<div class="resize-handle" id="global-chat-handle"'
chat_start_idx = content.find(chat_start_str)
chat_end_str = '  </div>\n\n<!-- ==================== NOTES VIEW ==================== -->'
chat_end_idx = content.find(chat_end_str, chat_start_idx)
chat_block = content[chat_start_idx:chat_end_idx] + '  </div>\n'
content = content[:chat_start_idx] + content[chat_end_idx+9:]

# Insert chat_block into reader_block right before the last </div> of reader-view.
reader_block = reader_block[:-7] + chat_block + '</div>\n'

# Extract notes-view
notes_start_str = '<!-- ==================== NOTES VIEW ==================== -->\n<div id="notes-view">'
notes_start_idx = content.find(notes_start_str)
notes_end_str = '  </div>\n</div>\n\n<!-- ==================== FLOATING NOTE ==================== -->'
notes_end_idx = content.find(notes_end_str, notes_start_idx)
notes_block = content[notes_start_idx:notes_end_idx] + '  </div>\n</div>\n'
content = content[:notes_start_idx] + content[notes_end_idx+11:]

# Add class="iframe-tab" to notes-view
notes_block = notes_block.replace('<div id="notes-view">', '<div id="notes-view" class="iframe-tab">')

# Insert reader_block and notes_block into views-container
views_start_idx = content.find('<div id="views-container">')
library_view_end_str = '</div>\n\n    </div> <!-- End views-container -->'
library_view_end_idx = content.find(library_view_end_str, views_start_idx)
content = content[:library_view_end_idx+7] + reader_block + '\n' + notes_block + '\n' + content[library_view_end_idx+7:]

# Left Sidebar
left_sidebar_html = """
<aside id="left-sidebar">
  <div class="ls-top">
    <div class="ls-item" onclick="TabManager.activateTab('home')"><span class="icon">📝</span> 新对话</div>
    <div class="ls-item"><span class="icon">🔍</span> 搜索</div>
    <div class="ls-item"><span class="icon">🧩</span> 插件</div>
    <div class="ls-item"><span class="icon">⚙️</span> 自动化</div>
  </div>
  <div class="ls-scroll">
    <div class="ls-section">
      <div class="ls-section-title">项目</div>
      <div id="ls-workspaces-list"></div>
    </div>
    <div class="ls-section">
      <div class="ls-section-title">对话</div>
      <div id="ls-chats-list"></div>
    </div>
  </div>
  <div class="ls-bottom">
    <div class="ls-item" onclick="showSettings()"><span class="icon">⚙️</span> 设置</div>
  </div>
</aside>
"""
content = content.replace('<div id="app-body"', left_sidebar_html + '\n<div id="app-body"')

# Right Sidebar resize handle
content = content.replace('<aside id="library-side">', '<div class="resize-handle" id="library-side-handle" onmousedown="startLibrarySideResize(event)"></div>\n<aside id="library-side">')

with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
print('Done HTML Refactoring!')
