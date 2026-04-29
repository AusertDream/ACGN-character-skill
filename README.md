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

**OCR 对话提取管线现已完成统一架构重构（2026-04-29），截断率 1.25%（目标 <5%），文本可读性良好，可直接用于角色蒸馏。说话人识别仍是已知短板。**

## 这个项目做了什么

colleague-skill 的核心思路是将一个真实同事的专业能力和人格特征分别提取、结构化，然后合并为一个可执行的 AI Skill。本项目将这一方法迁移到虚构角色领域：用 OCR 对话提取替代聊天记录采集，用角色设定（Story）替代工作能力（Work），用适配后的5层人格模型捕捉角色的说话方式、情感模式和行为准则。

整个流程：游戏剧情视频 → OCR 对话提取 → 角色信息提取 → 结构化生成 → 可对话的角色 Skill。

---

## 当前能力

### 视频对话提取管线（tools/unified_pipeline.py）

统一端到端管线：`Video → AutoROICalibrator → FrameExtractor → BatchOCR → PostMerge → TextCorrector → JSONL + TXT`

- **自适应状态机**：增长率追踪 + 像素差分预触发 + Levenshtein 停时判据，正确处理打字机效果
- **自动 ROI 检测**：利用 PaddleOCR DBNet 检测框聚类，无需手工配置对话框坐标
- **Checkpoint/Resume**：中断后可从上次位置继续，每完成一个事件写检查点
- **多 GPU 并行**：Stage 2 支持跨 GPU 批量 OCR，仅使用 GPU 2/3（L40S 45GB）
- **后置纠错流水线**：正则规则 + 可选 LLM 纠错（deepseek-v4-flash/pro）
- **结构化输出**：层级目录 `event_XXXXXX/{frame.png, dialog.png, name.png}` + JSONL + 纯文本台本

**7 视频全量验证结果（崩坏3舰长线，9,026 事件）：**

| 指标 | 值 | 目标 | 状态 |
|:---|:---|:---|:---|
| 截断率（肉眼评估） | 1.25% | <5% | PASS |
| 文本可读性 | 3.5-4/5 | 通顺可读 | PASS |
| OCR 噪声比例 | 5-10% | 可接受 | — |
| 说话人识别 | 1-3/5 | 已知短板 | 待解决 |

### 角色 Skill 创建器（SKILL.md）

- 从 OCR 提取的对话文本提取角色设定（Story）和五层人格（Persona）
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
│   ├── auto_roi.py             #   自动 ROI 检测（PaddleOCR dt_polys 聚类）
│   ├── post_merge.py           #   后置前缀合并 + 战斗文字过滤
│   ├── llm_corrector.py        #   LLM OCR 纠错（deepseek-v4-flash/pro，可选）
│   ├── ocr_engines.py          #   OCR 引擎工厂（PaddleOCR/EasyOCR/RapidOCR）
│   ├── ocr_fusion.py           #   多引擎 OCR 融合策略
│   ├── ocr_postprocess.py      #   正则纠错规则
│   ├── output_schema.py        #   统一输出格式定义
│   ├── metrics.py              #   性能指标追踪
│   ├── preprocessing.py        #   图像预处理 profile
│   ├── speaker_extractor.py    #   说话人识别与别名归一化
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
# 安装角色创建器
npx skills add AusertDream/ACGN-character-skill

# 安装月下角色
npx skills add AusertDream/ACGN-character-skill/tree/main/characters/yuexia
```

安装完成后，在 Claude Code 中：

```
/ACGN-character.skill    # 创建新角色 Skill
/character-yuexia        # 与月下对话
```

---

## 生成的 Skill 结构

月下的最终 Skill（`characters/yuexia/SKILL.md`）由两部分组成：

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

统一端到端管线：自动 ROI 检测 → 自适应状态机事件检测 → 批量 OCR → 后置合并 → 文本纠错 → JSONL + 纯文本输出。

运行方式（需先 `conda activate paddleocr`，GPU 仅限 2/3）：

```bash
# 单个视频（自动 ROI 检测）
python -m tools.unified_pipeline "video.mp4" --auto-roi --gpus 2,3

# 单个视频（使用已有配置）
python -m tools.unified_pipeline "video.mp4" --config tools/configs/yuexia.yaml --gpus 2,3

# 批量处理全部视频
python -m tools.process_all_videos
```

---

## 进化机制

与 colleague-skill 一致，支持两种进化方式：

**追加材料**：提供新的视频文件，通过 OCR 提取对话后自动分析增量内容并 merge 到 story.md 和 persona.md 中，不覆盖已有结论。

**对话纠正**：在角色扮演过程中说「她不会这样说」「她应该是……」，系统会识别纠正意图，生成 Correction 记录写入对应文件，立即生效。

---

## 致谢

本项目的架构设计参考了 [colleague-skill](https://github.com/titanwings/colleague-skill)（MIT License），将其「同事蒸馏」方法迁移到虚构角色领域。

角色「月下」及相关设定属于米哈游《崩坏3rd》。本项目仅用于个人学习和研究目的。
