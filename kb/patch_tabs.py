with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Replace the buttons
old_buttons = '''<div id="library-tabs-switcher" style="display:flex; gap:8px; margin-bottom:8px; padding:0 16px;">
            <button class="ctrl-btn active" onclick="document.getElementById('library-grid').style.display='grid'; document.getElementById('explorer-tree').style.display='none';">所有资料</button>
            <button class="ctrl-btn" onclick="document.getElementById('library-grid').style.display='none'; document.getElementById('explorer-tree').style.display='block';">知识库目录</button>
        </div>'''

new_buttons = '''<div id="library-tabs-switcher" style="display:flex; gap:8px; margin-bottom:8px; padding:0 16px;">
            <button id="tab-btn-grid" class="ctrl-btn active" onclick="switchLibraryTab('grid')">所有资料</button>
            <button id="tab-btn-tree" class="ctrl-btn" onclick="switchLibraryTab('tree')">知识库目录</button>
        </div>
        <script>
        function switchLibraryTab(tab) {
            if (tab === 'grid') {
                document.getElementById('library-grid').style.display = 'grid';
                document.getElementById('explorer-tree').style.display = 'none';
                document.getElementById('tab-btn-grid').classList.add('active');
                document.getElementById('tab-btn-tree').classList.remove('active');
            } else {
                document.getElementById('library-grid').style.display = 'none';
                document.getElementById('explorer-tree').style.display = 'block';
                document.getElementById('tab-btn-grid').classList.remove('active');
                document.getElementById('tab-btn-tree').classList.add('active');
            }
        }
        </script>'''

content = content.replace(old_buttons, new_buttons)

with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'w', encoding='utf-8') as f:
    f.write(content)
