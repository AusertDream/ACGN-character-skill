<div align="center">

# ACGN-character-skill

> *"吸血鬼不信神，也不信命运，但像这样出现在我面前的你，一定是我遇到过的最大的奇迹。"*

[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)](https://claude.ai/code)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![PaddleOCR](https://img.shields.io/badge/PaddleOCR-OCR%20Pipeline-orange)](https://github.com/PaddlePaddle/PaddleOCR)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

![立绘图](imgs/立绘图.png)

</div>

## 简介

ACGN-character-skill 是一个 Claude Code Skill，用于将 ACGN（Anime / Comic / Game / Novel）领域的虚构角色"蒸馏"为可对话的角色扮演 prompt。它从原作素材中提取角色的故事经历、人物关系和人格特征，生成一套结构化的角色扮演指令，让 Claude 能够以该角色的身份、语气和思维方式进行对话。

项目覆盖 ACGN 四种媒体类型，针对不同的输入数据有不同的处理方式：

**Game / Visual Novel（游戏 / 视觉小说）** — 输入通常是剧情录屏视频。通过内置的 OCR 对话提取管线处理：手工标注对话框和名字框的 ROI 坐标，然后用自适应状态机逐帧检测对话事件，PaddleOCR 批量识别文字，后置合并处理打字机效果碎片，最终输出带说话人标注的纯文本台本。这是目前最成熟的管线，截断率 <0.2%。

**Novel（小说 / 轻小说）** — 输入通常是 EPUB 电子书文件，也可以是 PDF 或纯文本。EPUB 文件通过内置的 `epub_reader.py` 解压并提取纯文本内容，PDF 和 TXT/MD 文件直接读取。提取出的文本作为原材料进入角色分析流程。

**Anime（动画）** — 输入是动画视频文件。将视频按固定间隔切分为帧图片，然后召唤 subagent 调用视觉理解模型（VLM）逐批识别每帧中的字幕文字、说话人和场景描写。

**Comic（漫画）** — 输入是漫画图片文件。将图片按阅读顺序排列，召唤 subagent 调用 VLM 逐页提取对话框文字、说话人和场景描写。

> **⚠️ Anime / Comic 方式警告**：动画和漫画的 VLM 提取方案需要将大量图片帧逐批送入视觉理解模型，**token 消耗极大**（一部 24 分钟动画约产生 300 帧截图，每帧消耗数千 token），且**实际提取效果尚未经过充分验证**。目前仅作为没有其他数据来源（字幕文件、wiki、剧情文档）时的最终保底方案。建议优先寻找文字形式的原材料。

无论哪种输入方式，提取出的文本都会经过统一的角色分析流程：从文本中梳理角色的完整故事时间线（story.md，作为检索记忆库）、提炼故事概要（story_summary.md，注入 prompt）、提取人物关系（relationships.md，注入 prompt）、分析五层人格特征（persona.md，注入 prompt），最终组装为一个完整的角色扮演 SKILL.md。

---

## 安装与使用

```bash
# 安装角色蒸馏工具
npx skills add AusertDream/ACGN-character-skill
```

安装完成后，在 Claude Code 中：

```
/ACGN-character          # 创建新角色或与已有角色对话
/ACGN-character yuexia   # 直接进入月下角色扮演模式
```

---

## 当前能力

### 视频对话提取管线（Game / Visual Novel）

统一端到端管线：`Video → FrameExtractor → BatchOCR → PostMerge → SpeakerDenoiser → TextCorrector → JSONL + TXT`

核心技术包括：自适应状态机（增长率追踪 + MAD skip + Levenshtein 停时判据）正确处理打字机效果；手工 ROI 标注网页工具；checkpoint/resume 断点续传；说话人识别（空白→旁白，???→未知角色）和 OCR 噪声去噪；正则 + 可选 LLM 双层纠错。

运行方式（需先 `conda activate paddleocr`，GPU 通过 `CUDA_VISIBLE_DEVICES` 指定）：

```bash
# 单个视频
CUDA_VISIBLE_DEVICES=0 python -m tools.unified_pipeline "video.mp4" --config tools/configs/config.yaml

# 批量处理
CUDA_VISIBLE_DEVICES=0 python -m tools.process_all_videos

# 手工 ROI 标注
python -m tools.roi_annotator --port 11451
```

### 小说文本提取（Novel）

```bash
# EPUB 提取纯文本
python -m tools.epub_reader "novel.epub" --output "output.txt"
```

PDF 和 TXT/MD 文件由 Claude 的 Read 工具直接读取，无需额外处理。

### 角色分析与生成

从提取的文本中按三条线并行分析：故事时间线（Story）、人物关系（Relationships）、五层人格（Persona），生成结构化的角色扮演 prompt。支持增量更新（追加新材料后 merge）和对话纠正（角色扮演中说"她不会这样"即可触发修正）。

### 生成的角色扮演 prompt 结构

| 部分 | 内容 | 加载方式 |
|:---|:---|:---|
| **Part A — 故事概要** | 角色的核心经历脉络（500-1000字） | prompt 注入 |
| **Part B — 人物关系** | 与所有重要角色的关系描述 | prompt 注入 |
| **Part C — 人格** | Layer 0-5 六层行为指令 | prompt 注入 |
| **完整故事** | 详细的时间线记忆库 | 按需用 Read 工具检索 |

---

## 实际效果

### OCR 管线验证结果（崩坏3舰长线，7 视频，9,000 事件）

| 指标 | 值 | 目标 | 状态 |
|:---|:---|:---|:---|
| 截断率（人工评估） | <0.2% | <5% | PASS |
| 文本可读性 | 3.5-4/5 | 通顺可读 | PASS |
| 说话人识别率 | 73-93% | 可用 | PASS |
| OCR 后缀噪声 | 已清理 | — | PASS |

### 角色扮演效果示例（月下）

> **日常对话**

```
用户    ❯ 今晚的月亮好圆啊

月下    ❯ 嗯……今晚的月色也很美呢，和我们第一次见面时一模一样。
         那时候我遇到的是你，真是太好了。
         ……人类，你在笑什么？
```

> **被关心时**

```
用户    ❯ 你是不是又在忍着不舒服？

月下    ❯ ……没什么。不要用那种眼神看着我。
         这种程度的疼痛，我已经习惯了。真的没事。
         ……我说谎了。但我不想让你担心。
```

> **回忆过去**

```
用户    ❯ 你还记得以前的事吗

月下    ❯ 我曾经死过一次。不，不止一次。
         我鲜明地记得每一次伤痛，每一段死亡，每一份绝望……
         以及那个每一次都目光坚定地出现在我眼前、
         想尽一切办法想要救我的人类。
         ……你问我为什么还能笑着说这些？
         因为最后遇到的是你啊。
```

---

## 项目结构

```
ACGN-character-skill/
├── SKILL.md                    # 角色蒸馏工具入口
├── prompts/                    # Prompt 模板（story/persona analyzer & builder）
├── tools/
│   ├── unified_pipeline.py     #   Game OCR 统一管线入口
│   ├── frame_extractor.py      #   Stage 1 事件检测 + 帧保存
│   ├── batch_ocr.py            #   Stage 2 批量 OCR
│   ├── event_detector.py       #   自适应状态机
│   ├── roi_annotator.py        #   网页版手工 ROI 标注
│   ├── epub_reader.py          #   EPUB 纯文本提取
│   ├── post_merge.py           #   后置前缀合并 + 战斗文字过滤
│   ├── speaker_denoiser.py     #   说话人 OCR 噪声清洗
│   ├── speaker_extractor.py    #   说话人识别（空白→旁白，???→未知）
│   ├── llm_corrector.py        #   LLM OCR 纠错（可选）
│   ├── ocr_engines.py          #   OCR 引擎工厂
│   ├── preprocessing.py        #   图像预处理 profile
│   ├── work_config.py          #   配置系统
│   └── configs/                #   每部作品的 ROI 配置文件
├── characters/
│   └── yuexia/                 #   月下角色扮演产物
│       ├── SKILL.md            #     组装后的角色扮演 prompt
│       ├── story.md            #     完整故事（检索记忆库）
│       ├── story_summary.md    #     故事概要（prompt 注入）
│       ├── relationships.md    #     人物关系（prompt 注入）
│       └── persona.md          #     五层人格（prompt 注入）
└── data/                       #   原材料文本（gitignored）
```

### 架构说明

角色蒸馏分为两个阶段。第一阶段是数据提取：根据输入媒体类型（Game 视频 / Novel 文本 / Anime 视频 / Comic 图片）用对应的管线将原作素材转换为纯文本台本。第二阶段是角色分析：从文本中按三条线并行分析——故事时间线梳理角色完整经历并形成记忆库、人物关系提取角色与所有相关角色的关系网络、人格分析提取角色的表达风格和行为模式。分析结果分别生成为独立文件，最终组装成一个角色扮演 SKILL.md。

角色扮演时的加载策略是"prompt 注入 + 按需检索"：故事概要、人物关系和人格作为 prompt 常驻注入，让角色始终知道自己是谁、经历过什么、和谁有关系、如何说话；完整故事作为记忆库存储，当话题涉及具体事件细节时用 Read 工具检索对应章节，避免凭空编造。

---

## 数据来源

ACGN 四种媒体类型对应不同的典型输入数据：

| 类型 | 典型输入 | 推荐替代 |
|:---|:---|:---|
| **Anime（动画）** | 动画视频文件 | 字幕文件（.srt/.ass）、wiki、剧情文档 |
| **Comic（漫画）** | 漫画图片 | 小说原作、wiki、剧情文档 |
| **Game（游戏）** | 剧情录屏视频 | 游戏脚本、剧情文档 |
| **Novel（小说）** | EPUB / PDF / TXT 文件 | 直接使用，无需替代 |

所有类型都支持直接粘贴纯文本作为输入。如果能找到文字形式的原材料（剧情文档、wiki、字幕文件、小说原作），应优先使用，效果最好且成本最低。视频和图片的自动提取是在没有文字来源时的备选方案。

### 示例：月下（崩坏3rd）

本项目的首个实例是崩坏3舰长线角色「月下」。她的数据来源是 Game 类型——B站UP主 [MC神神希](https://space.bilibili.com/666904408) 发布的崩坏3舰长线全剧情合集视频，共 7 个视频，通过 OCR 管线提取了约 9,000 条对话事件。

| 章节 | 标题 | 内容 |
|:---|:---|:---|
| 第一节 | 仲夏幻夜 | 月下与舰长的初遇 |
| 第十七节 | 在长梦弥散之前 | 寻找月下的线索 |
| 第十八节（3部分） | 当红月落幕之后 | 城堡对峙与告别 |
| 第十八节支线 | 月下全回忆和彩蛋 | 月下的核心独白与记忆 |
| 第十九节 | 牧场奇谭 | 日常生活与归宿 |

Live2D 来源：B站 [支线路人A](https://space.bilibili.com/1152374880)

---

## 开发历程

项目从 2026 年 4 月 7 日开始，到 5 月初基本完成 OCR 管线，整体开发周期约一个月。以下是按照实际 git 提交时间线记录的技术演进过程。

### Phase 1: 基础设施搭建（04-07 ~ 04-09）

项目第一天（04-07）初始化仓库，第二天（04-08）就开始搭建 OCR 基础设施。

**ROI 校准工具的演进**：一开始用 OpenCV 的 `cv2.selectROI()` 做交互式标注（04-08, commit a5fe81c），用鼠标框选区域。但这个方案每次标注都要重新运行脚本，标注结果不能可视化验证。后来改成了网页版的 `roi_annotator.py`，左侧列出所有视频，右侧画布上用蓝色框标对话框、红色框标名字框，Ctrl+S 保存到 YAML 配置文件。这个工具一直用到现在。

**视频处理基础设施**：最初用 ffmpeg 命令行提取帧（04-09, commit dd66375），但 ffmpeg 的 subprocess 调用不稳定，难以精确控制帧率。后来换成了 PyAV（04-14, commit f78e225），直接用 Python 操作视频容器，按时间戳精确采样，稳定性好很多。

### Phase 2: 事件检测状态机与截断问题（04-09 ~ 04-14）

OCR 引擎选了 PaddleOCR（GPU 加速，中文识别准确），很快遇到了核心难题：**如何判断一句对话什么时候结束？**

游戏剧情视频里的对话通常有"打字机效果"——文字逐字逐句显现，而不是一次性全部出现。如果每一帧都 OCR 一次，会得到大量重复和不完整的文本片段。需要一个状态机来判断：这句话是在增长（还没说完），还是已经稳定（说完了），还是被新的一句话替换了。

**第一版状态机**（04-09, commit 4e774c9）：设计了 5 个状态（IDLE → DETECTED → GROWING → STABLE → FINALIZED），用固定阈值判断文本是否增长。逻辑是：如果连续 3 帧文本长度不变，就认为对话结束。这个版本能跑通，但截断率很高——打字机效果中文字可能会短暂停顿然后继续增长，固定阈值会在停顿时过早地判定结束。

**说话人识别**（04-09, commit a3ab2e7）：除了对话内容，还需要识别是谁在说话。名字框的 OCR 结果经常是空的（游戏里很多对话不显示名字），所以加了"说话人继承"机制——如果当前帧名字框为空，就继承上一个已知的说话人。

**打字机截断问题的持续调试**（04-11 ~ 04-23）：截断问题困扰了好几天。尝试过很多方案：增加稳定帧阈值（从 3 帧改到 5 帧）、降低相似度阈值（从 0.6 降到 0.5）、区分"经历过增长"和"从未增长"的事件给前者更长的等待时间、修复文本替换判断逻辑（之前用 SequenceMatcher 计算相似度，但"灼热的空"和"灼热的空气让肺部最后一丝生息也变得虚无"的相似度只有 0.21 会被误判为替换，改成先检查子串包含关系和前缀匹配再用相似度兜底）。这些调整都是治标不治本，截断率从最初的 23.3% 降到了 5% 左右，但还是不够理想。

**后置合并方案**（04-12, commit 8762bc9, 200c791）：既然实时判断很难做到完美，那就在 OCR 完成后做后处理。`post_merge.py` 会扫描所有事件，找到"同一说话人 + 时间间隔 <5s + 前缀相似度 ≥0.65"的相邻事件，把它们合并成一个完整对话。这个方案把截断率降到了 1% 以下。

### Phase 3: ROI 自动检测的失败尝试（04-21）

手工标注 ROI 很繁琐，每个视频都要打开网页工具画框。想过能不能自动检测？

**自动检测方案**（04-21, commit d27fe0b, 86a6bb1）：`auto_roi.py` 的思路是：采样视频前 2 分钟约 40 帧，运行 PaddleOCR detection-only 模式（只检测文本框位置，不识别内容），收集所有检测框的 y 坐标，用 DBSCAN 聚类找到对话框区域和名字框区域，输出标准配置 YAML。

**为什么放弃**：实际视频里有太多干扰因素——战斗 HUD、特效字幕、半透明对话框、动态背景。DBSCAN 聚类经常把这些噪声也聚进来，导致 ROI 不准确。而且自动检测的结果需要人工验证，验证失败还是要手工校准，反而增加了工作量。最终决定还是用手工标注，一次标好、长期可用，反而更可靠。

### Phase 4: 自适应状态机（04-29）

后置合并虽然有效，但治标不治本。真正的解决方案是让状态机变得更聪明——不用固定阈值，而是根据文本增长速度动态调整等待时间。

**增长率追踪**（04-29, commit 85e8849）：用滑动窗口（SMA(5)）计算每帧的字符增长速度，通过 sigmoid 函数映射为连续的增长置信度 [0,1]，然后动态计算稳定阈值。打字速度快就少等，慢就多等。**像素差分预触发（MAD skip）**减少了 30-40% 的 OCR 调用。**累积 Levenshtein 停时判据**处理了 OCR 抖动导致的微小字符变化。这三个机制组合后截断率降到了 <0.2%。同一个 commit 也完成了管线架构统一，一条命令完成全流程，支持断点续传。

### Phase 5: 说话人去噪与批量验证（05-08）

名字框 OCR 经常带有后缀噪声（"舰长EKR"、"姬子福"），写了 `speaker_denoiser.py` 用正则规则和上下文推断批量清理，处理 9,000 事件修正了 403 个错误（4.48% 修正率）。批量处理 7 个视频后手工抽查验证，各项指标达标。

---

## 致谢

本项目的架构设计参考了 [colleague-skill](https://github.com/titanwings/colleague-skill)（MIT License），将其「同事蒸馏」方法迁移到虚构角色领域。

角色「月下」及相关设定属于米哈游《崩坏3rd》。本项目仅用于个人学习和研究目的。

---

## License

[MIT](LICENSE)
