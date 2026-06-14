# KBase

> [English](README.md)

一款由 AI 驱动的、本地优先的知识管理应用，专注于解析学术 PDF，支持 Markdown 笔记，并借助大语言模型协助你的知识工作流。

## 功能

- **文件入库** — 支持上传 PDF、Markdown、纯文本、代码、数据文件（CSV/JSON）、压缩包等。自动识别格式并生成可读 Markdown 预览。
- **PDF 解析** — 多引擎支持：PyMuPDF（上传即预解析）/ Marker（本地 Surya 模型）/ DocParser（云端 GPU）/ DocMind（阿里云 API）。按引擎保留版本历史，可切换对比。
- **文库管理** — 卡片/看板视图，全文搜索，分类标签，批量操作，工作区分组整理。
- **AI 对话** — 基于当前文档的上下文对话，支持跨库 RAG 检索问答（OpenAI 兼容 API）。
- **翻译** — 逐段调用 LLM 翻译，智能复用历史翻译结果，支持增量更新。
- **AI 总结与元数据提取** — 自动提取标题、作者、DOI、年份、期刊、摘要、关键词等信息。
- **笔记系统** — WYSIWYG Markdown 编辑器，文件夹分组，标签，双向 wiki 链接（`[[笔记名]]`），每日笔记，斜杠命令菜单，代码高亮，KaTeX 公式，Mermaid 图表。
- **三栏阅读** — Markdown + AI 对话 + PDF 原文，可拖拽调整宽度，布局可循环切换。

## 快速开始

从 [Releases](https://github.com/DeconBear/kbase/releases) 下载最新版本，解压后双击 `KBase.exe` 即可运行。

或从源码运行：

```bash
# 1. 安装核心依赖
pip install pymupdf pywebview

# 2. 配置 LLM API（OpenAI 兼容格式）
cp local.env.example local.env  # 仅旧版 v0.x 需要；当前版本会自动在 data/ 下生成
# 编辑 local.env，填入 API key、URL、model

# 3. 启动
python kb/desktop.py
```

Marker 本地 PDF 引擎（PyTorch + Surya 模型）为**可选组件**，首次使用时在应用内按需下载。云端引擎（DocParser、DocMind）配置 API key 即可使用，无需本地 GPU。

## 📦 打包与便携化使用

KBase 支持使用 PyInstaller 打包为独立的 Windows 单文件可执行程序或目录：

```bash
python -m PyInstaller --noconfirm KBase.spec
```

**数据存储机制：**
所有用户数据都集中存放在一个 `data/` 根目录下，首次启动时自动创建：
- **源码运行**（`python serve.py`）：`data/` 创建在仓库根目录（即 `kb/` 的父目录），源码目录本身保持干净。
- **打包运行**（`KBase.exe`）：`data/` 创建在可执行文件同级目录。

```
data/
├── articles/                     # 每个上传文件一个文件夹
├── notes/                        # note_*.md 笔记
├── .kbase/
│   ├── index.db                  # SQLite: articles, notes, tags, workspaces, translations
│   ├── chat_sessions/            # 每个 library-chat 会话一个 JSON
│   ├── chat_sessions_index.json
│   └── logs/                     # 每个任务一个 *.log
├── local.env                     # 首次启动自动生成，所有 key 为空
└── llm_config.json               # UI 管理的 LLM provider 列表
```

**`local.env`：** 首次启动 KBase 时自动生成，包含八项已知 key（LLM、DocMind、DocParser）的空值。可直接编辑文件，也可在应用内"设置"页面填写——所有密钥在 UI 中以掩码显示，**绝不以明文展示**。

**便携移动与数据迁移：**
1. **多设备漫游**：将 `KBase.exe` 与 `data/` 文件夹同时复制到 U 盘、任意磁盘或其他 Windows 电脑，直接双击运行。
2. **从旧版源码环境迁移（pre-SQLite 时代）**：若你曾使用旧版的 `kb/kb-index.json`、`kb/notes_index.json`、`kb/library_chat_sessions.json`，运行一次迁移脚本：
   ```bash
   python kb/migrate.py
   ```
   脚本会把 `kb/articles/`、`kb/notes/` 及 JSON 索引全部迁入 `data/`，导入 SQLite 数据库，然后把旧文件重命名为 `*.legacy.json` 保留备份。

## 解析引擎

| 引擎 | 类型 | GPU | 说明 |
|------|------|-----|------|
| **PyMuPDF** | 本地 | 不需要 | 内置 — 上传即自动预解析 |
| **DocParser** | 云端 API | 不需要 | [DeconBear DocParser](https://your-cloud-parser.com)，仅需 API Key |
| **DocMind** | 云端 API | 不需要 | 阿里云文档解析，需配置 RAM AccessKey |
| **Marker** | 本地 | 可选 | PyTorch + Surya 模型（~3.5 GB），首次使用按需下载 |

## 桌面应用

- 原生 Windows 窗口（pywebview + Edge WebView2）
- 启动自动开启 HTTP 服务，关闭窗口自动停服
- 自定义任务栏图标和分组
- 拖拽调整面板布局，点击 🔄 循环切换
- Windows 10 需安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win11 已内置）

## 项目结构

```
kbase/
├── kb/                     # 知识库应用
│   ├── desktop.py           # 桌面应用入口（pywebview）
│   ├── serve.py             # HTTP 服务端 + API 后端
│   ├── index.html           # 前端单页应用
│   ├── engines/             # 可插拔解析引擎
│   │   ├── marker.py         #   Marker（本地 Surya）
│   │   ├── docparser.py      #   DeconBear DocParser（云端）
│   │   └── docmind.py        #   阿里云 DocMind（云端）
│   ├── db_api.py            # SQLite 数据库层
│   ├── llm_config.py        # LLM provider 配置
│   ├── library_chat.py      # 跨库 RAG 检索
│   ├── translate.py         # 后台 Markdown 翻译
│   ├── calibrate.py         # LLM 辅助 MD 校准
│   ├── document_info.py     # 元数据提取（PyMuPDF + LLM）
│   ├── articles/            # 解析产出（每个文件一个文件夹）
│   │   └── {id}/
│   │       ├── original.pdf
│   │       ├── {id}.md
│   │       └── {id}_marker.md / {id}_docparser.md（引擎历史版本）
│   ├── notes/               # 笔记文件（.md）
│   │   └── {note_id}.md
│   └── .kbase/              # SQLite 数据库（运行时）
├── marker/                  # Marker PDF 解析引擎源码
├── kbase.spec               # PyInstaller 打包配置
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/articles` | 获取文库列表 |
| POST | `/api/upload` | 上传文件（multipart） |
| POST | `/api/chat` | LLM 代理（支持流式） |
| GET/PUT | `/api/llm-config` | LLM provider 配置 |
| POST | `/api/convert/{id}` | 触发 PDF 解析 |
| DELETE | `/api/articles/delete` | 删除文章 |
| PUT | `/api/articles/update` | 更新元数据 |
| POST | `/api/translate/{id}` | 启动后台翻译 |
| POST | `/api/extract-info/{id}` | 提取文档元数据 |
| POST | `/api/calibrate/{id}` | LLM 辅助 OCR 校准 |
| POST | `/api/library-chat/ask` | 跨库 RAG 问答 |
| GET/POST | `/api/notes` | 列出 / 创建笔记 |
| GET/PUT/DELETE | `/api/notes/{id}` | 读取 / 保存 / 删除笔记 |
| GET | `/api/workspaces` | 工作区列表 |
| POST | `/api/export` | 导出（BibTeX / PDF ZIP / Markdown ZIP） |
| POST | `/api/install-marker-deps` | 按需安装 Marker 引擎（SSE 流式进度） |

## 快捷键

### 阅读视图

| 快捷键 | 功能 | 快捷键 | 功能 |
|---|---|---|---|
| `T` | 切换目录 | `N` | 切换笔记侧栏 |
| `E` | 切换编辑模式 | `L` | 开始翻译 |
| `S` | 保存编辑 | `Esc` | 返回文库 |

### 笔记编辑器

| 快捷键 | 功能 | 快捷键 | 功能 |
|---|---|---|---|
| `/` | 斜杠命令菜单 | `@` | 引用其他笔记 |
| `Ctrl+S` | 保存 | `Ctrl+B` | 加粗 |
| `Ctrl+I` | 斜体 | `Ctrl+K` | 插入链接 |
| `Ctrl+M` | 行内公式 | `Ctrl+Shift+K` | 代码块 |
| `Ctrl+]` / `Ctrl+[` | 增加/减少缩进 | `Ctrl+Enter` | 下方插入空行 |

## 依赖与致谢

本项目基于以下开源项目：

- **[Marker](https://github.com/VikParuchuri/marker)** — PDF → Markdown 解析引擎（GPL-3.0）
- **[Surya](https://github.com/VikParuchuri/surya)** — 文档 OCR / 布局 / 文字识别模型（GPL-3.0）
- **[PyMuPDF](https://pymupdf.readthedocs.io/)** — PDF 预解析与元数据提取
- **[KaTeX](https://katex.org/)** — LaTeX 渲染
- **[Toast UI Editor](https://ui.toast.com/tui-editor)** — WYSIWYG Markdown 编辑器
- **[marked](https://marked.js.org/)** — Markdown 解析
- **[highlight.js](https://highlightjs.org/)** — 代码语法高亮
- **[Mermaid](https://mermaid.js.org/)** — 图表渲染
- **[DOMPurify](https://github.com/cure53/DOMPurify)** — Markdown 渲染后的 HTML 安全净化

## 许可

本项目沿用 Marker 的开源协议：

- **代码**：[GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html)
- **模型**：[OpenRAIL-M](https://www.datalab.to/pricing)

## Android 预览版

`android/` 目录包含第一版独立 Android 应用。它不依赖 Python 后端服务器；用户在手机端填写 OpenAI-compatible `chat/completions` endpoint、API key 和模型名后，应用直接联网调用云端 API。

当前 Android 范围：

- 通过 OpenAI-compatible API 直接云端聊天
- API key、模型名、endpoint 保存在手机本地
- 可选择文本/Markdown/JSON/XML 文件作为聊天上下文
- APK 暂不内置 Marker/PDF/OCR 解析管线

可以用 Android Studio 打开 `android/` 目录构建，也可以在已配置 Android SDK 和 JDK 17 的命令行中执行：

```powershell
cd android
.\gradlew.bat :app:assembleDebug
```

## AI 请看这里

本节供 Claude Code、GitHub Copilot 等 AI Agent 阅读。

### 项目概览

- 这是一个**由 AI 驱动的、本地优先的知识管理应用** — 解析学术 PDF，支持 Markdown 笔记，并将 LLM 融入整套工作流
- `marker/` 目录是 Marker 源码（从 [VikParuchuri/marker](https://github.com/VikParuchuri/marker) 集成），不是 git submodule
- `kb/` 目录是知识库应用：`serve.py` 后端、`index.html` SPA 前端、`desktop.py` pywebview 桌面入口
- `kb/engines/` 是可插拔解析引擎：`marker.py`（本地）、`docparser.py`（云端）、`docmind.py`（云端）
- 笔记使用 ToastUI Editor，支持 `[[标题]]` 双向链接
- 仓库**不含** Surya 模型（gitignore），Marker 引擎为可选组件，按需安装

### 环境搭建步骤

1. **Python** — 确认 Python 3.10+（`python --version`）
2. **安装** — `pip install pymupdf pywebview` 即可基础运行。Marker 本地引擎依赖（torch、transformers、surya-ocr）为可选项，在应用内按需下载。
3. **配置** — 复制 `local.env.example` 为 `local.env`，配置 LLM API key。DocParser/DocMind 云端引擎需各自 API key。
4. **启动** — `python kb/desktop.py`（桌面原生窗口）或 `cd kb && python serve.py`（浏览器访问 `http://localhost:8765`）

### 关键约束

- `local.env` 在 `.gitignore` 中 — **绝对不能提交或展示其内容**
- 模型目录 `{repo_root}/models/` 在 `.gitignore` 中 — **不要提交模型文件**
- `kb/articles/`、`kb/notes/`、`.kbase/` 是运行时数据，已在 `.gitignore`
- Marker 本地引擎为可选 — DocParser、DocMind 云端引擎无需 GPU
- 不要在 `CLAUDE.md` 中写入 API key 或敏感信息
- 桌面应用启动时自动开启 HTTP 服务，关闭窗口自动停服
- 新引擎只需在 `kb/engines/` 下实现 `run(pdf_path, article_id, log_callback)` 并在 `__init__.py` 注册
