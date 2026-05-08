> **首先查看 `current_work.md` 了解当前工作进展和待办事项。**

# ACGN-character-skill 项目文档

## 什么是 Skill

本仓库是一个 Claude Code Skill。Skill 是 Claude Code 的扩展能力单元，以一个包含 `SKILL.md` 的仓库形式存在，安装到 Claude Code 后可以通过 `/` 命令触发。当用户在 Claude Code 中输入对应的斜杠命令时，Claude 会读取 SKILL.md 中定义的指令、工具使用规则和工作流程，按照其中的步骤自动执行任务。一个 Skill 本质上是对 Claude 的行为编程——它告诉 Claude 在被触发时该做什么、怎么做、用哪些工具、按什么顺序，相当于一套可复用的自动化工作流。

本项目的 SKILL.md 定义了一个"ACGN 虚构角色蒸馏器"。它的核心能力是：接收用户提供的角色相关原材料（游戏剧情视频、文档、文本等），通过 OCR 提取对话、LLM 分析人格和设定，最终生成一套结构化的角色扮演 prompt（story.md + persona.md + 合并后的 SKILL.md），存放在 `characters/{角色名}/` 目录下。用户通过 `/ACGN-character {角色名}` 触发时，Claude 读取该角色的设定并以角色身份进行对话。

## 项目概述

本项目是一个博士研究项目中的核心工具，目标是将 ACGN（Anime/Comic/Game/Novel）领域的虚构角色"蒸馏"为可对话的角色扮演 prompt。整个系统的工作流程是：从游戏剧情视频中通过 OCR 提取角色对话文本，再从这些对话中提取角色的故事设定（Story）和五层人格特征（Persona），最终生成一套能让 Claude 以角色本人的语气说话、以角色的方式思考、带着角色情感回应的角色扮演指令。项目架构参考了 colleague-skill 的二层蒸馏方法，将其"同事能力 + 人格"框架迁移到虚构角色领域，替换为"角色设定 + 人格"。当前的首个实例是崩坏3舰长线角色「月下」，训练数据来源于B站UP主MC神神希发布的崩坏3舰长线全剧情合集视频。

项目目前处于半成品状态。OCR 对话提取管线已完成统一架构重构（2026-04-28），采用自适应状态机替代固定阈值，ROI 使用手工标注（已放弃自动检测方案），支持 checkpoint/resume、多引擎 OCR 融合、后置前缀合并和可选 LLM 纠错。角色扮演 prompt 生成部分已有初步框架但效果还在调试。

## Skill 使用流程

本 Skill 通过 `/ACGN-character` 命令触发，入口定义在 `SKILL.md` 中。调用时有两种模式：如果传入的参数是一个已存在于 `characters/` 目录下的角色名（如 `/ACGN-character yuexia`），则读取该角色的 `characters/{slug}/SKILL.md` 并进入角色扮演对话模式；如果参数不匹配任何已有角色或者用户没有传参数，则进入创建器模式，开始生成新角色。

创建器模式分为五个步骤。Step 1 是基础信息录入，只问三个问题：角色名/代号（必填）、基本信息（作品名、身份、种族、外貌等一句话描述）、性格画像（性格标签、角色类型、印象等一句话描述）。除角色名外均可跳过。Step 2 是原材料导入，支持三种方式混用：方式 A 是视频对话提取，使用 OCR 管线从游戏/VN 视频中自动提取对话，这是本项目的核心能力；方式 B 是上传文本文件（PDF、图片、TXT、MD、EPUB 等），通过 Read 工具或 epub_reader 转换后读取；方式 C 是直接粘贴文本。

方式 A 的视频对话提取流程又分为四个子步骤：A0 环境预检（检查 paddleocr 是否可用，缺失则安装）；A1 布局一致性检测（从每个视频抽取样本帧，判断所有视频的对话框 UI 布局是否一致，不一致则分组处理）；A2 ROI 配置（检查或创建 `tools/configs/*.yaml` 配置文件，并通过裁切样本帧验证 ROI 精度，确保名字框和对话框的坐标准确无误）；A3 运行提取（调用 `python3 -m tools.dialogue_extractor` 执行 OCR 提取，支持单视频和批量处理）。

Step 3 是分析原材料，沿两条线并行进行：线路 A 参考 `prompts/story_analyzer.md` 提取角色设定（世界观、经历、关系、能力、关键事件）；线路 B 参考 `prompts/persona_analyzer.md` 提取人格特征（表达风格、情感模式、人际行为、口癖）。Step 4 是生成并预览，参考 `prompts/story_builder.md` 和 `prompts/persona_builder.md` 分别生成 story.md 和 persona.md（五层结构），向用户展示摘要并确认。Step 5 是写入文件，在 `characters/{slug}/` 目录下创建 story.md、persona.md、meta.json 和最终的 SKILL.md，SKILL.md 将 story 和 persona 合并为一个完整的角色扮演指令。

除创建模式外，Skill 还支持两种进化模式。追加文件模式在用户提供新材料时触发，读取新内容后与现有设定合并，先备份当前版本再更新。对话纠正模式在用户说"不对"/"她不会这样"时触发，识别纠正内容属于 Story 还是 Persona，追加 correction 记录并重新生成 SKILL.md。管理命令包括 `/list-characters` 列出所有角色、`/character-rollback` 回滚版本、`/delete-character` 删除角色。

## 项目结构

项目根目录下的核心文件包括：`SKILL.md` 是角色 Skill 创建器的入口定义；`README.md` 包含项目说明和安装方式；`requirements.txt` 定义了 Python 依赖；`analyze_truncation.py` 是分析截断率的脚本；`current_work.md` 记录当前工作进展。

`tools/` 目录是 OCR 对话提取管线的代码所在。`unified_pipeline.py` 是统一 CLI 入口，串联全流程。核心模块：`frame_extractor.py`（Stage 1，OCR 驱动的事件检测，checkpoint/resume，OCRFusion，说话人识别）、`batch_ocr.py`（Stage 2，批量 OCR）、`event_detector.py`（自适应状态机，增长率追踪+MAD skip+Levenshtein 停时判据）、`auto_roi.py`（利用 PaddleOCR dt_polys 聚类自动检测 ROI）、`post_merge.py`（后置前缀合并+战斗文字过滤）、`llm_corrector.py`（LLM OCR 纠错，可选，需 DEEPSEEK_API_KEY）、`ocr_postprocess.py`（正则纠错规则）、`output_schema.py`（统一输出格式 DialogueEventOutput）、`metrics.py`（性能指标追踪）。辅助模块：`ocr_engines.py`（OCR 引擎工厂，PaddleOCR/EasyOCR/RapidOCR）、`ocr_fusion.py`（多引擎融合策略）、`preprocessing.py`（图像预处理 profile）、`text_cleaning.py`（OCR 文本清洗）、`speaker_extractor.py`（说话人识别和别名归一化）、`output_formatter.py`（JSONLWriter）、`text_output.py`（JSONL 转纯文本）、`video_processor.py`（PyAV 帧提取）、`work_config.py`（WorkConfig 配置系统）、`review_ui.py`（HTML 复核页面）、`roi_calibrator.py`（交互式 ROI 校准）。并行处理工具：`parallel_extract.py`、`parallel_batch_ocr.py`、`parallel_ocr.py`。流程编排：`process_all_videos.py`、`full_ocr_pipeline.py`。旧版 `dialogue_extractor.py` 和 `video_transcriber.py` 已归档至 `tools/_archived/`。

`tools/configs/` 目录存放每部作品的 ROI 配置文件。当前有 `yuexia.yaml`（通用月下配置）和 `yuexia_ep01_roi.yaml`（第一节专用 ROI 配置），两者的 ROI 坐标实际相同，只是命名和说明不同。

`characters/` 目录存放生成的角色 Skill 产物，其中 `characters/yuexia/` 包含月下角色的 SKILL.md、story.md 和 persona.md。`prompts/` 目录存放 Prompt 模板，用于角色设定提取和人格分析。`training data/` 目录存放原始视频文件和 OCR 提取产物（gitignored），视频文件不纳入版本控制。`benchmark/` 目录包含评估数据与脚本（同样 gitignored）。`live2d/` 目录存放月下的 Live2D 模型文件。

## 核心 Pipeline

管线已统一为单一主线，全流程为：AutoROICalibrator（可选，自动检测 ROI）→ FrameExtractor（Stage 1，checkpoint/resume，自适应状态机，说话人识别，输出层级目录 `event_XXXXXX/{frame.png, dialog.png, name.png}`）→ BatchOCRProcessor（Stage 2，PaddleOCR+EasyOCR fallback，自动检测层级/扁平目录结构）→ PostMergeProcessor（合并打字机前缀碎片+过滤战斗文字）→ TextCorrector（正则规则+可选 LLM 双层纠错）→ JSONL+纯文本输出。统一 CLI 入口为 `python -m tools.unified_pipeline`，批量处理全部视频使用 `python -m tools.process_all_videos`。

Stage 1 的 FrameExtractor 使用 OcrFusion（从 WorkConfig 读取 ocr_engine 和 fallback_engine）替代硬编码 PaddleOCR，支持 checkpoint/resume（每完成一个事件写 checkpoint，中断后可恢复），事件最终确定后自动提取说话人（空名字框继承上一已知说话人），过滤战斗/HUD 文字。Stage 2 的 BatchOCRProcessor 自动检测输入目录结构（扁平 `dialog_crops/` 或层级 `event_X/`），使用 PaddleOCR 批量推理，支持多 GPU 并行。

## 状态机设计

`EventDetector` 是管线的核心组件，实现了一个五状态的有限状态机，状态转移路径为 IDLE → DETECTED → GROWING → STABLE → FINALIZED → IDLE。整个设计围绕正确处理视觉小说中常见的"打字机效果"（文字逐字逐句显现）展开。

IDLE 状态表示当前没有活跃的对话事件。当 OCR 识别到的文本长度达到 `min_text_length`（默认 2）时，状态机创建一个新的 DialogueEvent 对象并进入 DETECTED 状态。在 IDLE 状态下还有一个防重复机制：如果新识别的文本与刚刚最终确定的上一个事件文本高度相似（相似度超过 `similarity_threshold`），则不会创建新事件。

进入活跃事件后，每帧的处理逻辑分三个分支。第一，如果 OCR 返回空文本（长度小于 `min_text_length`），`empty_frame_count` 递增，达到 `empty_frames_threshold`（默认 2）时事件被最终确定。第二，如果检测到文本替换（`_is_text_replacement` 返回 True），当前事件被最终确定，同时以新文本开始一个新事件——这处理的是一句对话结束、下一句对话直接出现且中间没有空帧的情况。第三，如果既不是空帧也不是替换，则进行增长检测和稳定性计数。

`_is_text_growing` 方法判断新文本是否是之前文本的"增长"。它采用两种策略：一是前缀重叠检查，向前回看最多 5 帧，对每一帧的旧文本，取新文本的等长前缀与之比较，如果 SequenceMatcher 比率达到阈值（`max(0.5, similarity_threshold * 0.8)`），则判定为增长；二是长度趋势检查，如果最近 3 帧的文本长度都大于 0 且新文本比它们都长，也判定为增长。当检测到增长时，事件进入 GROWING 状态，`stable_frames` 计数器归零，且 `_was_growing` 标记设为 True。

当文本不再增长时（既不是空帧，也不是替换，也不是增长），`stable_frames` 计数器递增。稳定性阈值取决于事件是否经历过 GROWING 状态：如果 `_was_growing` 为 True，使用 `post_growth_stable_threshold`（默认 10，即 2fps 下 5 秒）；否则使用 `stable_frames_threshold`（默认 5，即 2fps 下 2.5 秒）。这种区分的原因是打字机效果中文字可能会出现短暂停顿然后继续增长，如果使用较短的稳定阈值，就会在打字机暂停时过早地最终确定事件，导致截断。

`_is_text_replacement` 方法是一个关键的判断逻辑，它决定新文本是"完全不同的内容"还是"对当前对话的继续"。这个方法首先检查新文本是否包含旧文本作为子串（如果是，那显然是增长而非替换），然后检查新文本是否与旧文本共享超过 50% 的前缀字符匹配（如果是，也判定为增长），再向前回看最多 5 帧检查新文本是否包含任何历史文本作为子串。只有在这些检查都通过后，才用 SequenceMatcher 计算相似度，相似度低于 `similarity_threshold` 时才判定为替换。这个方法经过专门修复来处理打字机效果中快速增长的情况——例如从"灼热的空"到"灼热的空气让肺部最后一丝生息也变得虚无"，虽然 SequenceMatcher 比率只有 0.21（会被当作替换），但子串检查和前缀匹配能正确识别这是增长。

事件被最终确定时，`_merge_text_candidates` 方法从整个 `text_history` 中选择最终文本。策略是优先选择最长的文本（过滤掉长度不到最长文本 50% 的短文本），在最长文本中选择出现频率最高的，置信度取频率加权平均。

当前的参数配置已从最初的 `stable_frames: 3, similarity: 0.6, post_growth_stable: 5` 调整为 `stable_frames: 5, similarity: 0.5, post_growth_stable: 10`，目的是给打字机效果更多时间完成，减少截断。

**自适应状态机改进**（2026-04-28）：EventDetector 新增三项机制替代固定阈值策略。(a) growth_confidence 增长率追踪——用 SMA(5) 计算 delta_chars_per_frame，通过 sigmoid 映射为连续置信度 [0,1]，自适应阈值 = max(5, int(growth_confidence * 15))，速度快就少等、慢就多等。(b) 像素差分预触发（MAD skip）——活跃事件期间计算当前帧与上一帧 ROI 的 Mean Absolute Difference，MAD < 2% 时跳过 OCR（无变化等同空帧），通过 `enable_mad_skip=True` 启用，预期减少 30-40% OCR 调用。(c) 累积 Levenshtein 停时判据——滑动窗口（size=3）内累加编辑距离 <=2 且 adaptive_threshold 帧数已过才最终确定，处理 OCR 抖动导致的微小字符变化。

## OCR 引擎

项目使用 PaddleOCR 作为主引擎，通过 `ocr_engines.py` 中的工厂函数 `create_ocr_func` 创建。PaddleOCR 初始化时会自动检测 GPU 可用性（通过 `paddle.is_compiled_with_cuda()` 和 `paddle.device.cuda.device_count()`），有 GPU 则使用 `gpu:0`，否则回退到 CPU。引擎配置了一系列环境变量来抑制日志和避免库冲突（如 `KMP_DUPLICATE_LIB_OK`、`GLOG_minloglevel` 等），OCR 参数设置为 `text_det_thresh=0.2`、`text_det_box_thresh=0.35`、`text_det_unclip_ratio=2.0`、`text_det_limit_side_len=960`，语言设置为中文。

OCR 结果经过一套后过滤逻辑：过滤掉置信度低于 0.4 的检测框，过滤掉面积小于 150 像素的小框，过滤掉中心点过于靠近图像边缘（8 像素以内）的框。多个检测框的文本用空格拼接，置信度按面积加权平均。

图像预处理通过 `preprocessing.py` 中的 `PreprocessProfile` 数据类定义，管线按固定顺序应用：upscale → CLAHE → contrast → sharpen → denoise → binarize → invert。对话框使用 `game_dialogue` profile（2x 放大、CLAHE clip=2.5 tile=8、降噪），名字框使用 `game_namebox` profile（2x 放大、CLAHE clip=3.0 tile=8、不降噪）。CLAHE（Contrast Limited Adaptive Histogram Equalization）是关键的预处理步骤，它在 LAB 色彩空间的 L 通道上做自适应直方图均衡化，能有效增强半透明对话框背景上的文字对比度。降噪使用 OpenCV 的 `fastNlMeansDenoisingColored`（当 CLAHE 启用时）或 PIL 的 MedianFilter。

文本清洗由 `text_cleaning.py` 完成，包括去除方框绘制字符、去除尾部和头部的 ASCII 噪声（仅在主体文本包含 CJK 字符时）、折叠重复标点。说话人名字清洗额外去除尾部冒号和尾部标点。

## 配置系统

配置系统由 `work_config.py` 实现，核心是 `WorkConfig` 数据类和 `load_work_config` 加载函数。每个作品需要一份 YAML 配置文件，放在 `tools/configs/` 目录下。配置文件必须包含三个必填字段：`work_id`（作品标识符）、`dialog_box`（对话框 ROI 归一化坐标）和 `name_box`（名字框 ROI 归一化坐标）。ROI 坐标以 `{x, y, w, h}` 格式定义，所有值在 0 到 1 之间，表示相对于视频帧宽高的归一化位置和尺寸。加载时会验证 ROI 的合法性，包括各值的范围、面积是否为正、坐标加尺寸是否超出 1.0 等。

可选配置项包括：预处理 profile 名称（`dialog_preprocess` 和 `name_preprocess`）、OCR 引擎选择（`ocr_engine`，默认 paddleocr）、备用引擎（`fallback_engine`）及其置信度阈值（`fallback_threshold`）、说话人别名映射（`speaker_aliases`，canonical name 到 alias 列表的映射）、特殊说话人映射（`special_speakers`，如旁白、系统、???）、处理帧率（`target_fps`，默认 2.0）、复核阈值（`review_threshold`，默认 0.7）、VLM 兜底配置（`vlm_enabled`、`vlm_threshold`、`vlm_max_calls_per_video`、`vlm_model`），以及事件检测器的阈值覆盖（`stable_frames_threshold`、`post_growth_stable_threshold`、`similarity_threshold`、`min_text_length`、`empty_frames_threshold`）。当配置文件中指定了这些阈值覆盖时，`EventDetector` 会使用配置值而非代码中的默认值。

当前月下作品的 ROI 配置（`yuexia.yaml`）为：对话框位于 `x=0.14, y=0.815, w=0.7, h=0.125`，名字框位于 `x=0.14, y=0.73, w=0.11, h=0.075`。说话人别名包括舰长、姬子（别名姬子老师）、琪亚娜（别名琪亚）、芽衣、布洛妮娅（别名布洛妮）、德丽莎、符华、旁白和系统。

## Pipeline 架构

Stage 1（`frame_extractor.py`）的 `FrameExtractor` 类使用 OcrFusion（从 WorkConfig 读取 ocr_engine 和 fallback_engine 创建，支持 PaddleOCR+EasyOCR fallback）和 `game_dialogue` 预处理 profile，组合为带预处理的 OCR 函数传给 `EventDetector`。处理视频时用 PyAV 解码，按 `target_fps` 采样，每帧裁剪 ROI 区域送入自适应状态机。事件最终确定后保存至层级目录 `event_XXXXXX/`（含 frame.png、dialog.png、name.png），同时提取说话人识别结果。所有事件元数据写入 `events_metadata.json`。支持 checkpoint/resume，中断后可恢复。

Stage 2（`batch_ocr.py`）的 `BatchOCRProcessor` 自动检测输入目录结构（扁平 `dialog_crops/` 或层级 `event_X/`），对每张图像应用对应预处理 profile 后送入 PaddleOCR 批量推理，结果经 `clean_ocr_text` 和 `clean_speaker_name` 清洗后以 JSONL 格式输出。

`post_merge.py` 的 `PostMergeProcessor` 在 OCR 完成后对 JSONL 做后置处理：合并相邻的打字机前缀碎片（同一说话人+时间间隔<5s+前缀相似度>=0.65），过滤战斗/HUD 文字事件。

`auto_roi.py` 的 `AutoROICalibrator` 采样视频前 2 分钟约 40 帧，运行 PaddleOCR detection-only，收集 dt_polys 检测框的 y 坐标，DBSCAN 聚类找到对话框和名字框区域，输出标准 WorkConfig YAML。

## 环境要求

项目需要 Python 3.11+ 运行环境，使用 conda 环境 `paddleocr`（已预装全部依赖，`conda activate paddleocr` 激活）。核心依赖包括 Pillow、numpy、PyAV（视频帧提取）、PyYAML、opencv-python（CLAHE 预处理和 ROI 校准）。OCR 引擎使用 PaddlePaddle GPU 版 + PaddleOCR。可选依赖包括 EasyOCR 和 RapidOCR 作为备用引擎。LLM 纠错可选，需设置 `DEEPSEEK_API_KEY` 环境变量。GPU 选择通过 `CUDA_VISIBLE_DEVICES` 环境变量指定，程序内部统一使用设备 0（即 CUDA_VISIBLE_DEVICES 中的第一张可见卡）。

## 运行命令

统一端到端命令：`CUDA_VISIBLE_DEVICES=0 python -m tools.unified_pipeline VIDEO --config CONFIG.yaml --output-dir OUT --llm-correct`。会自动依次完成帧提取→OCR→合并→纠错→文本输出。

批量处理全部 7 个视频：`CUDA_VISIBLE_DEVICES=0 python -m tools.process_all_videos`（使用 `/data2/training_data/` 下的视频文件和 per-episode YAML 配置）。

手工 ROI 标注：`python -m tools.roi_annotator --port 11451`，浏览器打开 `http://localhost:11451`，左侧切换视频，画蓝色对话框和红色名字框，Ctrl+S 保存。

后置合并和过滤：`python -m tools.post_merge ocr_results.jsonl`。

所有命令在项目根目录运行，需先 `conda activate paddleocr`。

## 已知问题

打字机截断已通过自适应状态机（增长率追踪+MAD skip+Levenshtein 停时判据）和后置前缀合并解决，截断率从 23.3% 降至 <0.2%。

说话人 OCR 后缀噪声（EKR、福、享、Ta] 等）已通过 LLM 批量去噪处理（deepseek-v4-flash）。ROI 配置使用手工标注（`roi_annotator.py`），已放弃自动检测方案（`auto_roi.py` 保留但不推荐使用）。
