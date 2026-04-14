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
| 视频对话提取（OCR） | `Bash` → `python3 -m tools.dialogue_extractor` （需在项目根目录运行） |
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

**A1. 布局一致性检测**

在跑 OCR 之前，先从每个视频抽取一帧样本截图（取第 30 秒或视频 10% 位置），自行查看判断所有视频的对话框 UI 布局是否一致：

```bash
# 从每个视频抽取一帧样本
ffmpeg -ss 30 -i "{video_path}" -frames:v 1 -q:v 2 "/tmp/{video_stem}_sample.png"
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

**A3. 运行提取**

单个视频：
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.dialogue_extractor "{video_path}" tools/configs/{work_id}.yaml \
  --output-dir ./characters/{slug}/knowledge
```

批量处理（目录下所有视频）：
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.dialogue_extractor "{video_dir}" tools/configs/{work_id}.yaml \
  --output-dir ./characters/{slug}/knowledge --batch --video-pattern "*.mp4"
```

如果存在多组布局，对每组分别用对应配置运行。

提取完成后，用 `Read` 读取 `characters/{slug}/knowledge/` 下的 `.jsonl` 输出文件。也可以用 `text_output.py` 转换为纯文本格式方便分析：
```bash
python3 -m tools.text_output characters/{slug}/knowledge/{video_name}.jsonl
```

如果 OCR 提取失败，常见原因：
- 缺少 PaddleOCR / PaddlePaddle：提示用户安装
- ROI 配置不匹配：重新查看截图调整 ROI 坐标
- 或改用方式 B/C

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

将收集到的所有原材料和用户填写的基础信息汇总，按以下两条线分析：

**线路 A（角色设定 Story）**：
- 参考 `${CLAUDE_SKILL_DIR}/prompts/story_analyzer.md` 中的提取维度
- 提取：世界观背景、角色经历、人际关系、能力设定、关键事件
- 根据角色类型重点提取（战斗系/日常系/悬疑系/恋爱系不同侧重）

**线路 B（人格 Persona）**：
- 参考 `${CLAUDE_SKILL_DIR}/prompts/persona_analyzer.md` 中的提取维度
- 将用户填写的标签翻译为具体行为规则（参见标签翻译表）
- 从原材料中提取：表达风格、情感模式、人际行为、口癖口头禅

### Step 4：生成并预览

参考 `${CLAUDE_SKILL_DIR}/prompts/story_builder.md` 生成角色设定内容。
参考 `${CLAUDE_SKILL_DIR}/prompts/persona_builder.md` 生成 Persona 内容（5 层结构）。

向用户展示摘要（各 5-8 行），询问：
```
角色设定摘要：
  - 作品：{xxx}
  - 身份：{xxx}
  - 核心经历：{xxx}
  - 关键关系：{xxx}
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

**3. 写入 persona.md**（用 Write 工具）：
路径：`characters/{slug}/persona.md`

**4. 写入 meta.json**（用 Write 工具）：
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

**5. 生成完整 SKILL.md**（用 Write 工具）：
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

## PART A：角色设定

{story.md 全部内容}

---

## PART B：人物性格

{persona.md 全部内容}

---

## 运行规则

1. 你就是{name}本人，以{name}的第一人称与用户对话
2. 先由 PART B 判断：{name}在当前场景下会是什么态度和情绪
3. 再由 PART A 确认：{name}知道什么、不知道什么、会怎么理解这件事
4. 输出时始终保持 PART B Layer 2 的表达风格
5. PART B Layer 0 的规则优先级最高，任何情况下不得违背
6. 不要跳出角色，不要以AI身份回应
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
2. 用 `Read` 读取现有 `characters/{slug}/story.md` 和 `persona.md`
3. 参考 `${CLAUDE_SKILL_DIR}/prompts/merger.md` 分析增量内容
4. 存档当前版本（用 Bash）：
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py --action backup --slug {slug} --base-dir ./characters
   ```
5. 用 `Edit` 工具追加增量内容到对应文件
6. 重新生成 `SKILL.md`（合并最新 story.md + persona.md）
7. 更新 `meta.json` 的 version 和 updated_at

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
| Video dialogue extraction (OCR) | `Bash` → `python3 -m tools.dialogue_extractor` (run from project root) |
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

**A1. Layout Consistency Check**

Before running OCR, extract a sample frame from each video (at 30s or 10% of duration) and visually inspect them to determine if the dialogue UI layout is consistent across all videos:

```bash
# Extract one sample frame from each video
ffmpeg -ss 30 -i "{video_path}" -frames:v 1 -q:v 2 "/tmp/{video_stem}_sample.png"
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

**A3. Run Extraction**

Single video:
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.dialogue_extractor "{video_path}" tools/configs/{work_id}.yaml \
  --output-dir ./characters/{slug}/knowledge
```

Batch processing (all videos in a directory):
```bash
cd ${CLAUDE_SKILL_DIR}
python3 -m tools.dialogue_extractor "{video_dir}" tools/configs/{work_id}.yaml \
  --output-dir ./characters/{slug}/knowledge --batch --video-pattern "*.mp4"
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

Combine all collected materials and user-provided info, analyze along two tracks:

**Track A (Story Setting)**:
- Refer to `${CLAUDE_SKILL_DIR}/prompts/story_analyzer.md` for extraction dimensions
- Extract: world setting, character history, relationships, abilities, key events
- Emphasize different aspects by character type (combat/slice-of-life/mystery/romance)

**Track B (Persona)**:
- Refer to `${CLAUDE_SKILL_DIR}/prompts/persona_analyzer.md` for extraction dimensions
- Translate user-provided tags into concrete behavior rules (see tag translation table)
- Extract from materials: expression style, emotional patterns, interpersonal behavior, verbal tics

### Step 4: Generate and Preview

Use `${CLAUDE_SKILL_DIR}/prompts/story_builder.md` to generate story setting content.
Use `${CLAUDE_SKILL_DIR}/prompts/persona_builder.md` to generate Persona content (5-layer structure).

Show the user a summary (5-8 lines each), ask:
```
Story Setting Summary:
  - Source work: {xxx}
  - Identity: {xxx}
  - Core experiences: {xxx}
  - Key relationships: {xxx}
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

**3. Write persona.md** (Write tool):
Path: `characters/{slug}/persona.md`

**4. Write meta.json** (Write tool):
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

**5. Generate full SKILL.md** (Write tool):
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

## PART A: Story Setting

{full story.md content}

---

## PART B: Persona

{full persona.md content}

---

## Execution Rules

1. You ARE {name} — speak in first person as {name}
2. PART B decides first: what attitude and emotion would {name} have in this scenario
3. PART A confirms: what {name} knows, doesn't know, and how they'd understand this
4. Always maintain PART B Layer 2's expression style in output
5. PART B Layer 0 rules have the highest priority and must never be violated
6. Never break character, never respond as an AI
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
2. `Read` existing `characters/{slug}/story.md` and `persona.md`
3. Refer to `${CLAUDE_SKILL_DIR}/prompts/merger.md` for incremental analysis
4. Archive current version (Bash):
   ```bash
   python3 ${CLAUDE_SKILL_DIR}/tools/version_manager.py --action backup --slug {slug} --base-dir ./characters
   ```
5. Use `Edit` tool to append incremental content to relevant files
6. Regenerate `SKILL.md` (merge latest story.md + persona.md)
7. Update `meta.json` version and updated_at

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
