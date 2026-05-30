# Knowledge Base — 论文知识库

> [English](README.md)

基于 [Marker](https://github.com/VikParuchuri/marker) PDF 解析引擎的本地论文知识库，支持 PDF 上传/自动解析、Markdown 阅读、AI 对话、翻译、总结。

## 功能

- **PDF 解析** — 支持多种引擎：Marker 本地模型 / 阿里云 DocMind API
- **文库管理** — 卡片/看板视图，搜索、分类、标签、删除
- **文献阅读** — 三栏布局（Markdown + AI 对话 + PDF 原文），可拖拽调整宽度
- **AI 对话** — 基于论文全文的上下文对话（OpenAI 兼容 API）
- **一键翻译** — 逐段调用 LLM 翻译为中文，结果持久化
- **AI 总结** — 自动提取研究背景、方法、发现、贡献
- **重新解析** — 切换引擎重新解析，历史版本可对比
- **笔记系统** — 功能完整的 Markdown 笔记（详见 [笔记功能](#笔记功能)）
- **LaTeX 渲染** — KaTeX 实时渲染数学公式

### 笔记功能

内置笔记系统，灵感来自 [思源笔记](https://b3log.org/siyuan/)，点击顶栏 📝 按钮即可进入。

**编辑器**
- WYSIWYG Markdown 编辑器（ToastUI Editor），支持实时预览
- 斜杠命令菜单：输入 `/` 可快速插入标题、列表、代码块、表格、公式、Mermaid 图等
- 代码语法高亮（highlight.js），显示语言标签
- KaTeX 数学公式渲染（行内 `$...$` 和独立 `$$...$$`）
- Mermaid 图表渲染（流程图、时序图等）

**组织管理**
- 文件夹分组，侧边栏树形结构可折叠
- 标签系统，彩色标签药丸
- 笔记标题全文搜索
- 每日笔记：一键创建当天日期笔记（存入 `daily/` 文件夹）

**双向链接**
- 使用 `[[笔记标题]]` 语法在笔记间相互引用
- 反向链接面板：自动显示所有引用当前笔记的其他笔记
- 点击 wiki 链接跳转；点击虚线链接可创建新笔记

**键盘快捷键**

| 快捷键 | 功能 | 快捷键 | 功能 |
|---|---|---|---|
| `/` | 斜杠命令菜单 | `@` | 引用其他笔记 |
| `Ctrl+S` | 保存 | `Ctrl+D` | 复制当前行 |
| `Ctrl+B` | 加粗 | `Ctrl+I` | 斜体 |
| `Ctrl+U` | 下划线 | `Ctrl+Shift+S` | 删除线 |
| `Ctrl+G` | 行内代码 | `Alt+D` | 高亮（标记） |
| `Ctrl+K` | 链接 | `Ctrl+M` | 行内公式 |
| `Ctrl+Shift+K` | 代码块 | `Ctrl+Shift+L` | 切换任务状态 |
| `Ctrl+Shift+H` | 循环标题级别 | `Ctrl+Shift+D` | 删除当前行 |
| `Ctrl+Enter` | 下方插入空行 | `Ctrl+Shift+Enter` | 上方插入空行 |
| `Ctrl+]` / `Ctrl+[` | 增加/减少缩进 | `Tab` / `Shift+Tab` | 缩进/反缩进 |
| `Ctrl+Shift+T` | 插入日期时间 | `Escape` | 返回文库 |

**阅读视图快捷键**

| 快捷键 | 功能 | 快捷键 | 功能 |
|---|---|---|---|
| `T` | 打开/关闭目录 | `N` | 打开/关闭笔记 |
| `E` | 切换编辑模式 | `L` | 开始翻译 |
| `S` | 保存编辑 | | |

点击笔记顶栏的 ⌨️ 按钮查看完整快捷键列表。

**数据存储**
- 笔记存储为 `kb/notes/` 下的 `.md` 文件
- 元数据（标题、标签、文件夹）保存在 `kb/notes_index.json`
- 默认已加入 `.gitignore`

## 环境要求

| 依赖 | 说明 | 安装 |
|------|------|------|
| **Python** | 3.10+（推荐 3.13） | [python.org](https://www.python.org/) |
| **PyTorch** | 2.x + CUDA 12.x（本地引擎 GPU 可选） | `pip install torch` |
| **marker-pdf** | Marker 本地解析引擎 | `pip install marker-pdf` |
| **PyMuPDF** | PDF 预览 / 元数据提取 | `pip install pymupdf` |
| **pywebview** | 桌面窗口 | `pip install pywebview` |
| **huggingface_hub** | 模型下载（可选） | `pip install huggingface_hub` |
| **alibabacloud_docmind_api20220711** | DocMind 云端引擎（可选） | `pip install alibabacloud_docmind_api20220711` |

桌面模式下 pywebview 依赖系统 WebView2（Win11 自带，Win10 需安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)）。

### 识别引擎

| 引擎 | 类型 | 显存/GPU | 说明 |
|------|------|---------|------|
| **Marker** | 本地 | 需要 | Surya 模型，离线可用，需先下载模型 |
| **DocMind** | 云端 API | 不需要 | 阿里云文档解析，需配置 RAM AccessKey |

### Marker 本地引擎显存要求

> 以下**仅适用于**使用 Marker 本地引擎的情况。若选用 DocMind 云端引擎则无需 GPU。

Marker 默认会同时加载 4 个 Surya 模型，实测显存占用：

| GPU 显存 | 状态 | 建议 |
|---------|------|------|
| 4 GB | ❌ 无法运行 | 必须 CPU 模式 |
| **6 GB** | ⚠️ 勉强可用 | 需关掉浏览器 PDF、桌面应用占显存 |
| 8 GB | ✅ 正常 | 推荐 |
| 12 GB+ | ✅ 充裕 | 可用默认 batch size |

> **注意**：浏览器打开的 PDF 预览会占用 ~200-500MB GPU 显存，解析时可能导致 CUDA OOM 或 `CUBLAS_STATUS_EXECUTION_FAILED`。网页已内置 GPU 冲突监测，解析时自动隐藏 PDF。

### CPU 模式（Marker）

如 GPU 显存不足，在设置中选 CPU 模式。32GB 系统内存约 5-15 分钟完成一篇 10 页论文。

## 快速开始

```bash
# 1. 一次性安装所有依赖
pip install marker-pdf pymupdf pywebview alibabacloud_docmind_api20220711

# 2. 配置 LLM API（OpenAI 兼容格式）
cp local.env.example local.env
# 编辑 local.env，填入你的 API key、URL、model

# 3. 首次运行自动下载 Surya 模型（~3.5 GB，仅一次）
python kb/desktop.py
```

### 配置 DocMind 云端引擎（可选）

如需使用阿里云 DocMind 解析（无需 GPU），在 `local.env` 中配置 RAM 凭证：

```env
DOCMIND_ACCESS_KEY_ID=LTAI5t...  # RAM AccessKey ID
DOCMIND_ACCESS_KEY_SECRET=...     # RAM AccessKey Secret
DOCMIND_REGION=cn-hangzhou
```

获取方式：
1. 开通 [阿里云文档智能](https://www.aliyun.com/product/ai/docmind) 服务
2. 在 [RAM 访问控制](https://ram.console.aliyun.com/profile/access-keys) 创建 AccessKey
3. 填入 `local.env`，重启应用即可选择 DocMind 引擎

模型默认缓存到 `models/` 目录。本仓库**不含**模型文件（`models/` 在 `.gitignore` 排除）。

### 手动下载模型（网络不佳时）

```bash
pip install huggingface_hub
python -c "
from huggingface_hub import snapshot_download
base = 'models'
snapshot_download('vikp/surya_det3', local_dir=f'{base}/vikp--surya_det3')
snapshot_download('vikp/surya_rec3', local_dir=f'{base}/vikp--surya_rec3')
snapshot_download('vikp/surya_layout3', local_dir=f'{base}/vikp--surya_layout3')
snapshot_download('vikp/surya_order2', local_dir=f'{base}/vikp--surya_order2')
snapshot_download('vikp/surya_tablerec', local_dir=f'{base}/vikp--surya_tablerec')
"
```

也可通过环境变量指定模型目录：
```bash
$env:MODEL_CACHE_DIR="D:/path/to/models"   # PowerShell
export MODEL_CACHE_DIR=/path/to/models      # Bash
```

### 启动方式

| 方式 | 命令 | 说明 |
|------|------|------|
| **桌面应用** | `python kb/desktop.py` | 原生窗口，关闭即停服（推荐） |
| **Web 服务** | `cd kb && python serve.py` | 浏览器访问 `http://localhost:8765` |

### 桌面应用功能

- 三栏可切换排版：`📄💬📑` → `📄📑💬` → `💬📄📑`（点击 🔄 循环）
- 拖拽分隔线自由调整栏宽
- 每栏可独立显示/隐藏
- 文本可选择复制（`text_select` 启用）

## 项目结构

```
kbase/
├── kb/                     # 知识库应用
│   ├── desktop.py           # 桌面应用入口（pywebview）
│   ├── serve.py             # HTTP 服务端（API + 解析调度）
│   ├── index.html           # 前端单页应用
│   ├── engines/             # 解析引擎（Marker / DocMind）
│   ├── kb-index.json        # 文库索引
│   ├── low_memory_config.json # 用户设置（设备、保护模式）
│   ├── llm_config.json        # LLM provider 配置（运行时）
│   ├── articles/           # 解析成果（每篇一个文件夹）
│   │   └── {id}/
│   │       ├── original.pdf
│   │       ├── {id}.md
│   │       ├── {id}_marker.md   # 按引擎保留的历史版本
│   │       ├── {id}_docmind.md
│   │       └── {id}_meta.json
│   ├── notes/              # 笔记文件（运行时，gitignored）
│   │   └── {note_id}.md
│   └── notes_index.json    # 笔记元数据索引（运行时，gitignored）
├── marker/                 # Marker PDF 解析引擎（Surya 模型）
└── models/                 # Surya 模型缓存（~3.5 GB）
```

## 常用命令

### 单文件解析

```bash
marker_single paper.pdf
```

输出到 `kb/articles/{pdf_name}/`，包含 `{name}.md`、`{name}_meta.json` 及提取的图片。

### 批量解析

```bash
marker /path/to/pdf_folder
```

### 常用参数

| 参数 | 说明 |
|------|------|
| `--output_dir PATH` | 输出目录，默认 `kb/articles` |
| `--output_format FORMAT` | `markdown`（默认）、`json`、`html`、`chunks` |
| `--page_range RANGE` | 指定页面，如 `0,5-10,20` |
| `--debug` | 调试模式 |
| `--disable_image_extraction` | 不提取图片 |
| `--config_json PATH` | 附加 JSON 配置文件 |

示例：

```bash
# 只解析前 5 页，输出 JSON
marker_single paper.pdf --page_range 0-4 --output_format json

# 批量解析，指定输出目录
marker ./pdfs --output_dir ./results
```

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/articles` | 获取文库列表 |
| POST | `/api/upload` | 上传 PDF（multipart） |
| POST | `/api/chat` | LLM 代理 |
| GET/PUT | `/api/llm-config` | LLM provider 配置 |
| POST | `/api/convert/{id}` | 触发解析（body: `{id, engine}`） |
| POST | `/api/articles/delete` | 删除文章 |
| PUT | `/api/articles/update` | 更新元数据 |
| PUT | `/save` | 保存文件内容 |
| GET | `/api/conversion-status/{id}` | 解析进度/日志 |
| GET | `/api/conversion-history/{id}` | 解析历史与版本列表 |
| GET | `/api/notes` | 获取笔记列表 |
| POST | `/api/notes` | 创建笔记（body: `{title, folder}`） |
| GET | `/api/notes/{id}` | 获取笔记内容和元数据 |
| PUT | `/api/notes/{id}` | 保存笔记（body: `{content, title, tags, folder}`） |
| DELETE | `/api/notes/{id}` | 删除笔记 |
| PUT | `/api/notes/{id}/rename` | 重命名笔记（body: `{title}`） |

## 依赖与致谢

本项目基于以下开源项目：

- **[Marker](https://github.com/VikParuchuri/marker)** — PDF → Markdown 解析引擎（GPL-3.0）
- **[Surya](https://github.com/VikParuchuri/surya)** — 文档 OCR / 布局 / 文字识别模型（GPL-3.0）
- **[KaTeX](https://katex.org/)** — LaTeX 渲染
- **[Toast UI Editor](https://ui.toast.com/tui-editor)** — WYSIWYG Markdown 编辑器
- **[marked](https://marked.js.org/)** — Markdown 解析
- **[highlight.js](https://highlightjs.org/)** — 代码语法高亮
- **[Mermaid](https://mermaid.js.org/)** — 图表渲染
- **[思源笔记](https://b3log.org/siyuan/)** — 笔记功能 UI/UX 设计灵感

## 许可

本项目沿用 Marker 的开源协议：

- **代码**：[GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html)
- **模型**：[OpenRAIL-M](https://www.datalab.to/pricing)

## AI 请看这里

本节供 Claude Code、GitHub Copilot、Codex 等 AI Agent 阅读，帮助它们理解项目上下文并协助用户完成环境搭建。

### 项目概览

- 这是一个基于 Marker PDF 解析引擎的**本地论文知识库桌面应用**
- `marker/` 目录是 Marker 的 Python 源码（从 [VikParuchuri/marker](https://github.com/VikParuchuri/marker) 集成），不是 git submodule
- `kb/` 目录是知识库 Web 应用：`serve.py` 后端、`index.html` 前端、`desktop.py` pywebview 桌面入口
- `kb/engines/` 是可插拔解析引擎：`marker.py`（本地 Surya）、`docmind.py`（阿里云 API）
- `kb/notes/` 存储笔记 `.md` 文件，`kb/notes_index.json` 存储元数据（标题、标签、文件夹）— 均已 gitignore
- 笔记使用 ToastUI Editor（WYSIWYG + Markdown 双模式），支持 `[[标题]]` 双向链接语法
- 仓库**不含** Surya 模型文件，模型在 `.gitignore` 中排除，需单独下载

### 环境搭建步骤

当用户请求帮助安装或配置本项目时，按以下顺序操作：

1. **Python 环境** — 确认 Python 3.10+ 可用（`python --version`）
2. **安装依赖** — `pip install marker-pdf pymupdf pywebview alibabacloud_docmind_api20220711`。如果用户有 GPU，确认 PyTorch CUDA 版本匹配。Win10 用户可能需要额外安装 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
3. **下载模型** — 仅 Marker 本地引擎需要。Surya 模型必须放在 `{repo_root}/models/` 目录下（`serve.py` 启动时设置 `MODEL_CACHE_DIR` 指向该路径）。模型约 3.5 GB，两种方式二选一：
   - 自动下载：首次运行 `python kb/desktop.py` 或 `marker_single --help` 会自动拉取
   - 手动下载（网络不佳时）：
     ```bash
     pip install huggingface_hub
     python -c "
     from huggingface_hub import snapshot_download
     base = 'models'
     snapshot_download('vikp/surya_det3', local_dir=f'{base}/vikp--surya_det3')
     snapshot_download('vikp/surya_rec3', local_dir=f'{base}/vikp--surya_rec3')
     snapshot_download('vikp/surya_layout3', local_dir=f'{base}/vikp--surya_layout3')
     snapshot_download('vikp/surya_order2', local_dir=f'{base}/vikp--surya_order2')
     snapshot_download('vikp/surya_tablerec', local_dir=f'{base}/vikp--surya_tablerec')
     "
     ```
4. **配置环境** — 复制 `local.env.example` 为 `local.env`，填入：
   - LLM API（OpenAI 兼容）：可在应用设置中配置，也可使用 `LLM_API_KEY`、`LLM_API_URL`、`LLM_MODEL`
   - DocMind 云端引擎（可选）：`DOCMIND_ACCESS_KEY_ID`、`DOCMIND_ACCESS_KEY_SECRET`、`DOCMIND_REGION`（获取方式：阿里云 RAM 控制台 → 创建 AccessKey）
5. **启动** — `python kb/desktop.py`（桌面原生窗口）或 `cd kb && python serve.py`（Web 模式，浏览器打开 `http://localhost:8765`）

### 关键约束

- `local.env` 在 `.gitignore` 中，**绝对不能提交或展示其内容**
- 模型目录 `{repo_root}/models/` 在 `.gitignore` 中，**不要提交模型文件**。Surya 模型按 `models/vikp--{model_name}/` 的子目录结构存放
- `kb/articles/` 是运行时生成的文章目录，已在 `.gitignore`（含 `upload_queue/`、`conversion_temp/`、`kb-index.json`、`low_memory_config.json`、`llm_config.json`、`kb/notes/`、`kb/notes_index.json`）
- **Marker 本地引擎** 约需 6-8 GB GPU 显存；如显存不足可选 CPU 模式或改用 DocMind 云端引擎
- DocMind 云端引擎无需 GPU，通过阿里云官方 SDK 调用，提交后约 3-10 秒完成
- 不要在 `CLAUDE.md` 中写入 API key 或敏感信息
- **桌面应用**：`kb/desktop.py` 依赖系统 WebView2。启动时自动开启 HTTP 服务，关闭窗口自动停服
- **引擎架构**：新引擎只需在 `kb/engines/` 下实现 `run(pdf_path, article_id, log_callback)` 并在 `__init__.py` 注册即可
