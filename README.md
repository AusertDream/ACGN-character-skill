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

**目前最终效果还在优化与调试。还处于半成品的状态。**

## 这个项目做了什么

colleague-skill 的核心思路是将一个真实同事的专业能力和人格特征分别提取、结构化，然后合并为一个可执行的 AI Skill。本项目将这一方法迁移到虚构角色领域：用 OCR 对话提取替代聊天记录采集，用角色设定（Story）替代工作能力（Work），用适配后的5层人格模型捕捉角色的说话方式、情感模式和行为准则。

整个流程：游戏剧情视频 → OCR 对话提取 → 角色信息提取 → 结构化生成 → 可对话的角色 Skill。

---

## 当前能力

### 视频对话提取工具（tools/dialogue_extractor.py）

基于 OCR-first 方案，从 ACGN 视觉小说风格视频中提取对话台本：

- **对话事件检测**：状态机驱动（IDLE → DETECTED → GROWING → STABLE → FINALIZED），围绕"对话事件"而非单帧识别
- **打字机效果处理**：前缀增长检测 + 事后合并，189 个原始帧事件合并为 116 个完整对话事件
- **ROI 精准识别**：只对名字框和对话框区域做 OCR，支持每部作品独立配置（tools/configs/）
- **多引擎 OCR 融合**：PaddleOCR 主引擎 + EasyOCR 备用，置信度加权融合
- **半透明背景预处理**：多种预处理 profile（plain_light_bg / plain_dark_bg / semi_transparent_hsv / outline_heavy）
- **说话人识别**：名字框 OCR + 角色别名词典 + 上下文继承
- **战斗/HUD 文本过滤**：正则过滤战斗演出、分数显示等非对话内容
- **低置信度标记**：review_required 字段标记，5/116 事件（4.31%）需人工复核
- **结构化输出**：JSONL（含 event_id、时间戳、speaker、text、confidence、review_required、provenance）+ 纯文本台本（[HH:MM:SS] Speaker: Dialogue 格式）
- **断点续跑**：支持中断后从上次位置继续处理
- **人工复核 UI**：review_server.py 提供 Web 界面，展示关键帧、ROI 图、OCR 候选

**benchmark 验证结果（崩坏3仲夏幻夜，10分钟片段）：**

| 指标              | 值              | 目标          | 状态   |
| --------------- | -------------- | ----------- | ---- |
| Recall（时间重叠匹配）  | 100% (116/116) | ≥90%        | PASS |
| Mean CER        | 6.64%          | user-agreed | PASS |
| Duplicate rate  | 0%             | <10%        | PASS |
| Review rate     | 4.31%          | <5%         | PASS |
| False positives | 0              | 0           | PASS |

### 角色 Skill 创建器（SKILL.md）

- 从 OCR 提取的对话文本提取角色设定（Story）和五层人格（Persona）
- 支持增量更新：追加新视频数据自动 merge
- 支持对话纠正：说"她不会这样说"自动写入 Correction 记录

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
├── SKILL.md                    # 角色 Skill 创建器入口（/ACGN-character.skill）
├── prompts/                    # Prompt 模板
│   ├── story_analyzer.md       #   角色设定提取
│   ├── persona_analyzer.md     #   角色人格提取
│   └── ...
├── tools/
│   ├── dialogue_extractor.py   #   OCR 对话提取主入口
│   ├── event_detector.py       #   对话事件检测状态机
│   ├── ocr_engines.py          #   OCR 引擎工厂（PaddleOCR/EasyOCR/RapidOCR）
│   ├── ocr_fusion.py           #   多引擎 OCR 融合策略
│   ├── video_processor.py      #   视频帧提取与 ROI 裁剪
│   ├── speaker_extractor.py    #   说话人识别与别名归一化
│   ├── preprocessing.py        #   图像预处理 profile
│   ├── output_formatter.py     #   JSONL 结构化输出
│   ├── text_output.py          #   JSONL → 纯文本转换
│   ├── review_ui.py            #   人工复核 HTML 页面生成
│   ├── roi_calibrator.py       #   交互式 ROI 校准工具
│   └── configs/
│       └── yuexia.yaml         #   月下 ROI 配置
├── characters/
│   └── yuexia/                 #   月下的生成产物（/character-yuexia）
│       ├── SKILL.md
│       ├── story.md
│       └── persona.md
└── benchmark/                  #   评估数据与脚本
```

另有以下数据目录（视频文件不纳入版本控制）：

```
training data/                  # 原始视频 + OCR 提取产物
├── *.mp4                       #   崩坏3舰长线剧情视频（8个，gitignored）
├── ocr_output/                 #   OCR 对话提取产物（.jsonl + .txt）
└── acknowledgement.txt         #   视频来源说明
live2d/                         # 月下 Live2D 模型文件
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

项目使用 PaddleOCR 作为主引擎，EasyOCR 作为备用引擎，通过 ROI 区域裁剪 + 状态机事件检测 + 打字机合并的方式从游戏剧情视频中提取对话台本。每部作品需要一份独立的 ROI 配置文件（`tools/configs/*.yaml`），定义对话框和名字框的位置。

运行方式（需要安装 paddlepaddle 和 paddleocr）：

```bash
# 单个视频
python -m tools.dialogue_extractor "training data/视频文件.mp4" tools/configs/yuexia.yaml --output-dir "training data/ocr_output"

# 批量处理
python -m tools.dialogue_extractor "training data" tools/configs/yuexia.yaml --output-dir "training data/ocr_output" --batch --video-pattern "*.mp4"
```

提取产物保存在输出目录下，每个视频对应一个 `.jsonl`（结构化数据）和一个 `.txt`（纯文本台本）文件。

---

## 进化机制

与 colleague-skill 一致，支持两种进化方式：

**追加材料**：提供新的视频文件，通过 OCR 提取对话后自动分析增量内容并 merge 到 story.md 和 persona.md 中，不覆盖已有结论。

**对话纠正**：在角色扮演过程中说「她不会这样说」「她应该是……」，系统会识别纠正意图，生成 Correction 记录写入对应文件，立即生效。

---

## 致谢

本项目的架构设计参考了 [colleague-skill](https://github.com/titanwings/colleague-skill)（MIT License），将其「同事蒸馏」方法迁移到虚构角色领域。

角色「月下」及相关设定属于米哈游《崩坏3rd》。本项目仅用于个人学习和研究目的。
