with open('e:/BaiduSyncdisk/research/Programming_Development/prodev/klynx-dev/repos/kbase/kb/index.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if '<div id="views-container">' in line:
            print(f'views_container: {i}')
        if '<!-- ==================== READER VIEW ====================' in line:
            print(f'reader_view: {i}')
        if '<div class="resize-handle" id="global-chat-handle"' in line:
            print(f'global_chat_handle: {i}')
        if '<!-- ==================== NOTES VIEW ====================' in line:
            print(f'notes_view: {i}')
        if '<!-- ==================== FLOATING NOTE ====================' in line:
            print(f'floating_note: {i}')
