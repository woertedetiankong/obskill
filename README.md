# obskill · Obsidian Experience

把文章、人物收藏、互动作品、对话和开发经验整理到 Obsidian，通过清晰导航和自然语言检索找回需要的内容。

这是一个 **skill＋配套 Python 脚本**。笔记保持为普通 Markdown，检索默认在本机 CPU 上运行，无需 Obsidian 插件、Ollama、显卡或 API Key。

## 能做什么

- **保存并整理内容**：文章、摘录、人物／作者收藏、SVG/HTML 作品，以及 Codex、Claude Code 对话。
- **便于阅读和维护**：对话使用彩色卡片，保留图片与代码；提供统一命名、元数据约定和阅读笔记／经验模板。
- **分类和导航**：按内容用途归档，维护首页及主题／人物链接；整理移动时修复链接、保留来源，并刷新检索索引。
- **查找笔记**：支持关键词、语义和混合检索，返回原文片段、文件路径和行号。
- **复用开发经验**：按项目、模块、路径和状态筛选，沿替代关系找到当前有效的经验。
- **维护索引**：复用未变化的片段，更新变化内容；删除索引后可从原始笔记重建。

保存文章、整理目录与导航、提炼经验和判断适用性由 AI 按 skill 指引完成；脚本负责对话导出、检索和索引等确定性操作。检索得到的笔记仍需阅读并核对适用条件。

## 安装

仓库中的完整 skill 位于 [`skills/obsidian-experience`](skills/obsidian-experience)。下载或克隆本仓库：

```sh
git clone https://github.com/woertedetiankong/obskill.git
```

把 `skills/obsidian-experience` 整个目录放进 Codex 的 skills 目录。默认位置为 `~/.codex/skills/obsidian-experience`；使用自定义 `CODEX_HOME` 时，放进对应的 `skills` 目录。保留目录内的 `scripts`、`references` 和 `assets`。已有版本时，更新前保留自己的修改。

本地检索需要 `uv` 命令可用。脚本声明了 Python 3.11–3.13 和固定版本依赖，首次运行时由 uv 准备独立环境；也可以自行创建环境并安装脚本声明的依赖。对话导出使用 Python 3.9+ 标准库。

安装后，可在对话中指定使用 `obsidian-experience`：

> 初始化 Obsidian 语义检索，下载本地模型并给我的笔记建索引。

AI 会根据你的环境确定笔记库位置，下载模型、建立索引并试查。不会使用作者的笔记库路径。

## 首次建立索引

以下命令在安装后的 `obsidian-experience` 目录中执行，把 `/path/to/vault` 替换为自己的笔记库路径：

```sh
# 查看模型状态、大小和保存位置。
uv run scripts/retrieval.py model-status

# 显式下载并校验模型。
uv run scripts/retrieval.py download-model

# 建立或增量更新索引。
uv run scripts/retrieval.py index --vault "/path/to/vault"

# 查询覆盖情况。
uv run scripts/retrieval.py status --vault "/path/to/vault"
```

默认模型是 `jinaai/jina-embeddings-v2-base-zh` 的量化 ONNX 版本，支持中英文。模型与分词器合计 **163,596,011 字节，约 164 MB**；Python 环境和依赖另外下载。模型从固定的 Hugging Face 官方仓库版本下载，经过 SHA-256 校验，支持进度、失败重试、已有文件复用和离线文件导入。

模型文件在所有笔记库之间共享，默认保存在 `~/.cache/obsidian-experience/models/`。每个笔记库有独立的 `~/.cache/obsidian-experience/<vault-identity>/index.sqlite3`。这些缓存不属于笔记内容，也不需要放入本仓库。

模型和运行环境准备完成后，可用 `uv run --offline ...` 运行。检索时笔记和问题留在本机；普通搜索不会自动下载模型。模型缺失时，混合检索会给出诊断并保留关键词结果。

## 使用示例

直接对 AI 说：

- “找一下之前数据库写入一直等待的问题。”
- “把这段对话保存到 Obsidian，标题叫数据库并发写入排查。”
- “查一下 atlas 项目里，已经确认有效的数据库经验。”

也可以手动调用脚本：

```sh
# 默认混合检索：关键词＋语义。
uv run scripts/retrieval.py search "几个操作互相等着，写不进去" --vault "/path/to/vault"

# 纯关键词检索，不执行模型推理。
uv run scripts/retrieval.py search "busy_timeout" --mode fts --vault "/path/to/vault"

# 只检索当前项目及明确标记为通用的有效经验。
uv run scripts/retrieval.py recall "数据库写入等待" --project atlas --vault "/path/to/vault"
```

`search` 可以查普通文章、对话和历史材料；`recall` 只推荐符合项目范围且处于 `active` 状态的经验。保存对话不会自动将其中的建议提升为有效经验。

已有 Ollama 用户可在命令中加 `--backend ollama`，但默认本地模型不依赖它。切换模型或后端后需要运行 `index`，不同模型的向量不能混用。

## 详细说明

- [Skill 工作流程](skills/obsidian-experience/SKILL.md)
- [命名、阅读布局和长期维护](skills/obsidian-experience/references/collection.md)
- [检索、模型下载、离线导入和索引管理](skills/obsidian-experience/references/retrieval.md)
- [对话选择、导出、图片和样式](skills/obsidian-experience/references/history.md)

## 验证与边界

在 skill 目录运行：

```sh
uv run scripts/test_retrieval.py -q
uv run scripts/test_local_model.py -q
python3 -B scripts/test_history.py -q

# 模型下载完成后，用临时笔记库进行实际模型评估。
uv run scripts/evaluate_retrieval.py --output /path/to/report.json
```

当前有 53 项行为／回归测试。小规模合成语料评估中，语义与混合检索各通过 8/8，关键词基线为 7/8；这些结果不代表对任意笔记库的准确率保证。默认相似度门槛针对当前模型做了小样本调整，实际使用中仍需检查检索结果。

目前实测平台为 Apple Silicon macOS；Windows、Linux 和 Intel Mac 尚未完成端到端实测，依赖能否安装取决于对应平台的运行时支持。模型在 CPU 上运行时，实际内存占用高于模型文件大小。

检索读取 Markdown，不对图片、PDF 或 HTML 做 OCR／正文抽取；不会在后台自动同步。笔记发生变化后，在保存或查询流程中运行增量 `index` 即可更新语义索引。
