<div align="center">

# ACGN-character-skill

> *"吸血鬼不信神，也不信命运，但像这样出现在我面前的你，一定是我遇到过的最大的奇迹。"*

[![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet)](https://claude.ai/code)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://python.org)
[![PaddleOCR](https://img.shields.io/badge/PaddleOCR-OCR%20Pipeline-orange)](https://github.com/PaddlePaddle/PaddleOCR)

![立绘图](imgs/立绘图.png)



<br>

将虚构角色蒸馏成可对话的 AI Skill。<br>
从 ACGN 游戏剧情视频中提取角色的故事设定与人格特征，<br>
生成一个**用她的语气说话、以她的方式思考、带着她的情感回应**的角色扮演 Skill。<br>
内置 OCR 对话提取工具，支持无语音剧情视频的文本提取。

本项目以崩坏3舰长线角色「月下」为首个实例，<br>
架构参考 [colleague-skill](https://github.com/titanwings/colleague-skill) 的二层蒸馏方法，<br>
将「工作能力 + 人格」适配为「角色设定 + 人格」。

</div>

**OCR 对话提取管线现已完成统一架构重构（2026-05-08），截断率 <0.2%（目标 <5%），文本可读性良好，可直接用于角色蒸馏。说话人 OCR 后缀噪声已通过 LLM 批量去噪处理。**

## 什么是 Skill

Skill 是 Claude Code 的扩展能力单元，以一个包含 `SKILL.md` 的 Git 仓库形式存在。安装到 Claude Code 后，可以通过斜杠命令（如 `/ACGN-character`）触发。当用户输入对应命令时，Claude 会读取 SKILL.md 中定义的指令、工具使用规则和工作流程，按照其中的步骤自动执行任务。

本项目是一个**角色蒸馏工具 Skill**。它从游戏视频中提取对话，分析角色的故事设定和人格特征，生成结构化的角色扮演 prompt。这些 prompt 让 Claude 能够以该角色的身份、语气、思维方式进行对话。

## 这个项目做了什么

colleague-skill 的核心思路是将一个真实同事的专业能力和人格特征分别提取、结构化，然后合并为一套可执行的角色扮演指令。本项目将这一方法迁移到虚构角色领域：用 OCR 对话提取替代聊天记录采集，用角色设定（Story）替代工作能力（Work），用适配后的5层人格模型捕捉角色的说话方式、情感模式和行为准则。

整个流程：游戏剧情视频 → OCR 对话提取 → 角色信息提取 → 结构化生成 → 角色扮演 prompt → Claude 以角色身份对话。

---

## 当前能力

### 视频对话提取管线（tools/unified_pipeline.py）

统一端到端管线：`Video → FrameExtractor → BatchOCR → PostMerge → SpeakerDenoiser → TextCorrector → JSONL + TXT`

- **自适应状态机**：增长率追踪 + 像素差分预触发（MAD skip）+ Levenshtein 停时判据，正确处理打字机效果
- **手工 ROI 标注**：网页工具 `roi_annotator.py`，可视化标注对话框和名字框坐标（已放弃自动检测方案）
- **Checkpoint/Resume**：中断后可从上次位置继续，每完成一个事件写检查点
- **说话人识别**：
  - 空白名字框 → 旁白
  - 全问号名字框（???）→ 未知角色
  - 正常名字 → 保持原样（不做归一化）
- **说话人去噪**：LLM 批量清理 OCR 后缀噪声（deepseek-v4-flash），修正率 4.48%
- **后置纠错流水线**：正则规则 + 可选 LLM 纠错（deepseek-v4-flash/pro）
- **结构化输出**：层级目录 `event_XXXXXX/{frame.png, dialog.png, name.png}` + JSONL + 纯文本台本

**7 视频全量验证结果（崩坏3舰长线，9,000 事件）：**

| 指标 | 值 | 目标 | 状态 |
|:---|:---|:---|:---|
| 截断率（人工评估） | <0.2% | <5% | PASS |
| 文本可读性 | 3.5-4/5 | 通顺可读 | PASS |
| 说话人识别率 | 73-93% | 可用 | PASS |
| OCR 后缀噪声 | 已清理 | — | PASS |

### 角色 Skill 创建器（SKILL.md）

- 从 OCR 提取的对话文本提取角色设定（Story）和五层人格（Persona）
- 生成结构化的角色扮演 prompt，让 Claude 以角色身份对话
- 支持增量更新和对话纠正

---

## 计划中的能力

### 近期
- **VLM 兜底**：低置信度事件调用多模态 API（需配置 API key），处理半透明/特效字幕等难例

### 中期
- **Anime 支持**：动画视频的字幕提取（硬字幕 + 软字幕）
- **Comic 支持**：漫画图片的对话框文字提取
- **Novel 支持**：轻小说/视觉小说文本文件直接导入

### 长期
- **多作品批量管理**：任务队列、进度追踪、质量报表
- **脚本匹配增强**：若有现成剧本，OCR 结果与脚本模糊匹配提纯

---

## 项目结构

```
ACGN-character-skill/
├── SKILL.md                    # 角色 Skill 创建器入口
├── prompts/                    # Prompt 模板（story/persona analyzer & builder）
├── tools/
│   ├── unified_pipeline.py     #   统一 CLI 入口，串联全流程
│   ├── frame_extractor.py      #   Stage 1 事件检测 + 帧保存（checkpoint/resume）
│   ├── batch_ocr.py            #   Stage 2 批量 OCR 处理器
│   ├── event_detector.py       #   自适应状态机（增长率+MAD skip+Levenshtein）
│   ├── roi_annotator.py        #   网页版手工 ROI 标注工具
│   ├── post_merge.py           #   后置前缀合并 + 战斗文字过滤
│   ├── speaker_denoiser.py     #   说话人 OCR 噪声清洗
│   ├── llm_corrector.py        #   LLM OCR 纠错（deepseek-v4-flash/pro，可选）
│   ├── ocr_engines.py          #   OCR 引擎工厂（PaddleOCR/EasyOCR/RapidOCR）
│   ├── ocr_fusion.py           #   多引擎 OCR 融合策略
│   ├── ocr_postprocess.py      #   正则纠错规则
│   ├── output_schema.py        #   统一输出格式定义
│   ├── metrics.py              #   性能指标追踪
│   ├── preprocessing.py        #   图像预处理 profile
│   ├── speaker_extractor.py    #   说话人识别（空白→旁白，???→未知角色）
│   ├── video_processor.py      #   视频帧提取与 ROI 裁剪
│   ├── work_config.py          #   配置系统
│   └── configs/                #   每部作品的 ROI 配置文件
├── characters/
│   └── yuexia/                 #   月下的生成产物
└── benchmark/                  #   评估数据与脚本
```

---

## 架构说明

本项目的架构参考了 [colleague-skill](https://github.com/titanwings/colleague-skill) 的二层蒸馏方法。colleague-skill 将真实同事拆分为「工作能力」和「人格特征」两个维度分别提取，本项目将同样的思路迁移到虚构角色领域，用「角色设定（Story）」替代工作能力，用适配后的5层人格模型替代原版的职场人格结构。数据来源从飞书/钉钉聊天记录替换为游戏剧情视频的 OCR 对话提取。

---

## 安装与使用

### 安装 Skill

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

## 生成的角色扮演 prompt 结构

月下的最终角色扮演 prompt（`characters/yuexia/SKILL.md`）由两部分组成：

| 部分                | 内容                                       |
| ----------------- | ---------------------------------------- |
| **Part A — 角色设定** | 世界观、角色身份与能力、故事线（按章节）、人物关系、知识库（知道/不知道的事实） |
| **Part B — 人格**   | Layer 0 核心规则 → Layer 1 身份认知 → Layer 2 表达风格 → Layer 3 情感与决策 → Layer 4 关系行为 → Layer 5 边界与禁区 |

运行规则：以月下第一人称对话 → Persona 决定当前态度和情绪 → Story 确认认知范围 → 始终保持 Layer 2 的表达风格 → Layer 0 不可违背。

---

## 效果示例

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

## 数据来源

本项目的训练数据来自崩坏3舰长线全剧情视频，涵盖以下章节：

| 章节        | 标题       | 内容                 |
| --------- | -------- | ------------------ |
| 第一节       | 仲夏幻夜     | 月下与舰长的初遇           |
| 第八节       | 星与你消失之日  | 圣贤王的棋局             |
| 第十七节      | 在长梦弥散之前  | 因为语音内容较少，所以暂时没有    |
| 第十八节（3部分） | 当红月落幕之后  | 同上，暂时没有这部分记忆       |
| 第十八节支线    | 月下全回忆和彩蛋 | 月下的核心独白与记忆（信息密度最高） |
| 第十九节      | 牧场奇谭     | 日常生活与归宿            |

所有章节均通过 OCR 对话提取管线处理，最终角色数据主要依据仲夏幻夜、月下回忆彩蛋、牧场奇谭三个章节生成，其余章节因角色出场有限仅作参考。

视频来源：B站 UP主 [MC神神希](https://space.bilibili.com/666904408)

Live2D 来源：B站 [支线路人A](https://space.bilibili.com/1152374880)

---

## OCR 对话提取

统一端到端管线：手工 ROI 标注 → 自适应状态机事件检测 → 批量 OCR → 后置合并 → 说话人去噪 → 文本纠错 → JSONL + 纯文本输出。

运行方式（需先 `conda activate paddleocr`，GPU 通过 `CUDA_VISIBLE_DEVICES` 指定）：

```bash
# 单个视频（使用已有配置）
CUDA_VISIBLE_DEVICES=0 python -m tools.unified_pipeline "video.mp4" --config tools/configs/yuexia_ep01_roi.yaml

# 批量处理全部视频
CUDA_VISIBLE_DEVICES=0 python -m tools.process_all_videos

# 手工 ROI 标注（不需要 GPU）
python -m tools.roi_annotator --port 11451
# 浏览器打开 http://localhost:11451，画蓝色对话框和红色名字框，Ctrl+S 保存
```

---

## 进化机制

与 colleague-skill 一致，支持两种进化方式：

**追加材料**：提供新的视频文件，通过 OCR 提取对话后自动分析增量内容并 merge 到 story.md 和 persona.md 中，不覆盖已有结论。

**对话纠正**：在角色扮演过程中说「她不会这样说」「她应该是……」，系统会识别纠正意图，生成 Correction 记录写入对应文件，立即生效。

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

**打字机截断问题的持续调试**（04-11 ~ 04-23）：截断问题困扰了好几天。尝试过很多方案：

- 增加稳定帧阈值（从 3 帧改到 5 帧，04-23, commit 5a63b4c）
- 降低相似度阈值（从 0.6 降到 0.5）
- 区分"经历过增长"和"从未增长"的事件，给前者更长的等待时间（post_growth_stable_threshold = 10）
- 修复文本替换判断逻辑（04-24, commit 0686c7d）：之前用 SequenceMatcher 计算相似度，但"灼热的空"和"灼热的空气让肺部最后一丝生息也变得虚无"的相似度只有 0.21，会被误判为替换。改成先检查子串包含关系和前缀匹配，再用相似度兜底

但这些调整都是治标不治本，截断率从最初的 23.3% 降到了 5% 左右，但还是不够理想。

**后置合并方案**（04-12, commit 8762bc9, 200c791）：既然实时判断很难做到完美，那就在 OCR 完成后做后处理。`post_merge.py` 会扫描所有事件，找到"同一说话人 + 时间间隔 <5s + 前缀相似度 ≥0.65"的相邻事件，把它们合并成一个完整对话。这个方案把截断率降到了 1% 以下。

### Phase 3: ROI 自动检测的失败尝试（04-21）

手工标注 ROI 很繁琐，每个视频都要打开网页工具画框。想过能不能自动检测？

**自动检测方案**（04-21, commit d27fe0b, 86a6bb1）：`auto_roi.py` 的思路是：采样视频前 2 分钟约 40 帧，运行 PaddleOCR detection-only 模式（只检测文本框位置，不识别内容），收集所有检测框的 y 坐标，用 DBSCAN 聚类找到对话框区域（最下方的聚类）和名字框区域（对话框上方的聚类），输出标准 WorkConfig YAML。

**为什么放弃**：自动检测在理想情况下能工作，但实际视频里有太多干扰因素——战斗 HUD、特效字幕、半透明对话框、动态背景。DBSCAN 聚类经常把这些噪声也聚进来，导致 ROI 不准确。而且自动检测的结果需要人工验证（内置了 5 帧采样验证），验证失败还是要手工校准，反而增加了工作量。最终决定还是用手工标注，一次标好、长期可用，反而更可靠。

### Phase 4: 自适应状态机（04-29）

后置合并虽然有效，但治标不治本。真正的解决方案是让状态机变得更聪明——不用固定阈值，而是根据文本增长速度动态调整等待时间。

**增长率追踪**（04-29, commit 85e8849）：用滑动窗口（SMA(5)）计算每帧的字符增长速度 `delta_chars_per_frame`，通过 sigmoid 函数映射为连续的增长置信度 [0,1]，然后动态计算稳定阈值 = max(5, int(growth_confidence * 15))。打字速度快就少等，慢就多等。

**像素差分预触发（MAD skip）**：活跃事件期间，计算当前帧与上一帧 ROI 的 Mean Absolute Difference。如果 MAD < 2%（画面几乎没变化），直接跳过 OCR，等同于空帧。这个优化减少了 30-40% 的 OCR 调用，大幅提升了处理速度。

**累积 Levenshtein 停时判据**：用滑动窗口（size=3）累加编辑距离，只有当累积编辑距离 ≤2 且自适应阈值帧数已过，才最终确定事件。这个机制处理了 OCR 抖动导致的微小字符变化（比如"的"和"地"反复横跳）。

这三个机制组合后，截断率降到了 <0.2%，终于达到了可用标准。同一个 commit 也完成了管线架构统一——把之前分散的 `dialogue_extractor.py`、`batch_ocr.py`、`post_merge.py`、`llm_corrector.py` 串联成 `unified_pipeline.py`，一条命令完成全流程，支持 checkpoint/resume 断点续传。

### Phase 5: 说话人识别的噪声问题（05-08）

名字框的 OCR 结果经常带有后缀噪声，比如"舰长EKR"、"姬子福"、"琪亚娜享"、"芽衣Ta]"。这些噪声来自 OCR 引擎把名字框边缘的装饰元素或背景纹理误识别为文字。

**最初的方案**：在 `speaker_extractor.py` 里加了别名映射（speaker_aliases），手工维护一个"正确名字 → 可能的错误变体"的字典。但这个方案不可扩展——每次发现新的噪声模式都要手工添加规则。

**去噪方案**（05-08, commit 84a773f）：写了 `speaker_denoiser.py`，用正则规则和上下文推断批量清理 OCR 结果。提供角色名单（舰长、姬子、琪亚娜、芽衣、布洛妮娅、德丽莎、符华、旁白、系统等），对每个 OCR 结果做后缀噪声去除、模糊匹配和上下文推断。处理了 9,000 个事件，修正了 403 个错误（4.48% 修正率）。

**特殊说话人处理**：除了角色名字，还有两种特殊情况需要处理：
- 空白名字框（OCR 返回空字符串或纯空格）→ 映射为"旁白"
- 全是问号的名字框（"???"、"？？？"）→ 映射为"未知角色"

这两个规则在 `speaker_extractor.py` 里实现，优先级高于去噪流程。

### Phase 6: 批量处理与质量验证（05-08）

单个视频能跑通后，批量处理崩坏3舰长线的全部 7 个视频（第一节、第十七节、第十八节 3 部分、第十八节支线、第十九节）。

**质量评估**：处理完成后手工抽查，评估截断率、文本可读性、说话人识别率。最终结果：
- 截断率 <0.2%（目标 <5%）✓
- 文本可读性 3.5-4/5（通顺可读）✓
- 说话人识别率 73-93%（可用）✓
- OCR 后缀噪声已清理 ✓

### 当前状态与未来方向

OCR 对话提取管线已经完成并验证可用。接下来的工作是角色 Skill 生成部分——从提取的对话文本中分析角色设定（Story）和人格特征（Persona），生成角色扮演 prompt。这部分的框架已经搭好（`prompts/` 目录下的 analyzer 和 builder），但效果还需要调试。

技术上还有一些可以改进的地方：
- VLM 兜底：对于低置信度的 OCR 结果，调用多模态 API 重新识别
- 脚本匹配增强：如果有现成的游戏剧本，可以用模糊匹配把 OCR 结果和剧本对齐，提升准确率
- 多作品批量管理：任务队列、进度追踪、质量报表

但核心的 OCR 管线已经足够可靠，可以开始专注于角色蒸馏的部分了。

---

## 致谢

本项目的架构设计参考了 [colleague-skill](https://github.com/titanwings/colleague-skill)（MIT License），将其「同事蒸馏」方法迁移到虚构角色领域。

角色「月下」及相关设定属于米哈游《崩坏3rd》。本项目仅用于个人学习和研究目的。
