with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update onclick handlers to use openNoteTab
content = content.replace("onclick=\"openNote('${esc(n.id)}')\"", "onclick=\"openNoteTab('${esc(n.id)}')\"")
content = content.replace("onclick=\"openNote('${esc(singleId)}');", "onclick=\"openNoteTab('${esc(singleId)}');")

# 2. Update TabManager.activateTab to call openNote
old_activate = "} else if (tab.type === 'note' || tab.type === 'md') {\n       document.getElementById('notes-view').classList.add('active');\n       // init notes view\n    }"

new_activate = "} else if (tab.type === 'note' || tab.type === 'md') {\n       document.getElementById('notes-view').classList.add('active');\n       if (window.currentNoteId !== tab.data.id) {\n           openNote(tab.data.id);\n       }\n    }"

content = content.replace(old_activate, new_activate)

with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
