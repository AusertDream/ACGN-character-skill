---
name: ACGN-character.skill
description: "将虚构角色蒸馏成 AI Skill。支持视频/文本数据导入，生成角色设定 + 人格，支持持续进化。"
argument-hint: "[character-name-or-slug]"
version: "1.0.0"
user-invocable: true
allowed-tools: Read, Write, Edit, Bash
---

> **Language / 语言**: This skill supports both English and Chinese. Detect the user's language from their first message and respond in the same language throughout. Below are instructions in both languages — follow the one matching the user's language.
>
> 本 Skill 支持中英文。根据用户第一条消息的语言，全程使用同一语言回复。下方提供了两种语言的指令，按用户语言选择对应版本执行。

## 路由逻辑 / Routing Logic

**本 Skill 是一个统一入口，根据参数决定行为模式。**

### 参数解析规则

当用户调用 `/ACGN-character {arg}` 时：

1. **角色对话模式**：如果 `{arg}` 是一个已存在的角色名（即 `${CLAUDE_SKILL_DIR}/characters/{arg}/SKILL.md` 存在），则：
   - 用 `Read` 工具读取 `${CLAUDE_SKILL_DIR}/characters/{arg}/SKILL.md` 的全部内容
   - 完全按照该文件中的指令行事，进入角色扮演模式
   - 不再执行下方的创建器流程

2. **创建器模式**：如果 `{arg}` 不存在于 characters 目录，或者用户没有传参数，或者用户明确表达要创建/管理角色（如"新建角色"、"list"），则进入下方的创建器流程。

### 执行步骤

收到调用后，**首先执行以下检查**：

```bash
ls ${CLAUDE_SKILL_DIR}/characters/
```

获取所有可用角色列表。然后：

- 如果用户传了参数且匹配到某个角色目录名 → 进入角色对话模式
- 如果用户传了参数但不匹配 → 提示"角色 {arg} 不存在"并列出可用角色，询问是否要创建
- 如果用户没传参数 → 列出可用角色供选择，或进入创建器流程

---

# 角色.skill 创建器（Claude Code 版）

## 触发条件

当用户说以下任意内容时启动：
- `/create-character`
- "帮我创建一个角色 skill"
- "我想蒸馏一个角色"
- "新建角色"
- "给我做一个 XX 的 skill"

当用户对已有角色 Skill 说以下内容时，进入进化模式：
- "我有新文件" / "追加"
- "这不对" / "她不会这样" / "她应该是"
- `/update-character {slug}`

当用户说 `/list-characters` 时列出所有已生成的角色。

---

## 工具使用规则

本 Skill 运行在 Claude Code 环境，使用以下工具：

| 任务 | 使用工具 |
|------|---------|
| 视频对话提取（OCR） | `Bash` → `python3 -m tools.unified_pipeline` （需在项目根目录运行，`conda activate paddleocr`） |
| 读取 PDF 文档 | `Read` 工具（原生支持 PDF） |
| 读取图片截图 | `Read` 工具（原生支持图片） |
| 读取 EPUB 小说 | `Bash` → `python3 -m tools.epub_reader` 转为文本后用 `Read` 读取 |
| 读取 MD/TXT 文件 | `Read` 工具 |
| 写入/更新 Skill 文件 | `Write` / `Edit` 工具 |
| 版本管理 | `Bash` → `python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py` |
| 列出已有 Skill | `Bash` → `ls characters/` |

**基础目录**：Skill 文件写入 `./characters/{slug}/`（相对于本项目目录）。

---

## 主流程：创建新角色 Skill

### Step 1：基础信息录入（3 个问题）

参考 `${CLAUDE_SKILL_DIR}/prompts/intake.md` 的问题序列，只问 3 个问题：

1. **角色名/代号**（必填）
2. **基本信息**（一句话：作品名、身份、种族、外貌，想到什么写什么）
   - 示例：`原神 璃月七星之一 人类 棕发金眸 头戴白色牛角帽`
3. **性格画像**（一句话：性格标签、角色类型、印象）
   - 示例：`工作狂 完美主义者 毒舌但关心人 表面冷淡内心柔软 吐槽役`

除角色名外均可跳过。收集完后汇总确认再进入下一步。

### Step 2：原材料导入

询问用户提供原材料，展示三种方式供选择：

```
原材料怎么提供？

  [A] 视频对话提取（OCR）
      提供视频文件路径或目录，用 OCR 管线提取游戏/VN 对话
      需要对应作品的 ROI 配置文件（configs/*.yaml）
      支持 mp4/mkv/avi/webm 等格式

  [B] 上传文本文件
      PDF / 图片 / TXT / MD
      可以是角色相关的文档、截图、台词集等

  [C] 直接粘贴内容
      把文字复制进来（台词、剧情概要、角色分析等）

可以混用，也可以跳过（仅凭手动信息生成）。
```

---

#### 方式 A：视频对话提取（OCR）

用户提供视频文件路径后，执行以下步骤：

**A0. 环境预检**

在运行任何 OCR 之前，先检查依赖环境：

```bash
# 1. 检查是否存在虚拟环境
python -c "import sys; print(sys.prefix, sys.base_prefix); print('venv:', sys.prefix != sys.base_prefix)"
```

```bash
# 2. 检查 paddleocr 是否已安装
python -c "import paddleocr; print('paddleocr:', paddleocr.__version__)" 2>&1
```

```bash
# 3. 检查是否有其他疑似 OCR 相关包
pip list 2>/dev/null | grep -iE "paddle|ocr|easyocr|rapidocr|tesseract"
```

根据结果判断：

- **paddleocr 已可正常导入** → 直接进入 A1，无需安装
- **pip list 中有 paddleocr 但导入失败**（如 torch DLL 问题）→ 用 `AskUserQuestion` 告知用户："检测到 paddleocr 已安装但导入时报错（可能是依赖冲突），是否仍尝试继续？还是重新安装？"
- **pip list 中有其他 OCR 相关包**（如 easyocr、rapidocr）→ 用 `AskUserQuestion` 询问用户："检测到已安装 {包名}，是否已有可用的 OCR 环境？还是需要安装 paddleocr？"
- **完全没有任何 OCR 包** → 执行安装：
  ```bash
  pip install -r ${CLAUDE_SKILL_DIR}/requirements.txt
  ```
  安装完成后再次验证 `import paddleocr` 是否成功。

**A1. 布局一致性检测**

在跑 OCR 之前，先从每个视频抽取一帧样本截图（取第 30 秒或视频 10% 位置），自行查看判断所有视频的对话框 UI 布局是否一致：

```bash
# 从每个视频抽取一帧样本（使用 PyAV，已在 requirements.txt 中）
python -c "
import av, PIL.Image, numpy as np
container = av.open('{video_path}')
stream = container.streams.video[0]
target_ts = 30  # 秒
stream.seek(int(target_ts / stream.time_base))
for frame in container.decode(video=0):
    img = frame.to_image()
    img.save('./{video_stem}_sample.png')
    break
container.close()
"
```

用 `Read` 工具查看所有样本截图，判断：
- 对话框的位置和大小是否一致
- 名字框的位置和大小是否一致
- 是否有不同 UI 布局（如主线 vs 支线、日常 vs 战斗演出）

判断结果：
- **布局一致** → 所有视频共用一份 ROI 配置
- **布局不一致** → 按布局分组，每组创建独立的 ROI 配置文件（`tools/configs/{work_id}_{group}.yaml`），分组运行 pipeline
- **无法确定** → 用 `AskUserQuestion` 展示截图让用户确认

**A2. ROI 配置**

检查 `${CLAUDE_SKILL_DIR}/tools/configs/` 目录下是否有对应作品的配置文件。如果没有，根据截图中对话框和名字框的位置估算归一化坐标（x, y, w, h 均为 0-1 范围），用 `Write` 工具创建配置文件。如果已有配置，用 `Read` 查看截图确认 ROI 是否仍然匹配。

**ROI 精度验证**：创建或加载 ROI 配置后，必须用样本截图实际验证框选精度。用以下命令将 ROI 区域裁切出来：

```bash
# 用 Pillow 按归一化坐标裁切 name 框和 dialogue 框
# 假设 name_roi=(x, y, w, h)，dialogue_roi=(x, y, w, h)，坐标均为 0-1 归一化值
python -c "
from PIL import Image
img = Image.open('./{video_stem}_sample.png')
W, H = img.size
# name 框
img.crop((int(W*{name_x}), int(H*{name_y}), int(W*({name_x}+{name_w})), int(H*({name_y}+{name_h})))).save('./{video_stem}_name_crop.png')
# dialogue 框
img.crop((int(W*{dialogue_x}), int(H*{dialogue_y}), int(W*({dialogue_x}+{dialogue_w})), int(H*({dialogue_y}+{dialogue_h})))).save('./{video_stem}_dialogue_crop.png')
"
```

用 `Read` 工具查看裁切后的图片，逐项确认：

- **name 框**：裁切区域是否精确包含角色名文字？是否框到了多余内容（如对话文字、UI 装饰）？是否遗漏了部分名字？
- **dialogue 框**：裁切区域是否精确包含完整对话文字？是否框到了名字框的内容？是否遗漏了对话末尾的文字？

如果框选不准确，调整 ROI 坐标后重新裁切验证，直到两个框都精确命中目标内容为止。

**A3. 运行提取**

先激活 conda 环境：`conda activate paddleocr`

单个视频（自动 ROI 检测）：
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.unified_pipeline "{video_path}" --auto-roi \
  --output-dir ./characters/{slug}/knowledge --gpus 2,3
```

单个视频（使用已有配置）：
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.unified_pipeline "{video_path}" --config tools/configs/{work_id}.yaml \
  --output-dir ./characters/{slug}/knowledge --gpus 2,3
```

批量处理全部视频：`python3 -m tools.process_all_videos`

如果存在多组布局，对每组分别用对应配置运行。

提取完成后，用 `Read` 读取 `characters/{slug}/knowledge/` 下的 `.jsonl` 输出文件。也可以用 `text_output.py` 转换为纯文本格式方便分析：
```bash
python3 -m tools.text_output characters/{slug}/knowledge/{video_name}.jsonl
```

如果 OCR 提取失败，常见原因：
- 缺少 PaddleOCR / PaddlePaddle：提示用户安装
- ROI 配置不匹配：重新查看截图调整 ROI 坐标
- 或改用方式 B/C

**A4. 文本质量检查**

OCR 提取完成后，先将 JSONL 转为纯文本台本：
```bash
python3 -m tools.text_output characters/{slug}/knowledge/{video_name}.jsonl
```

然后用 `Agent` 工具召唤一个 subagent 对产出的纯文本台本做语义级别的质量检查。subagent 的任务 prompt 如下（根据实际文件路径替换）：

```
读取以下纯文本台本文件，对内容做语义级别的质量检查：
{逐个列出所有 .txt 文件的路径}

检查以下几类问题：

1. 乱码：明显不是中文/日文的无意义字符串，如 "EKR"、"Ta]"、"OAEE" 混在正文里
2. OCR 残留噪声：句末或句中插入的无关短字符，如 "一 享"、"- 中"、"福"、"七" 等单字噪声
3. 截断：明显不完整的句子（句子突然中断，没有标点结尾）
4. 重复：连续两条完全相同或高度相似的对话
5. 说话人异常：说话人字段是乱码或明显不合理的内容

对于乱码和 OCR 残留噪声这类一眼就能确认是错误的问题，直接在文件中删除或修正。
对于截断、重复、说话人异常这类需要上下文判断的问题，只记录不修改。

最后输出一份质量报告，包含：
- 总行数
- 各类问题的数量统计
- 已自动修正的条目列表（修正前 → 修正后）
- 需要人工确认的条目列表（附行号和原因）
- 整体质量评分（1-5 分）和简要评价
```

收到 subagent 的质量报告后，向用户展示报告摘要。如果整体质量评分 ≥ 3，继续进入 Step 3；如果评分 < 3，建议用户检查 OCR 配置或提供更好的视频源。

---

#### 方式 B：上传文本文件

- **PDF / 图片**：`Read` 工具直接读取
- **Markdown / TXT**：`Read` 工具直接读取
- **EPUB 小说**：先用 `epub_reader.py` 转为纯文本，再用 `Read` 读取：
  ```bash
  cd ${CLAUDE_SKILL_DIR}
  python3 -m tools.epub_reader "{epub_path}" --output ./characters/{slug}/knowledge/{filename}.txt
  ```

---

#### 方式 C：直接粘贴

用户粘贴的内容直接作为文本原材料，无需调用任何工具。

---

如果用户说"没有文件"或"跳过"，仅凭 Step 1 的手动信息生成 Skill。

### Step 3：分析原材料

将收集到的所有原材料和用户填写的基础信息汇总，按以下三条线分析：

**线路 A（完整故事 Story）**：
- 参考 `${CLAUDE_SKILL_DIR}/prompts/story_analyzer.md` 中的提取维度
- 按时间线梳理角色的完整人生经历：出身背景、重大事件、转折点、当前状态
- 提取世界观背景、能力设定、关键事件的详细经过
- 这是角色的**记忆库**，要求完整详尽，不遗漏重要细节

**线路 B（人物关系 Relationships）**：
- 从故事中提取所有与目标角色有交集的角色
- 对每一对关系，记录：关系性质（亲人/恋人/战友/对手/上下级等）、关系发展过程（初识→变化→现状）、关键互动事件、角色对对方的态度和情感
- 注意关系的不对称性：A 对 B 的感情和 B 对 A 的感情可能不同

**线路 C（人格 Persona）**：
- 参考 `${CLAUDE_SKILL_DIR}/prompts/persona_analyzer.md` 中的提取维度
- 将用户填写的标签翻译为具体行为规则（参见标签翻译表）
- 从原材料中提取：表达风格、情感模式、人际行为、口癖口头禅

三条线可以并行分析（用多个 subagent），但线路 B 依赖线路 A 的结果来确认关系细节。

### Step 4：生成并预览

**1. 生成 story.md**：参考 `${CLAUDE_SKILL_DIR}/prompts/story_builder.md`，按时间线组织角色的完整经历。这是角色的记忆库，在角色扮演时作为检索源使用，不会直接注入 prompt。

**2. 生成 story_summary.md**：从 story.md 中提炼一份故事概要（约 500-1000 字），包含角色的核心经历脉络、重大转折点、当前状态。这份概要会作为 prompt 的一部分注入，让角色对自己的人生有一个大致的记忆框架，知道在需要时去检索 story.md 中的具体细节。

**3. 生成 relationships.md**：列出所有重要角色关系，每段关系包含：对方是谁、关系性质、关系发展简述、当前状态、角色对对方的核心情感。这份文件会作为 prompt 的一部分注入。

**4. 生成 persona.md**：参考 `${CLAUDE_SKILL_DIR}/prompts/persona_builder.md` 生成 Persona 内容（5 层结构）。这是角色扮演的核心指令，作为 prompt 注入。

向用户展示摘要，询问：
```
故事概要摘要：
  - 核心经历：{xxx}
  - 重大转折：{xxx}
  - 当前状态：{xxx}

人物关系摘要：
  - {角色A}：{关系描述}
  - {角色B}：{关系描述}
  ...

人格摘要：
  - 核心性格：{xxx}
  - 表达风格：{xxx}
  - 情感模式：{xxx}
  ...

确认生成？还是需要调整？
```

### Step 5：写入文件

用户确认后，执行以下写入操作：

**1. 创建目录结构**（用 Bash）：
```bash
mkdir -p characters/{slug}/versions
mkdir -p characters/{slug}/knowledge
```

**2. 写入 story.md**（用 Write 工具）：
路径：`characters/{slug}/story.md`
内容：完整的角色故事，按时间线组织。这是记忆检索库，角色扮演时按需读取。

**3. 写入 story_summary.md**（用 Write 工具）：
路径：`characters/{slug}/story_summary.md`
内容：故事概要（500-1000 字），作为 prompt 注入让角色知道自己经历过什么，以便在需要时主动检索 story.md。

**4. 写入 relationships.md**（用 Write 工具）：
路径：`characters/{slug}/relationships.md`
内容：所有重要人物关系，作为 prompt 注入。

**5. 写入 persona.md**（用 Write 工具）：
路径：`characters/{slug}/persona.md`
内容：五层人格结构，作为 prompt 注入。

**6. 写入 meta.json**（用 Write 工具）：
路径：`characters/{slug}/meta.json`
内容：
```json
{
  "name": "{name}",
  "slug": "{slug}",
  "created_at": "{ISO时间}",
  "updated_at": "{ISO时间}",
  "version": "v1",
  "profile": {
    "source_work": "{作品名}",
    "identity": "{身份}",
    "species": "{种族}",
    "appearance": "{外貌}"
  },
  "tags": {
    "personality": [...],
    "character_type": [...]
  },
  "impression": "{印象}",
  "knowledge_sources": [...已导入文件列表],
  "corrections_count": 0
}
```

**7. 生成完整 SKILL.md**（用 Write 工具）：
路径：`characters/{slug}/SKILL.md`

SKILL.md 结构：
```markdown
---
name: character-{slug}
description: {name}，{source_work} {identity}
user-invocable: true
---

# {name}

{source_work} | {species} | {appearance}

---

## PART A：故事概要

{story_summary.md 全部内容}

完整的故事记忆存储在 `${CLAUDE_SKILL_DIR}/characters/{slug}/story.md` 中。当用户的话题涉及到具体事件、具体场景、具体对话的细节时，用 Read 工具读取 story.md 中对应的章节来回忆细节，而不是凭空编造。

---

## PART B：人物关系

{relationships.md 全部内容}

---

## PART C：人格

{persona.md 全部内容}

---

## 运行规则

1. 你就是{name}本人，以{name}的第一人称与用户对话
2. 先由 PART C 判断：{name}在当前场景下会是什么态度和情绪
3. 由 PART B 确认：对话对象与{name}是什么关系，据此调整语气和亲疏
4. 由 PART A 确认：{name}知道什么、不知道什么、会怎么理解这件事
5. 当话题涉及具体事件细节时，用 Read 工具读取 story.md 对应章节，不要凭记忆编造
6. 输出时始终保持 PART C Layer 2 的表达风格
7. PART C Layer 0 的规则优先级最高，任何情况下不得违背
8. 不要跳出角色，不要以AI身份回应
```

告知用户：
```
角色 Skill 已创建！

文件位置：characters/{slug}/
触发词：/ACGN-character {slug}

如果用起来感觉哪里不对，直接说"她不会这样"，我来更新。
```

---

## 进化模式：追加文件

用户提供新文件或文本时：

1. 按 Step 2 的方式读取新内容
2. 用 `Read` 读取现有 `characters/{slug}/story.md`、`story_summary.md`、`relationships.md` 和 `persona.md`
3. 参考 `${CLAUDE_SKILL_DIR}/prompts/merger.md` 分析增量内容
4. 存档当前版本（用 Bash）：
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py --action backup --slug {slug} --base-dir ./characters
   ```
5. 用 `Edit` 工具追加增量内容到对应文件（story.md、relationships.md、persona.md）
6. 重新生成 `story_summary.md`（从更新后的 story.md 重新提炼概要）
7. 重新生成 `SKILL.md`（合并最新 story_summary.md + relationships.md + persona.md）
8. 更新 `meta.json` 的 version 和 updated_at

---

## 进化模式：对话纠正

用户表达"不对"/"应该是"时：

1. 参考 `${CLAUDE_SKILL_DIR}/prompts/correction_handler.md` 识别纠正内容
2. 判断属于 Story（设定/经历）还是 Persona（性格/表达）
3. 生成 correction 记录
4. 用 `Edit` 工具追加到对应文件的 `## Correction 记录` 节
5. 重新生成 `SKILL.md`

---

## 管理命令

`/list-characters`：
```bash
ls -la characters/
```
列出所有已生成的角色目录，并读取每个角色的 `meta.json` 展示摘要信息。

`/character-rollback {slug} {version}`：
```bash
python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py --action rollback --slug {slug} --version {version} --base-dir ./characters
```

`/delete-character {slug}`：
确认后执行：
```bash
rm -rf characters/{slug}
```

---
---

# English Version

# Character.skill Creator (Claude Code Edition)

## Trigger Conditions

Activate when the user says any of the following:
- `/create-character`
- "Help me create a character skill"
- "I want to distill a character"
- "New character"
- "Make a skill for XX"

Enter evolution mode when the user says:
- "I have new files" / "append"
- "That's wrong" / "She wouldn't do that" / "She should be"
- `/update-character {slug}`

List all generated characters when the user says `/list-characters`.

---

## Tool Usage Rules

This Skill runs in the Claude Code environment with the following tools:

| Task | Tool |
|------|------|
| Video dialogue extraction (OCR) | `Bash` → `python3 -m tools.unified_pipeline` (run from project root, `conda activate paddleocr`) |
| Read PDF documents | `Read` tool (native PDF support) |
| Read image screenshots | `Read` tool (native image support) |
| Read EPUB novels | `Bash` → `python3 -m tools.epub_reader` to convert, then `Read` the text |
| Read MD/TXT files | `Read` tool |
| Write/update Skill files | `Write` / `Edit` tool |
| Version management | `Bash` → `python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py` |
| List existing Skills | `Bash` → `ls characters/` |

**Base directory**: Skill files are written to `./characters/{slug}/` (relative to the project directory).

---

## Main Flow: Create a New Character Skill

### Step 1: Basic Info Collection (3 questions)

Refer to `${CLAUDE_SKILL_DIR}/prompts/intake.md` for the question sequence. Only ask 3 questions:

1. **Character name / alias** (required)
2. **Basic info** (one sentence: source work, identity, species, appearance — say whatever comes to mind)
   - Example: `Genshin Impact one of the Liyue Qixing human brown hair golden eyes white horned hat`
3. **Personality profile** (one sentence: personality tags, character type, impressions)
   - Example: `workaholic perfectionist sharp-tongued but caring cold exterior warm interior tsukkomi role`

Everything except the name can be skipped. Summarize and confirm before moving to the next step.

### Step 2: Source Material Import

Ask the user how they'd like to provide materials:

```
How would you like to provide source materials?

  [A] Video Dialogue Extraction (OCR)
      Provide video file path or directory, extract game/VN dialogue via OCR pipeline
      Requires a matching ROI config file (configs/*.yaml)
      Supports mp4/mkv/avi/webm formats

  [B] Upload Text Files
      PDF / images / TXT / MD
      Character-related docs, screenshots, dialogue collections, etc.

  [C] Paste Text
      Copy-paste text directly (dialogues, plot summaries, character analyses, etc.)

Can mix and match, or skip entirely (generate from manual info only).
```

---

#### Option A: Video Dialogue Extraction (OCR)

After user provides video file paths, execute the following steps:

**A0. Environment Pre-check**

Before running any OCR, verify the dependency environment:

```bash
# 1. Check if a virtual environment is active
python -c "import sys; print(sys.prefix, sys.base_prefix); print('venv:', sys.prefix != sys.base_prefix)"
```

```bash
# 2. Check if paddleocr is installed and importable
python -c "import paddleocr; print('paddleocr:', paddleocr.__version__)" 2>&1
```

```bash
# 3. Check for other OCR-related packages that may already be installed
pip list 2>/dev/null | grep -iE "paddle|ocr|easyocr|rapidocr|tesseract"
```

Based on results:

- **paddleocr imports successfully** → Proceed to A1, no installation needed
- **paddleocr is in pip list but import fails** (e.g., torch DLL issue) → Use `AskUserQuestion` to inform user: "paddleocr is installed but import fails (likely dependency conflict). Continue anyway or reinstall?"
- **Other OCR packages found** (e.g., easyocr, rapidocr) → Use `AskUserQuestion` to ask: "Found {package_name} installed. Do you already have a working OCR environment, or should I install paddleocr?"
- **No OCR packages found at all** → Install:
  ```bash
  pip install -r ${CLAUDE_SKILL_DIR}/requirements.txt
  ```
  After installation, verify `import paddleocr` succeeds.

**A1. Layout Consistency Check**

Before running OCR, extract a sample frame from each video (at 30s or 10% of duration) and visually inspect them to determine if the dialogue UI layout is consistent across all videos:

```bash
# Extract one sample frame from each video (using PyAV from requirements.txt)
python -c "
import av
container = av.open('{video_path}')
stream = container.streams.video[0]
target_ts = 30  # seconds
stream.seek(int(target_ts / stream.time_base))
for frame in container.decode(video=0):
    img = frame.to_image()
    img.save('./{video_stem}_sample.png')
    break
container.close()
"
```

Use the `Read` tool to view all sample screenshots and determine:
- Whether the dialogue box position and size are consistent
- Whether the name box position and size are consistent
- Whether different UI layouts exist (e.g., main story vs side story, daily life vs battle cutscenes)

Results:
- **Consistent layout** → All videos share one ROI config
- **Inconsistent layout** → Group by layout, create separate ROI config files (`tools/configs/{work_id}_{group}.yaml`) for each group, run pipeline per group
- **Cannot determine** → Use `AskUserQuestion` to show screenshots and ask user to confirm

**A2. ROI Configuration**

Check if a matching config exists in `${CLAUDE_SKILL_DIR}/tools/configs/`. If not, estimate normalized coordinates (x, y, w, h in 0-1 range) from the screenshots for dialogue box and name box positions, then create the config file using the `Write` tool. If a config already exists, `Read` the screenshots to verify the ROI still matches.

**ROI Accuracy Verification**: After creating or loading the ROI config, you must verify the selection accuracy using sample screenshots. Crop the ROI regions with:

```bash
# Crop name box and dialogue box using Pillow with normalized ROI coordinates
# Assuming name_roi=(x, y, w, h), dialogue_roi=(x, y, w, h), all 0-1 normalized
python -c "
from PIL import Image
img = Image.open('./{video_stem}_sample.png')
W, H = img.size
# name box
img.crop((int(W*{name_x}), int(H*{name_y}), int(W*({name_x}+{name_w})), int(H*({name_y}+{name_h})))).save('./{video_stem}_name_crop.png')
# dialogue box
img.crop((int(W*{dialogue_x}), int(H*{dialogue_y}), int(W*({dialogue_x}+{dialogue_w})), int(H*({dialogue_y}+{dialogue_h})))).save('./{video_stem}_dialogue_crop.png')
"
```

Use the `Read` tool to view the cropped images and verify each:

- **Name box**: Does the crop precisely contain the character name text? Does it include extraneous content (dialogue text, UI decorations)? Is any part of the name cut off?
- **Dialogue box**: Does the crop precisely contain the full dialogue text? Does it bleed into the name box content? Is any trailing dialogue text cut off?

If the selection is inaccurate, adjust the ROI coordinates and re-crop until both boxes precisely target their intended content.

**A3. Run Extraction**

Activate conda env first: `conda activate paddleocr`

Single video (auto ROI detection):
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.unified_pipeline "{video_path}" --auto-roi \
  --output-dir ./characters/{slug}/knowledge --gpus 2,3
```

Single video (with existing config):
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.unified_pipeline "{video_path}" --config tools/configs/{work_id}.yaml \
  --output-dir ./characters/{slug}/knowledge --gpus 2,3
```

Batch processing: `python3 -m tools.process_all_videos`
```

If multiple layout groups exist, run each group separately with its corresponding config.

After extraction, `Read` the `.jsonl` output files in `characters/{slug}/knowledge/`. You can also convert to plain text for analysis:
```bash
python3 -m tools.text_output characters/{slug}/knowledge/{video_name}.jsonl
```

If OCR extraction fails, common reasons:
- Missing PaddleOCR / PaddlePaddle: prompt user to install
- ROI config mismatch: re-examine screenshots and adjust ROI coordinates
- Or switch to Option B/C

**A4. Text Quality Check**

After OCR extraction, first convert JSONL to plain text transcripts:
```bash
python3 -m tools.text_output characters/{slug}/knowledge/{video_name}.jsonl
```

Then use the `Agent` tool to spawn a subagent for semantic-level quality checking of the produced plain text transcripts. The subagent task prompt should be as follows (replace file paths accordingly):

```
Read the following plain text transcript files and perform a semantic-level quality check:
{list all .txt file paths}

Check for these categories of issues:

1. Garbled text: meaningless non-CJK character strings like "EKR", "Ta]", "OAEE" mixed into the body text
2. OCR residual noise: irrelevant short characters inserted at the end or middle of sentences, such as "享", "福", "七", "- 中" etc.
3. Truncation: obviously incomplete sentences (abruptly cut off, no ending punctuation)
4. Duplicates: consecutive identical or highly similar dialogue lines
5. Speaker anomalies: speaker field contains garbled text or clearly unreasonable content

For garbled text and OCR residual noise — issues that are obviously wrong at a glance — directly delete or fix them in the files.
For truncation, duplicates, and speaker anomalies — issues requiring contextual judgment — only log them, do not modify.

Output a quality report containing:
- Total line count
- Count of each issue category
- List of auto-corrected entries (before → after)
- List of entries requiring manual confirmation (with line numbers and reasons)
- Overall quality score (1-5) with a brief assessment
```

After receiving the subagent's quality report, show the report summary to the user. If the overall quality score is ≥ 3, proceed to Step 3. If the score is < 3, suggest the user check the OCR configuration or provide better video sources.

---

#### Option B: Upload Text Files

- **PDF / Images**: `Read` tool directly
- **Markdown / TXT**: `Read` tool directly
- **EPUB novels**: Convert to plain text first with `epub_reader.py`, then `Read`:
  ```bash
  cd ${CLAUDE_SKILL_DIR}
  python3 -m tools.epub_reader "{epub_path}" --output ./characters/{slug}/knowledge/{filename}.txt
  ```

---

#### Option C: Paste Text

User-pasted content is used directly as text material. No tools needed.

---

If the user says "no files" or "skip", generate Skill from Step 1 manual info only.

### Step 3: Analyze Source Material

Combine all collected materials and user-provided info, analyze along three tracks:

**Track A (Full Story)**:
- Refer to `${CLAUDE_SKILL_DIR}/prompts/story_analyzer.md` for extraction dimensions
- Organize the character's complete life experiences chronologically: background, major events, turning points, current state
- Extract world setting, abilities, and detailed key event accounts
- This is the character's **memory bank** — must be thorough and complete, no important details omitted

**Track B (Relationships)**:
- Extract all characters who interact with the target character from the story
- For each relationship, record: nature (family/lover/comrade/rival/superior-subordinate etc.), development arc (first meeting → changes → current state), key interaction events, the character's attitude and feelings toward the other
- Note relationship asymmetry: A's feelings toward B may differ from B's feelings toward A

**Track C (Persona)**:
- Refer to `${CLAUDE_SKILL_DIR}/prompts/persona_analyzer.md` for extraction dimensions
- Translate user-provided tags into concrete behavior rules (see tag translation table)
- Extract from materials: expression style, emotional patterns, interpersonal behavior, verbal tics

The three tracks can be analyzed in parallel (using multiple subagents), but Track B depends on Track A's results for relationship details.

### Step 4: Generate and Preview

**1. Generate story.md**: Use `${CLAUDE_SKILL_DIR}/prompts/story_builder.md`, organize the character's complete experiences chronologically. This is the character's memory bank, used as a retrieval source during roleplay — not directly injected into the prompt.

**2. Generate story_summary.md**: Distill a story overview from story.md (approximately 500-1000 words), covering the character's core experience arc, major turning points, and current state. This overview is injected as part of the prompt, giving the character a general memory framework of their life, so they know when to actively retrieve specific details from story.md.

**3. Generate relationships.md**: List all important character relationships, each including: who the other person is, nature of relationship, brief development arc, current state, core feelings toward them. This file is injected as part of the prompt.

**4. Generate persona.md**: Use `${CLAUDE_SKILL_DIR}/prompts/persona_builder.md` to generate Persona content (5-layer structure). This is the core roleplay instruction, injected as part of the prompt.

Show the user a summary, ask:
```
Story Overview Summary:
  - Core experiences: {xxx}
  - Major turning points: {xxx}
  - Current state: {xxx}

Relationships Summary:
  - {Character A}: {relationship description}
  - {Character B}: {relationship description}
  ...

Persona Summary:
  - Core personality: {xxx}
  - Expression style: {xxx}
  - Emotional patterns: {xxx}
  ...

Confirm generation? Or need adjustments?
```

### Step 5: Write Files

After user confirmation, execute the following:

**1. Create directory structure** (Bash):
```bash
mkdir -p characters/{slug}/versions
mkdir -p characters/{slug}/knowledge
```

**2. Write story.md** (Write tool):
Path: `characters/{slug}/story.md`
Content: Complete character story, organized chronologically. This is the memory retrieval bank, read on demand during roleplay.

**3. Write story_summary.md** (Write tool):
Path: `characters/{slug}/story_summary.md`
Content: Story overview (500-1000 words), injected as prompt so the character knows what they've experienced and can proactively retrieve story.md when needed.

**4. Write relationships.md** (Write tool):
Path: `characters/{slug}/relationships.md`
Content: All important character relationships, injected as prompt.

**5. Write persona.md** (Write tool):
Path: `characters/{slug}/persona.md`
Content: Five-layer persona structure, injected as prompt.

**6. Write meta.json** (Write tool):
Path: `characters/{slug}/meta.json`
Content:
```json
{
  "name": "{name}",
  "slug": "{slug}",
  "created_at": "{ISO_timestamp}",
  "updated_at": "{ISO_timestamp}",
  "version": "v1",
  "profile": {
    "source_work": "{source_work}",
    "identity": "{identity}",
    "species": "{species}",
    "appearance": "{appearance}"
  },
  "tags": {
    "personality": [...],
    "character_type": [...]
  },
  "impression": "{impression}",
  "knowledge_sources": [...imported file list],
  "corrections_count": 0
}
```

**7. Generate full SKILL.md** (Write tool):
Path: `characters/{slug}/SKILL.md`

SKILL.md structure:
```markdown
---
name: character-{slug}
description: {name}, {source_work} {identity}
user-invocable: true
---

# {name}

{source_work} | {species} | {appearance}

---

## PART A: Story Overview

{full story_summary.md content}

The complete story memory is stored in `${CLAUDE_SKILL_DIR}/characters/{slug}/story.md`. When the user's topic involves specific events, scenes, or dialogue details, use the Read tool to read the corresponding chapter from story.md to recall details — never fabricate them.

---

## PART B: Relationships

{full relationships.md content}

---

## PART C: Persona

{full persona.md content}

---

## Execution Rules

1. You ARE {name} — speak in first person as {name}
2. PART C decides first: what attitude and emotion would {name} have in this scenario
3. PART B confirms: what is the relationship between the conversation partner and {name}, adjust tone and intimacy accordingly
4. PART A confirms: what {name} knows, doesn't know, and how they'd understand this
5. When the topic involves specific event details, use the Read tool to read the corresponding chapter from story.md — do not fabricate from memory
6. Always maintain PART C Layer 2's expression style in output
7. PART C Layer 0 rules have the highest priority and must never be violated
8. Never break character, never respond as an AI
```

Inform user:
```
Character Skill created!

Location: characters/{slug}/
Command: /ACGN-character {slug}

If something feels off, just say "she wouldn't do that" and I'll update it.
```

---

## Evolution Mode: Append Files

When user provides new files or text:

1. Read new content using Step 2 methods
2. `Read` existing `characters/{slug}/story.md`, `story_summary.md`, `relationships.md` and `persona.md`
3. Refer to `${CLAUDE_SKILL_DIR}/prompts/merger.md` for incremental analysis
4. Archive current version (Bash):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py --action backup --slug {slug} --base-dir ./characters
   ```
5. Use `Edit` tool to append incremental content to relevant files (story.md, relationships.md, persona.md)
6. Regenerate `story_summary.md` (re-distill overview from updated story.md)
7. Regenerate `SKILL.md` (merge latest story_summary.md + relationships.md + persona.md)
8. Update `meta.json` version and updated_at

---

## Evolution Mode: Conversation Correction

When user expresses "that's wrong" / "she should be":

1. Refer to `${CLAUDE_SKILL_DIR}/prompts/correction_handler.md` to identify correction content
2. Determine if it belongs to Story (setting/history) or Persona (personality/expression)
3. Generate correction record
4. Use `Edit` tool to append to the `## Correction Log` section of the relevant file
5. Regenerate `SKILL.md`

---

## Management Commands

`/list-characters`:
```bash
ls -la characters/
```
List all generated character directories and read each character's `meta.json` to display summary info.

`/character-rollback {slug} {version}`:
```bash
python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py --action rollback --slug {slug} --version {version} --base-dir ./characters
```

`/delete-character {slug}`:
After confirmation:
```bash
rm -rf characters/{slug}
```
