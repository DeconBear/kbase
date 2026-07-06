# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('kb/index.html', 'kb'), ('kb/assets', 'kb/assets')]
binaries = []
hiddenimports = ['kb.utils_yaml', 'kb.storage', 'kb.database', 'kb.app_config', 'kb.workspace', 'kb.derivations', 'kb.workspace_paths', 'kb.workspace_watch', 'kb.workspace_search', 'kb.workspace_index', 'kb.literature_classify', 'kb.workspace_ingest', 'kb.literature_organize', 'kb.word_extract', 'kb.legacy_bridge', 'kb.bookmarks', 'kb.cli', 'kb.engines._paths', 'kb.llm_config', 'kb.document_info', 'kb.translate', 'kb.calibrate', 'kb.library_chat', 'kb.engines', 'kb.engines.marker', 'kb.engines.docmind', 'kb.engines.docparser', 'kb.engines.vision_ocr', 'kb.engines.ocr', 'kb.engines.llm_vision', 'kb.engines.unisound_parser', 'kb.serve', 'kb.version', 'kb.updater', 'webview', 'fitz', 'clr']
tmp_ret = collect_all('pythonnet')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('clr_loader')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['kb\\desktop.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'transformers', 'numpy', 'scipy', 'PIL', 'pillow', 'cv2', 'pandas', 'matplotlib', 'tkinter', '_tkinter', 'aiohttp', 'cryptography', 'openpyxl', 'lxml', 'sklearn', 'scikit-learn', 'surya_ocr', 'surya', 'tqdm', 'rapidfuzz', 'pdftext', 'markdownify', 'ftfy', 'filetype', 'google_genai', 'anthropic', 'marker', 'datasets', 'huggingface_hub', 'fontTools', 'kiwisolver', 'contourpy', 'cycler', 'zstandard', 'brotlicffi', 'apscheduler', 'rich', 'pygments', 'jinja2', 'pytest', 'yarl', 'multidict', 'frozenlist', 'propcache', 'attr', 'attrs'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KBase',
    debug=all,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['kb\\assets\\kbase-logo.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='KBase',
)
