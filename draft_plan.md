# ACGN 长视频对话抽取技术方案报告（Markdown 版）

- 文档版本：v1.0
- 调研日期：2026-04-08
- 面向场景：ACGN / Galgame / 视觉小说风格长视频，对话框+人名框为主，夹杂战斗演出、CG、实机演示、旁白/系统字样，部分有配音、部分无配音。
- 输出目标：高质量导出为**纯文本对话台本**，尽量保持 `说话人：台词` 结构，并支持后续人工复核与迭代优化。

---

## 重要！！！

前情提要：当前项目为借鉴同事.skill所做的ACGN-character.skill，虽然当前的命名为yuexia.skill，之前根据training data里面的视频数据，通过ASR提取其中的对话，参考同事.skill的蒸馏数据的方法，得到的月下的人格与故事设定，然而ASR的方案对于人物数据来说并不高质量，部分视频没有配音导致无法提取到语音，此外各种杂音和BGM导致提取质量很差。因此我们需要有一个工具来将视频中的对话文本给高质量的提取出来作为纯文本内容。为了解决这个需求，才有了下面的这个方案报告。从视频到对话台本，这是当前ACGN-character.skill的其中一个工具，是封装在这个skill里面的，是作为其中的一个功能来使用的。之后对于ACGN中的Anime， Comic， Game， Novel都需要进行一定程度的支持，目前就只做这种视频处理即可。本方案中存在大量的参考项目链接，对于github上的开源项目，需要查看的，通过git clone到本地，然后再本地查看，不要通过web fetch，git clone之后，记得把其中的.git文件夹删掉，不然会导致git in git问题。如果github访问不到，使用代理，本机的10808端口。

## 目录

1. [执行摘要](#执行摘要)
2. [需求分析](#需求分析)
3. [问题拆解与关键难点](#问题拆解与关键难点)
4. [社区调研结果](#社区调研结果)
5. [应参考的开源项目与借鉴点](#应参考的开源项目与借鉴点)
6. [总体技术路线](#总体技术路线)
7. [可执行的项目总纲](#可执行的项目总纲)
8. [详细技术方案](#详细技术方案)
9. [数据结构与输出格式](#数据结构与输出格式)
10. [质量评估与验收标准](#质量评估与验收标准)
11. [成本、性能与部署策略](#成本性能与部署策略)
12. [风险清单与应对措施](#风险清单与应对措施)
13. [实施路线图](#实施路线图)
14. [推荐结论](#推荐结论)
15. [参考资料](#参考资料)

---

## 执行摘要

### 一句话结论

对你的场景，**最佳路线不是 ASR-first，而是 OCR-first**：  
以**视频画面中的对话框文字**为主信息源，围绕“**对话事件**”而不是“单帧文本”做处理；再以 **ASR** 作为配音句的辅助校对，以 **VLM / 闭源多模态 API** 作为**低置信度难例兜底**，最终输出结构化台本。

### 为什么不是直接用 Whisper
因为你的目标不是“听清说了什么”，而是“拿到视频里展示出来的正式文本”：

1. 有些句子没有配音，ASR 天然拿不到。
2. BGM、音效、战斗演出会显著污染语音识别。
3. 视觉小说式内容的真正“权威文本”通常已经出现在对话框中。
4. 你要的是**小说台本结构**，而不是一段时间轴转写。

### 方案核心
本报告建议构建一条四层流水线：

1. **对话段识别层**：从长视频里找出“处于对话界面”的片段。
2. **视觉抽取层**：只对名字框 / 对话框 ROI 做 OCR，并做多帧融合。
3. **结构化整理层**：将 OCR 结果整理为“说话人 + 台词 + 事件类型”。
4. **增强兜底层**：ASR 校对有声句；VLM/API 只处理低置信度难例；必要时引入脚本匹配。

最终形成一条“**本地为主、API 为辅、可批处理、可人工复核**”的高质量生产线。

---

## 需求分析

## 1. 业务目标

需要把数小时级别的 ACGN 视频中的对话内容提取为纯文本台本，要求：

- 尽可能完整地提取视频中出现的对白内容；
- 保持视觉小说的对话格式，而不是纯散文或字幕流；
- 对于显示了名字的人物，输出其角色名；
- 对于旁白、系统提示、选项、演出字幕等，保留相应标签；
- 最终适合作为小说脚本、文本存档、后续翻译或检索的基础数据。

## 2. 输入特征

输入视频具有以下特征：

- 以视觉小说式站桩对话为主；
- 存在固定或半固定的**人名框 + 对话框**布局；
- 有些视频会夹杂战斗、CG 演出、实机操作或 UI 过场；
- 有些句子有配音，有些没有；
- 对话框可能存在：
  - 半透明背景；
  - 描边字 / 阴影字；
  - 打字机逐字展开；
  - 长句跨多帧停留；
  - 不同 UI 主题、不同分辨率；
  - 名字框有时为空，或者使用“旁白 / 系统 / ???”。

## 3. 输出要求

最终输出不应只是 OCR 原文堆叠，而应尽可能接近：

```text
莉莉娅：梦莎莉娅梦莎莉娅，是的没错，他醒了。
梦莎莉娅：……
【旁白】夜色渐深，风声渐起。
【系统】获得道具：XXXX
【选项】1. 留下  2. 离开
```

并建议同时保留一份中间结构化数据，用于复核：

```json
{
  "event_id": "ep01_000123",
  "source_file": "path/to/source_file",
  "start_ms": 123456,
  "end_ms": 126980,
  "scene_type": "dialogue",
  "speaker": "莉莉娅",
  "text": "梦莎莉娅梦莎莉娅，是的没错，他醒了。",
  "ocr_confidence": 0.93,
  "asr_text": null,
  "review_required": false
}
```

## 4. 非功能性要求

- **高质量优先**：宁可慢一些，也要保证台本可用性。
- **批处理能力**：能处理数小时视频，不要求实时。
- **成本可控**：闭源 API 不能作为全量主链路。
- **可扩展**：后续应能适配不同作品、不同 UI。
- **可复核**：低置信度结果必须能回看和人工修正。
- **本地部署优先**：你已有 GPU，适合建设本地流程。

---

## 问题拆解与关键难点

## 1. 不能把“每帧 OCR”当成最终方案

视觉小说对话的最常见陷阱是：

- 同一句话会停留几十到几百帧；
- 前若干帧只是打字机残句；
- 转场帧会出现运动模糊；
- 对话框不变但角色表情变了；
- 一句话可能跨 1–3 秒稳定显示。

因此系统必须围绕“**对话事件**”而不是“每帧识别”来工作。

## 2. OCR 难点比 ASR 更复杂，但更接近真实文本

OCR 面临的主要问题：

- 半透明底板；
- 动画模糊；
- 字体描边 / 发光 / 阴影；
- 小字、多行、挤压换行；
- 立绘、特效、背景图干扰；
- 中文、日文、英数混排；
- 名字框与正文区样式不同。

但即便如此，对你的任务，OCR 仍然是主线，因为它能覆盖**无配音句**和**正式显示文本**。

## 3. 说话人识别并不等于单纯读名字框

说话人识别会遇到：

- 名字框缺失；
- 同一角色多种写法；
- `？？？`、`旁白`、`系统` 等特殊说话人；
- 战斗场景中台词直接浮在 HUD 或演出字幕里；
- UI 切换后名字框位置变化。

因此需要一套**说话人判定优先级策略**，而不是单次 OCR 读个名字就结束。

## 4. 社区已有工具多为“单点强项”，不是完整生产线

这一点很关键。社区里已经有：

- 擅长 Hook 的；
- 擅长实时 OCR 的；
- 擅长视频硬字幕抽取的；
- 擅长 ROI 批量图像 OCR 的；
- 擅长脚本匹配的；

但没有一个成熟开源项目能直接满足“**长视频 -> 高质量 VN 对话台本**”这一端到端需求。因此本方案的价值，不在于再造轮子，而在于**组合这些成熟思路**。

---

## 社区调研结果

## 1. 调研结论概览

对本需求最相关的社区资产大致分为四类：

### A. Hook / 文本抓取类
- **Textractor**：典型 VN 文本 hook 工具，可直接从游戏进程提取文本。[Textractor GitHub](https://github.com/Artikash/Textractor)
- **LunaTranslator**：将 HOOK、OCR、模拟器支持、翻译、TTS 等集成到同一工具链中，强调 HOOK 仍是主要提取方式。[LunaTranslator 官网](https://docs.lunatranslator.org/en/) / [GitHub](https://github.com/HIllya51/LunaTranslator)

### B. 实时 OCR / 读屏类
- **GameSentenceMiner (GSM)**：最值得借鉴的是其两阶段 OCR 设计，先快扫检测变化，再精扫定稿。[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/)
- **OwOCR**：多输入、多输出，支持 screen capture / OBS / websocket，且内建“两阶段”实用思路。[OwOCR GitHub](https://github.com/AuroraWright/owocr)
- **Visual Novel OCR**：重点解决视觉小说常见的半透明背景问题，依赖 mirror capture + color contrast threshold 思路。[Visual Novel OCR Guide](https://visual-novel-ocr.sourceforge.io/)
- **RSTGameTranslation**：更贴近你的目标，支持多 OCR 引擎、上下文感知、角色/地点名识别、chat window 场景优化。[RSTGameTranslation 官网](https://thanhkeke97.github.io/RSTGameTranslation/) / [GitHub](https://github.com/thanhkeke97/RSTGameTranslation)

### C. 视频 OCR / 硬字幕批处理类
- **VideOCR**：典型的硬字幕视频 OCR 骨架，适合参考其长视频批处理方式。[VideOCR GitHub](https://github.com/timminator/VideOCR)
- **VideoSubFinder**：擅长先从视频中找出带字帧，再生成清背景文字图供 OCR 使用。[VideoSubFinder SourceForge](https://sourceforge.net/projects/videosubfinder/)
- **visual-novel-game-ocr**：思路来自 videocr，强调关键帧筛选、RapidOCR、输出 txt/SRT/docx，非常适合参考其“只处理变化帧”的理念。[visual-novel-game-ocr GitHub](https://github.com/wanghaisheng/visual-novel-game-ocr)
- **video-text-extraction**：明确面向 visual novel gameplay video，强调去重和固定 ROI。[video-text-extraction GitHub](https://github.com/girubato/video-text-extraction)

### D. 结构化 / 脚本匹配 / ROI 工具类
- **GameDialogueOCR**：适合借鉴其“自定义 ROI + 批量 OCR”的交互方式。[GameDialogueOCR GitHub](https://github.com/purpyviolet/GameDialogueOCR)
- **Game2Text**：值得借鉴的是 OCR-assisted game script matching；如果能找到现成脚本，可显著提纯结果。[Game2Text GitHub](https://github.com/mathewthe2/Game2Text)

## 2. 调研结论的核心判断

从上述项目的官方说明可以看出：

- Hook 工具适合“有原游戏进程”的场景，不适合“只有录制视频”的场景；
- 实时 OCR 工具最贴近“VN 画面读字”的核心问题；
- 视频 OCR 工具最贴近“长视频离线批处理”的工程模式；
- 结构化工具和脚本匹配适合作为质量增强模块。

因此，**社区现状并不是缺“可借鉴的方案”，而是缺“能直接产出台本的一体化工程整合”**。  
这意味着最合理的选择是：**参考成熟项目的核心机制，自建一条围绕 VN 对话事件的批处理流水线**。

---

## 应参考的开源项目与借鉴点

下表给出“建议参考，不建议直接照搬”的清单。

| 项目                    | 重点借鉴内容                            | 不建议直接作为最终产品的原因          |
| --------------------- | --------------------------------- | ----------------------- |
| Textractor            | Hook 文本抓取思路；有原游戏时可作为旁路真值来源        | 你的主场景是视频，不是原游戏进程        |
| LunaTranslator        | HOOK + OCR + 翻译聚合框架，说明多来源融合是成熟路线  | 更偏实时阅读/翻译，不是长视频批量出剧本    |
| GameSentenceMiner     | **两阶段 OCR**：OCR1 快扫变化，OCR2 稳定后精扫  | 当前目标是离线批处理，不是实时学习工具     |
| OwOCR                 | 多输入源、多输出、两阶段优化、重复过滤               | 更偏实时通用 OCR，不含 VN 台本级后处理 |
| Visual Novel OCR      | **半透明对话框处理**、镜像截取、色彩阈值            | 依赖手动设置，自动化批处理能力不足       |
| RSTGameTranslation    | **上下文感知、角色名识别、VN chat window 优化** | 主要目标是翻译显示，不是脚本导出系统      |
| VideOCR               | 长视频硬字幕抽取骨架、CLI/GUI 批处理思路          | 不理解人物名框和 VN 结构          |
| VideoSubFinder        | **先找有字帧，再做 OCR** 的思想非常重要          | 面向通用 hardsub，不理解对话格式    |
| visual-novel-game-ocr | 变化帧筛选、RapidOCR、离线输出 txt/SRT/docx  | 更像轻量实验/工具，不是完整生产线       |
| video-text-extraction | 固定 ROI + 去重逻辑，很贴近 VN 视频           | 结构简单，OCR 与后处理能力有限       |
| GameDialogueOCR       | 自定义 ROI 标注与批处理交互                  | 主要是图像级工具，不是长视频系统        |
| Game2Text             | **OCR 辅助脚本匹配**                    | 依赖脚本库，不适合无脚本的通用主链路      |

---

## 总体技术路线

## 1. 路线选择

### 结论
采用：

> **OCR-first + 事件聚合 + 结构化后处理 + ASR/VLM 兜底**

而不是：

- 纯 ASR；
- 纯单帧 OCR；
- 纯闭源多模态 API；
- 纯规则脚本；
- 纯 Hook。

## 2. 设计原则

### 原则一：围绕“对话事件”建模
一个事件是“一条完整台词在屏幕上从出现到消失的过程”，而不是“一帧图像”。

### 原则二：先找“对话段”，再识别“对话框”
整个视频不必逐帧高精 OCR，先分出有效时段，再精处理。

### 原则三：固定 UI 的作品，必须允许每作单独标定 ROI
这会极大提升质量，远胜于盲跑通用 OCR。

### 原则四：低成本模块处理大多数样本，高成本模块只处理难例
大部分样本走本地 OCR；只有低置信度片段才调 VLM 或闭源 API。

### 原则五：保留中间结果，支持复核
任何高质量转录系统都应该保留：
- 帧图；
- ROI 图；
- OCR 候选；
- 置信度；
- ASR/VLM 候选；
- 最终定稿。

---

## 可执行的项目总纲

## 1. 目标成果物

本项目最终将工具封装在skill中，因此对用户来说对外成果物只是一个skill。而对于skill来说，最终至少产出以下成果：

1. **批处理命令行工具**
   - 输入：视频文件、作品配置
   - 输出：TXT / Markdown / JSONL / SRT（可选）

2. **作品配置系统**
   - 每部作品维护一份 UI 配置
   - 包括名字框 ROI、对话框 ROI、文本颜色策略、特殊场景策略

3. **低置信度复核工具**
   - 能看原帧、OCR 候选、最终文本
   - 支持手工修正

4. **评测与回归集**
   - 选取若干视频片段做人标金标
   - 用于评估完整率、错字率、重复率、说话人准确率

## 2. 交付阶段

### P0：PoC 验证版
目标：验证核心假设成立。

交付：
- 1 部作品，10–20 分钟样本；
- 能从标准对话 UI 中抽出台词；
- 能处理打字机展开；
- 能输出基本台本。

### P1：MVP 批处理版
目标：形成可跑完整视频的基本系统。

交付：
- 对话段检测；
- ROI OCR；
- 多帧融合；
- 去重；
- 说话人提取；
- TXT/Markdown 导出；
- 低置信度标记。

### P2：高质量增强版
目标：大幅提高复杂场景可用性。

交付：
- 半透明背景增强；
- 多 OCR 引擎策略；
- ASR 辅助；
- 本地/闭源 VLM 兜底；
- 战斗/CG/系统文本分类。

### P3：生产化版
目标：支持多作品、多视频、低人工成本。

交付：
- 作品配置管理；
- 批量队列；
- 复核 UI；
- 质量报表；
- 版本化输出与回归测试。

---

## 详细技术方案

## 1. 总体架构

```text
视频输入
  -> 视频解码与抽样
  -> 场景分类 / 对话段检测
  -> ROI 裁切（名字框、对话框）
  -> 文本变化检测
  -> OCR1 快扫
  -> 稳定判定
  -> OCR2 精扫
  -> 事件聚合（去重、残句合并、定稿）
  -> 说话人识别 / 特殊角色识别
  -> ASR 辅助（可选）
  -> VLM/API 低置信度兜底（可选）
  -> 文本清洗 / 结构化导出
  -> 人工复核
  -> 最终台本
```

---

## 2. 模块设计

## 2.1 视频解码与抽样模块

### 目标
高效读取长视频，并为后续分析提供稳定帧序列。

### 建议
- 使用 `ffmpeg` 做基础预处理；
- 使用 `PyAV` 或 `Decord` 做高效视频解码；
- 先按较低频率做粗扫描，例如 2–6 fps；
- 在检测到对话段后，再切换到较高频率分析，例如 8–15 fps。

### 原因
并不是所有帧都值得高成本处理。`visual-novel-game-ocr` 明确强调**不需要对整段视频每帧都做 OCR，而应先筛出变化关键帧**，这一点非常适合你的场景。[visual-novel-game-ocr GitHub](https://github.com/wanghaisheng/visual-novel-game-ocr)

---

## 2.2 对话段检测模块

### 目标
从整段视频中识别出“标准对话 UI 正在显示”的时段。

### 建议的实现层次

#### 第一层：轻规则
- 检测底部大矩形对话框区域的存在；
- 检测名字框位置的样式特征；
- 统计字幕框区域边缘 / 半透明层 / 固定色块特征；
- 用 OCR 探测框中是否持续有文本。

#### 第二层：轻量分类器
训练一个二分类模型：
- `dialogue_ui`
- `non_dialogue_ui`

输入可为整帧缩略图或底部区域图。

#### 第三层：多类场景分类器（进阶）
分类：
- 标准对话
- CG 演出字幕
- 战斗 HUD 对话
- 系统提示
- 选项界面
- 非文本场景

### 推荐策略
PoC 阶段用规则；P2 阶段再加分类器。

---

## 2.3 ROI 管理模块

### 目标
只对真正有意义的区域做 OCR。

### 为什么很重要
相比整帧 OCR，固定 ROI 的稳定性会高很多；而且人名框和正文区最好分开处理，因为字体、尺寸、色彩、文本长度都不同。

### 设计建议
每部作品配置一份 YAML / JSON：

```yaml
game_id: sample_vn
resolution_profile:
  base_width: 1920
  base_height: 1080
name_box:
  x: 95
  y: 708
  w: 210
  h: 78
dialog_box:
  x: 90
  y: 785
  w: 1540
  h: 170
special_modes:
  battle_caption:
    enabled: true
    roi: [120, 820, 1480, 120]
preprocess_profile:
  mode: semi_transparent_hsv
```

### 参考来源
- `GameDialogueOCR` 证明了**自定义 ROI**对文字游戏和 VN 转录很有价值。[GameDialogueOCR GitHub](https://github.com/purpyviolet/GameDialogueOCR)
- `video-text-extraction` 也明确依赖“text boxes are always in the same positions”这一假设。[video-text-extraction GitHub](https://github.com/girubato/video-text-extraction)

---

## 2.4 文本变化检测模块

### 目标
识别“这是不是一条新对白”。

### 关键思路
参考 `GameSentenceMiner` 的两阶段设计：  
先用快引擎持续扫描文本变化，稳定后再精识别。[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/)

### 实现建议

在 ROI 上维护时间序列：

- 图像差分：比较当前对话框 ROI 与上一时刻是否变化；
- OCR 文本差分：比较候选文本是否增长/变化；
- 稳定判定：若连续 N 帧差异低于阈值，则视为“文本稳定”。

### 打字机判定
若文本呈现“前缀扩展”模式，例如：

- `梦莎`
- `梦莎莉娅`
- `梦莎莉娅梦莎`
- `梦莎莉娅梦莎莉娅，是的没错，他醒了。`

则这些帧应归入同一个事件，而不是多条句子。

### 推荐规则
- 文字长度单调增加；
- 新文本以前一帧文本为前缀；
- 字框变化小；
- 时间间隔短。

满足上述条件时，执行“事件内合并”。

---

## 2.5 图像预处理模块

### 目标
把复杂的对话框图像转成 OCR 更容易识别的图。

### 必做预处理
- 放大（1.5x–3x）
- 锐化
- 去噪
- 自适应二值化
- 颜色通道分离
- 描边/阴影减弱

### 半透明背景处理
这里应重点借鉴 `Visual Novel OCR` 的思路：  
其核心不是简单截图，而是通过**镜像截取**与**色彩对比阈值**，把半透明背景上的文字转成更清晰的“深色字 / 浅底”图像。[Visual Novel OCR Guide](https://visual-novel-ocr.sourceforge.io/)

### 建议的预处理配置模式
为不同作品预设多种 profile：

- `plain_light_bg`
- `plain_dark_bg`
- `semi_transparent_hsv`
- `outline_heavy`
- `battle_caption`
- `small_font_dense`

每个 profile 对应不同的阈值和形态学处理参数。

---

## 2.6 OCR 模块

### 总体建议
不要只用一个 OCR 引擎，也不要让所有帧都走最重模型。  
推荐采用：

- **OCR1（快）**：用于变化检测、粗识别、低成本扫描
- **OCR2（准）**：用于稳定后定稿

这与 `GameSentenceMiner` 以及 `OwOCR` 的“两阶段”思路一致。[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/) [OwOCR GitHub](https://github.com/AuroraWright/owocr)

### 推荐实现
- OCR1：本地轻量 OCR（适合高频调用）
- OCR2：更高精度 OCR（可用 GPU）
- 对名字框和正文框使用不同参数，必要时甚至使用不同 OCR 引擎

### 候选融合
对每个事件保留多个 OCR 候选：
- 原始候选；
- 预处理版本候选；
- 多引擎候选；
- 多帧候选。

最终使用投票或规则融合定稿。

---

## 2.7 事件聚合与定稿模块

这是整个系统里**最重要**的模块之一。

### 输入
若干帧的名字框 OCR、正文 OCR、图像变化信息、时间戳。

### 输出
一条稳定的结构化对话事件。

### 核心职责

#### 1）去重复
同一句话停留很多帧时，只保留一次。

#### 2）残句合并
打字机效果的中间残句不输出，只保留最终完整句。

#### 3）候选排序
优先级建议：

1. 长度更完整；
2. 与前缀增长链一致；
3. OCR 置信度更高；
4. 跨帧一致性更强；
5. 与角色/上下文更合理。

#### 4）换行与标点修复
常见修复包括：
- 多余空格；
- OCR 把中文标点识别为英标；
- 行末错误断句；
- 重复字；
- 省略号形态统一。

### 建议的事件状态机

```text
Idle
  -> Detecting (发现文本)
  -> Growing (文本在增加，疑似打字机)
  -> Stable (文本稳定，等待定稿)
  -> Finalized (输出事件)
  -> Idle
```

---

## 2.8 说话人识别模块

### 优先级策略

#### 优先级 1：明确名字框
若当前事件名字框识别到稳定角色名，则直接采用。

#### 优先级 2：特殊标签
若正文区域显示：
- `旁白`
- `系统`
- `？？？`
- `选择`
  则按规则赋予标签。

#### 优先级 3：连续继承
若名字框缺失，但当前事件与上一事件属于同一对话界面、同一文本风格，且中间没有明显角色切换，则可继承上一说话人。

#### 优先级 4：未知保留
若仍不确定，则输出：
- `【未知】`
- 或保留空 speaker 字段，进入复核队列。

### 角色名词典
建议建立每部作品的角色名 alias 表：

```yaml
speaker_alias:
  梦莎莉娅: [梦莎莉娅, 梦莎, 莎莉娅]
  莉莉娅: [莉莉娅, 莉莉亚]
  系统: [系统, SYS, System]
```

这会显著提升说话人归一化能力。

### 借鉴来源
`RSTGameTranslation` 明确强调其有“game context awareness and character name detection”，这说明上下文和名称识别在游戏 OCR 中是很有价值的方向。[RSTGameTranslation GitHub](https://github.com/thanhkeke97/RSTGameTranslation)  
其官网也写明支持 visual novel chat window，并通过上下文信息提升识别/翻译准确性。[RSTGameTranslation 官网](https://thanhkeke97.github.io/RSTGameTranslation/)

---

## 2.9 ASR 辅助模块（不选，放弃这条路）

### 定位
ASR 只做三件事：

1. 给有配音的事件提供校对候选；
2. 在 OCR 置信度很低时提供补全文本；
3. 帮助判断台词切换边界。

### 不应该做的事
- 不要直接把整段音频转成最终台本；
- 不要把无配音台词交给 ASR；
- 不要在 BGM 很重的战斗场景里盲目信任 ASR。

### 建议流程
- 先做人声分离 / 降噪；
- 只在“对话事件时段”内调用 ASR；
- 将 ASR 结果与 OCR 结果对齐；
- 仅在 OCR 不稳时才参考 ASR。

---

## 2.10 VLM / 闭源多模态 API 兜底模块（闭源API调用作为兜底,初次使用需要配置API url和key，如果不配说明放弃兜底）

### 目标
解决以下难例：

- OCR 两个引擎都不稳定；
- 半透明或演出特效导致字符缺损；
- 名字框过小或颜色复杂；
- 系统字、战斗 HUD 字、CG 演出文字难以用普通 OCR 捕捉。

### 成本控制原则
只在以下条件满足时才触发：

- OCR 置信度低于阈值；
- 多帧候选相互冲突；
- 名字框缺失但判断有说话人；
- 事件被标为 `review_required`。

### 输入方式
不要把整段长视频交给 API。  
只发送与单条事件相关的 2–5 张关键帧，以及结构化提示词，例如：

```text
请只转写该游戏画面中对话框的最终完整文本，并分别输出：
speaker:
text:
不要翻译，不要解释，不要补写未显示内容。
```

### 推荐定位
- 本地 VLM：中等成本、适合大量难例
- 闭源 API：高成本，只处理最终顽固难例

---

## 2.11 脚本匹配模块（不用看这个，这个不需要，没有脚本）

如果对应作品能找到现成剧本、攻略文本或玩家整理文本，那么可引入“脚本匹配”层：

1. 用 OCR 得到近似句；
2. 在脚本库中做模糊匹配；
3. 匹配到高相似度句后，以脚本句替换 OCR 结果；
4. 说话人也可由脚本侧补全。

这条路线直接借鉴 `Game2Text` 的 “OCR-assisted game script matching”。[Game2Text GitHub](https://github.com/mathewthe2/Game2Text)

注意：这是**增强层**，不是主链路；因为很多视频未必有现成脚本。

---

## 2.12 人工复核模块

### 必要性
只要目标是“高质量台本”，就必须给低置信度结果留人工通道。

### 建议复核界面展示
- 事件编号；
- 起止时间；
- 原始关键帧；
- 名字框 ROI 图；
- 正文框 ROI 图；
- OCR 候选 1 / 2 / 3；
- ASR 候选；
- VLM 候选；
- 当前定稿；
- 标记理由（如“名字不稳”“疑似残句”“疑似系统字”）。

### 复核优先队列
优先人工处理：
1. 低置信度；
2. 名字缺失；
3. 多候选冲突；
4. 高价值剧情段；
5. 选项 / 分支文本。

---

## 数据结构与输出格式

## 1. 中间结构：JSONL

建议所有事件都先写入 JSONL，以支持追踪和复跑。

```json
{
  "video_id": "sample_ep01",
  "event_id": "sample_ep01_000245",
  "scene_type": "dialogue",
  "start_ms": 183240,
  "end_ms": 186960,
  "speaker_raw": "莉莉娅",
  "speaker_norm": "莉莉娅",
  "text_raw": "梦莎莉娅梦莎莉娅，是的没错，他醒了。",
  "text_norm": "梦莎莉娅梦莎莉娅，是的没错，他醒了。",
  "ocr_candidates": [
    {"engine": "ocr1", "text": "梦莎莉娅梦莎莉娅，是的没错，他醒了。", "conf": 0.88},
    {"engine": "ocr2", "text": "梦莎莉娅梦莎莉娅，是的没错，他醒了。", "conf": 0.95}
  ],
  "asr_text": null,
  "vlm_text": null,
  "final_confidence": 0.95,
  "review_required": false
}
```

## 2. 最终输出：Markdown / TXT

### Markdown
```markdown
# sample_ep01

莉莉娅：梦莎莉娅梦莎莉娅，是的没错，他醒了。  
梦莎莉娅：……  
【旁白】夜色渐深，风声渐起。  
【系统】获得道具：XXXX  
```

### TXT
```text
莉莉娅：梦莎莉娅梦莎莉娅，是的没错，他醒了。
梦莎莉娅：……
【旁白】夜色渐深，风声渐起。
【系统】获得道具：XXXX
```

### 可选：SRT
用于回看时间轴，不作为主交付。

---

## 质量评估与验收标准

**以下质量评估以及验收标准仅供参考，构造标准集和检查各种指标需要大量的人工，因此实际并不现实。真正现实的人工验收应该是读取处理后的结构化的对话文本，通过html页面可视化的方式供用户审阅，可以给每条记录进行打分与评价，选择好还是不好。**

## 1. 建议建立金标集

从每部作品抽取 10–30 分钟，人工标注：
- 对话起止时间；
- 说话人；
- 文本；
- 特殊事件类型（旁白/系统/选项/战斗字幕）。

## 2. 建议指标

### 文本层
- **字符错误率（CER）**
- **句子完整率**：是否抽到了完整句，而不是残句
- **重复率**：同一句多次输出的比例

### 结构层
- **说话人准确率**
- **事件切分准确率**
- **特殊标签准确率**（旁白/系统/选项）

### 系统层
- **每小时视频处理时长**
- **每小时视频 API 成本**
- **需人工复核比例**

## 3. 验收建议

### MVP 验收
- 标准对话 UI 场景下，文本完整率达到可读水平；
- 打字机重复显著减少；
- 能导出成基础台本。

### 高质量验收
- 核心剧情段文本可直接阅读；
- 大多数说话人可正确识别；
- API 成本可控；
- 人工只需处理少量难例。

---

## 成本性能与部署策略

## 1. 成本控制原则

### 本地优先
- 视频解码、本地 OCR、规则后处理全部本地执行；
- 本地 GPU 优先给高精度 OCR 或本地 VLM 使用。

### API 只用于难例
闭源多模态 API 的使用门槛建议设置为：
- 最终置信度 < 阈值；
- 事件被标记为重要剧情；
- 或人工复核成本高于 API 成本。

## 2. 性能优化原则

- 先粗扫、后精扫；
- 先场景筛选、后 OCR；
- 只处理 ROI，不处理整帧；
- 只在变化帧和稳定阶段做精识别；
- 对同一作品缓存配置与词典；
- 支持断点续跑。

## 3. 推荐部署形态

### 单机本地部署
适合当前阶段：
- 一台带 GPU 的 Windows / Linux 工作站；
- 本地存视频、本地跑 OCR、本地出 JSONL/TXT；
- 可选连接云端 API。

### 后续可扩展
如果后面视频量很大，可以升级为：
- 解码与场景切分：CPU 队列
- OCR/VLM：GPU 队列
- 复核界面：本地 Web 前端

---

## 风险清单与应对措施

| 风险             | 表现         | 应对                             |
| -------------- | ---------- | ------------------------------ |
| 打字机残句过多        | 台本出现大量半句   | 事件状态机 + 前缀增长合并                 |
| 半透明背景识别差       | 漏字、错字      | 借鉴 Visual Novel OCR 的阈值/颜色增强思路 |
| 战斗/CG 场景误检     | 非对白文本混入主台本 | 场景分类 + 特殊标签输出                  |
| 名字框不稳定         | 说话人错乱      | 角色词典 + 上下文继承 + 低置信度复核          |
| OCR 对某作品字体不适配  | 错字率高       | 每作 profile、双引擎、多预处理策略          |
| 闭源 API 成本失控    | 预算不可控      | 只对低置信度事件调用                     |
| 无码脚本匹配不可用      | 无法进一步提纯    | 将 script matching 作为增强项而非依赖项   |
| 长视频重复处理成本高     | 处理慢        | 变化帧筛选、断点续跑、缓存                  |
| 直接复用开源代码产生许可问题 | 商业或分发风险    | 先借鉴设计，直接集成代码前逐仓核查许可协议          |

---

## 实施路线图

## 第一阶段：2 周内完成 PoC

### 目标
验证：
- 对话段识别可行；
- ROI OCR 可行；
- 打字机合并可行；
- 基础台本输出可行。

### 工作项
1. 选 1 部作品样本视频（10–20 分钟）
2. 人工标定 ROI
3. 实现：
   - 视频解码
   - 对话段检测（规则版）
   - OCR1/OCR2
   - 事件聚合
   - TXT/Markdown 导出
4. 产出误差报告

## 第二阶段：3–5 周完成 MVP

### 目标
跑完整数小时视频，人工修一点就能用。

### 工作项
- 增加作品配置系统
- 增加角色词典
- 增加特殊事件标签
- 增加复核输出
- 支持断点续跑
- 输出 JSONL + Markdown

## 第三阶段：5–8 周完成高质量增强

### 工作项
- 半透明背景增强 profile
- 多 OCR 引擎融合
- ASR 辅助
- 本地 VLM / API 兜底
- 战斗/CG/系统场景分类
- 评测集与回归测试

## 第四阶段：后续迭代

### 工作项
- 多作品配置沉淀
- 脚本匹配增强
- 可视化复核前端
- 质量报表
- 批量任务管理

---

## 推荐结论

## 1. 最推荐的工程路线

### 主链路
**视频场景筛选 -> 固定 ROI OCR -> 文本变化检测 -> 多帧融合 -> 说话人结构化 -> 导出台本**

### 兜底链路
**低置信度事件 -> 闭源多模态 API**

---

## 2. 最值得直接借鉴的开源项目

### 必借鉴
1. **GameSentenceMiner**
   - 借“两阶段 OCR”与“文本稳定后再定稿”的思路。  
   - 这是解决打字机问题最关键的参考之一。  
     参考：[GSM OCR 文档](https://docs.gamesentenceminer.com/docs/features/ocr/)

2. **Visual Novel OCR**
   - 借“半透明背景处理”和“色彩对比阈值”思路。  
   - 这是提高对话框 OCR 质量的关键参考之一。  
     参考：[Visual Novel OCR Guide](https://visual-novel-ocr.sourceforge.io/)

3. **RSTGameTranslation**
   - 借“上下文感知、角色名识别、VN chat window 优化”的思路。  
   - 这是做台本结构化时最接近目标的参考之一。  
     参考：[RSTGameTranslation 官网](https://thanhkeke97.github.io/RSTGameTranslation/) / [GitHub](https://github.com/thanhkeke97/RSTGameTranslation)

4. **VideOCR + VideoSubFinder**
   - 借“长视频批处理”和“先找有字帧/清背景再 OCR”的工程模式。  
   - 这是把实时 OCR 思路改造成离线生产线的关键参考。  
     参考：[VideOCR GitHub](https://github.com/timminator/VideOCR) / [VideoSubFinder SourceForge](https://sourceforge.net/projects/videosubfinder/)

### 值得作为增强补充
5. **OwOCR**
   - 借其多输入、多输出和两阶段优化框架。  
     参考：[OwOCR GitHub](https://github.com/AuroraWright/owocr)

6. **visual-novel-game-ocr**
   - 借其“变化关键帧 + 快速离线 OCR 输出”的策略。  
     参考：[visual-novel-game-ocr GitHub](https://github.com/wanghaisheng/visual-novel-game-ocr)

7. **Game2Text**
   - 借脚本匹配增强思路。  
     参考：[Game2Text GitHub](https://github.com/mathewthe2/Game2Text)

---

## 3. 最终判断

如果你希望的是：

- **高质量**
- **可批处理长视频**
- **兼顾无配音文本**
- **成本可控**
- **后续能扩展到多作品**

那么不应寻找“现成一键工具”，而应建设一条：

> **基于社区成熟思路的自定义 OCR-first VN 视频台本抽取流水线**

从工程可行性、质量上限和成本控制三方面看，这是目前最稳妥的路线。

---

## 参考资料

1. Textractor（视频游戏 / VN 文本 Hook）  
   https://github.com/Artikash/Textractor

2. LunaTranslator（HOOK、OCR、模拟器支持）  
   https://docs.lunatranslator.org/en/  
   https://github.com/HIllya51/LunaTranslator

3. GameSentenceMiner OCR 文档（两阶段 OCR）  
   https://docs.gamesentenceminer.com/docs/features/ocr/

4. OwOCR（多输入、多输出、screen capture/OBS/websocket）  
   https://github.com/AuroraWright/owocr

5. Visual Novel OCR Guide（半透明背景、镜像截取、颜色阈值）  
   https://visual-novel-ocr.sourceforge.io/

6. RSTGameTranslation（上下文感知、角色名识别、chat window 优化）  
   https://thanhkeke97.github.io/RSTGameTranslation/  
   https://github.com/thanhkeke97/RSTGameTranslation

7. VideOCR（硬字幕视频 OCR）  
   https://github.com/timminator/VideOCR

8. VideoSubFinder（有字帧检测、清背景文字图）  
   https://sourceforge.net/projects/videosubfinder/

9. visual-novel-game-ocr（关键帧、RapidOCR、txt/SRT/docx）  
   https://github.com/wanghaisheng/visual-novel-game-ocr

10. video-text-extraction（面向 visual novel gameplay video）  
   https://github.com/girubato/video-text-extraction

11. GameDialogueOCR（自定义 ROI 的批量图像 OCR）  
    https://github.com/purpyviolet/GameDialogueOCR

12. Game2Text（OCR-assisted game script matching）  
    https://github.com/mathewthe2/Game2Text
