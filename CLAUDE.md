> **首先查看 `current_work.md` 了解当前工作进展和待办事项。**

# ACGN-character-skill 项目文档

## 项目概述

本项目是一个博士研究项目中的核心工具，目标是将 ACGN（Anime/Comic/Game/Novel）领域的虚构角色"蒸馏"为可对话的 AI Skill。整个系统的工作流程是：从游戏剧情视频中通过 OCR 提取角色对话文本，再从这些对话中提取角色的故事设定（Story）和五层人格特征（Persona），最终生成一个能以角色本人的语气说话、以角色的方式思考、带着角色情感回应的角色扮演 Skill。项目架构参考了 colleague-skill 的二层蒸馏方法，将其"同事能力 + 人格"框架迁移到虚构角色领域，替换为"角色设定 + 人格"。当前的首个实例是崩坏3舰长线角色「月下」，训练数据来源于B站UP主MC神神希发布的崩坏3舰长线全剧情合集视频。

项目目前处于半成品状态，OCR 对话提取管线已基本成型并在持续优化中，角色 Skill 生成部分已有初步框架但效果还在调试。当前工作的重心在 OCR 管线的状态机调优上，目标是消除打字机效果导致的对话截断问题。

## 项目结构

项目根目录下的核心文件包括：`SKILL.md` 是角色 Skill 创建器的入口定义，它描述了整个角色创建流程的触发条件、工具使用规则和五步主流程；`README.md` 包含项目说明、安装方式和效果示例；`requirements.txt` 定义了 Python 依赖；`analyze_truncation.py` 是用于分析截断率的脚本；`batch_extract_frames.sh` 和 `batch_ocr_all.sh` 是批量处理的 shell 脚本。

`tools/` 目录是整个 OCR 对话提取管线的代码所在，包含以下关键模块：`dialogue_extractor.py` 是旧版一体化提取管线的主入口，它将视频处理、OCR、事件检测、说话人识别和输出格式化整合在一起，支持断点续跑；`event_detector.py` 是对话事件检测的状态机核心；`frame_extractor.py` 是新版两阶段管线的第一阶段，负责 OCR 驱动的事件检测和关键帧保存；`batch_ocr.py` 是第二阶段，负责对保存的裁剪图像做离线 OCR；`ocr_engines.py` 是 OCR 引擎工厂，支持 PaddleOCR、EasyOCR 和 RapidOCR 三种引擎；`ocr_fusion.py` 实现多引擎 OCR 融合策略；`preprocessing.py` 定义了多种图像预处理 profile；`text_cleaning.py` 提供 OCR 后处理文本清洗功能；`speaker_extractor.py` 负责说话人识别和别名归一化；`output_formatter.py` 负责 JSONL 结构化输出；`text_output.py` 将 JSONL 转换为纯文本台本；`video_processor.py` 处理视频帧提取与 ROI 裁剪；`work_config.py` 是配置系统核心，加载和验证 YAML 配置文件；`review_ui.py` 生成人工复核的 HTML 页面；`roi_calibrator.py` 提供交互式 ROI 校准工具。

`tools/configs/` 目录存放每部作品的 ROI 配置文件。当前有 `yuexia.yaml`（通用月下配置）和 `yuexia_ep01_roi.yaml`（第一节专用 ROI 配置），两者的 ROI 坐标实际相同，只是命名和说明不同。

`characters/` 目录存放生成的角色 Skill 产物，其中 `characters/yuexia/` 包含月下角色的 SKILL.md、story.md 和 persona.md。`prompts/` 目录存放 Prompt 模板，用于角色设定提取和人格分析。`training data/` 目录存放原始视频文件和 OCR 提取产物（gitignored），视频文件不纳入版本控制。`benchmark/` 目录包含评估数据与脚本（同样 gitignored）。`live2d/` 目录存放月下的 Live2D 模型文件。

## 核心 Pipeline

对话提取管线的核心思路是围绕"对话事件"而非单帧识别来工作。视频逐帧（按目标 FPS 采样，默认 2fps）处理，每帧先根据 ROI 配置裁剪出对话框和名字框区域，对话框裁剪送入状态机进行事件检测，当一个完整的对话事件被检测并最终确定后，再对名字框做 OCR 识别说话人，最后输出为结构化的 JSONL 格式。

旧版管线（`dialogue_extractor.py`）将所有步骤整合在一次视频遍历中完成，支持断点续跑和 VLM 兜底。新版管线将流程拆分为两个独立阶段：Stage 1 由 `frame_extractor.py` 完成，它在遍历视频时使用 OCR 驱动的状态机检测事件，每检测到一个事件就保存完整帧、对话框裁剪和名字框裁剪为 PNG 文件，同时生成 `events_metadata.json` 记录事件的时间戳信息；Stage 2 由 `batch_ocr.py` 完成，它读取 Stage 1 保存的裁剪图像，对每张图像独立做预处理和 OCR，最终输出 JSONL 结果。这种拆分的好处是事件检测和最终 OCR 可以独立调优，Stage 1 的结果可以被反复重跑 Stage 2 而不需要重新遍历视频。

## 状态机设计

`EventDetector` 是管线的核心组件，实现了一个五状态的有限状态机，状态转移路径为 IDLE → DETECTED → GROWING → STABLE → FINALIZED → IDLE。整个设计围绕正确处理视觉小说中常见的"打字机效果"（文字逐字逐句显现）展开。

IDLE 状态表示当前没有活跃的对话事件。当 OCR 识别到的文本长度达到 `min_text_length`（默认 2）时，状态机创建一个新的 DialogueEvent 对象并进入 DETECTED 状态。在 IDLE 状态下还有一个防重复机制：如果新识别的文本与刚刚最终确定的上一个事件文本高度相似（相似度超过 `similarity_threshold`），则不会创建新事件。

进入活跃事件后，每帧的处理逻辑分三个分支。第一，如果 OCR 返回空文本（长度小于 `min_text_length`），`empty_frame_count` 递增，达到 `empty_frames_threshold`（默认 2）时事件被最终确定。第二，如果检测到文本替换（`_is_text_replacement` 返回 True），当前事件被最终确定，同时以新文本开始一个新事件——这处理的是一句对话结束、下一句对话直接出现且中间没有空帧的情况。第三，如果既不是空帧也不是替换，则进行增长检测和稳定性计数。

`_is_text_growing` 方法判断新文本是否是之前文本的"增长"。它采用两种策略：一是前缀重叠检查，向前回看最多 5 帧，对每一帧的旧文本，取新文本的等长前缀与之比较，如果 SequenceMatcher 比率达到阈值（`max(0.5, similarity_threshold * 0.8)`），则判定为增长；二是长度趋势检查，如果最近 3 帧的文本长度都大于 0 且新文本比它们都长，也判定为增长。当检测到增长时，事件进入 GROWING 状态，`stable_frames` 计数器归零，且 `_was_growing` 标记设为 True。

当文本不再增长时（既不是空帧，也不是替换，也不是增长），`stable_frames` 计数器递增。稳定性阈值取决于事件是否经历过 GROWING 状态：如果 `_was_growing` 为 True，使用 `post_growth_stable_threshold`（默认 10，即 2fps 下 5 秒）；否则使用 `stable_frames_threshold`（默认 5，即 2fps 下 2.5 秒）。这种区分的原因是打字机效果中文字可能会出现短暂停顿然后继续增长，如果使用较短的稳定阈值，就会在打字机暂停时过早地最终确定事件，导致截断。

`_is_text_replacement` 方法是一个关键的判断逻辑，它决定新文本是"完全不同的内容"还是"对当前对话的继续"。这个方法首先检查新文本是否包含旧文本作为子串（如果是，那显然是增长而非替换），然后检查新文本是否与旧文本共享超过 50% 的前缀字符匹配（如果是，也判定为增长），再向前回看最多 5 帧检查新文本是否包含任何历史文本作为子串。只有在这些检查都通过后，才用 SequenceMatcher 计算相似度，相似度低于 `similarity_threshold` 时才判定为替换。这个方法经过专门修复来处理打字机效果中快速增长的情况——例如从"灼热的空"到"灼热的空气让肺部最后一丝生息也变得虚无"，虽然 SequenceMatcher 比率只有 0.21（会被当作替换），但子串检查和前缀匹配能正确识别这是增长。

事件被最终确定时，`_merge_text_candidates` 方法从整个 `text_history` 中选择最终文本。策略是优先选择最长的文本（过滤掉长度不到最长文本 50% 的短文本），在最长文本中选择出现频率最高的，置信度取频率加权平均。

当前的参数配置已从最初的 `stable_frames: 3, similarity: 0.6, post_growth_stable: 5` 调整为 `stable_frames: 5, similarity: 0.5, post_growth_stable: 10`，目的是给打字机效果更多时间完成，减少截断。

## OCR 引擎

项目使用 PaddleOCR 作为主引擎，通过 `ocr_engines.py` 中的工厂函数 `create_ocr_func` 创建。PaddleOCR 初始化时会自动检测 GPU 可用性（通过 `paddle.is_compiled_with_cuda()` 和 `paddle.device.cuda.device_count()`），有 GPU 则使用 `gpu:0`，否则回退到 CPU。引擎配置了一系列环境变量来抑制日志和避免库冲突（如 `KMP_DUPLICATE_LIB_OK`、`GLOG_minloglevel` 等），OCR 参数设置为 `text_det_thresh=0.2`、`text_det_box_thresh=0.35`、`text_det_unclip_ratio=2.0`、`text_det_limit_side_len=960`，语言设置为中文。

OCR 结果经过一套后过滤逻辑：过滤掉置信度低于 0.4 的检测框，过滤掉面积小于 150 像素的小框，过滤掉中心点过于靠近图像边缘（8 像素以内）的框。多个检测框的文本用空格拼接，置信度按面积加权平均。

图像预处理通过 `preprocessing.py` 中的 `PreprocessProfile` 数据类定义，管线按固定顺序应用：upscale → CLAHE → contrast → sharpen → denoise → binarize → invert。对话框使用 `game_dialogue` profile（2x 放大、CLAHE clip=2.5 tile=8、降噪），名字框使用 `game_namebox` profile（2x 放大、CLAHE clip=3.0 tile=8、不降噪）。CLAHE（Contrast Limited Adaptive Histogram Equalization）是关键的预处理步骤，它在 LAB 色彩空间的 L 通道上做自适应直方图均衡化，能有效增强半透明对话框背景上的文字对比度。降噪使用 OpenCV 的 `fastNlMeansDenoisingColored`（当 CLAHE 启用时）或 PIL 的 MedianFilter。

文本清洗由 `text_cleaning.py` 完成，包括去除方框绘制字符、去除尾部和头部的 ASCII 噪声（仅在主体文本包含 CJK 字符时）、折叠重复标点。说话人名字清洗额外去除尾部冒号和尾部标点。

## 配置系统

配置系统由 `work_config.py` 实现，核心是 `WorkConfig` 数据类和 `load_work_config` 加载函数。每个作品需要一份 YAML 配置文件，放在 `tools/configs/` 目录下。配置文件必须包含三个必填字段：`work_id`（作品标识符）、`dialog_box`（对话框 ROI 归一化坐标）和 `name_box`（名字框 ROI 归一化坐标）。ROI 坐标以 `{x, y, w, h}` 格式定义，所有值在 0 到 1 之间，表示相对于视频帧宽高的归一化位置和尺寸。加载时会验证 ROI 的合法性，包括各值的范围、面积是否为正、坐标加尺寸是否超出 1.0 等。

可选配置项包括：预处理 profile 名称（`dialog_preprocess` 和 `name_preprocess`）、OCR 引擎选择（`ocr_engine`，默认 paddleocr）、备用引擎（`fallback_engine`）及其置信度阈值（`fallback_threshold`）、说话人别名映射（`speaker_aliases`，canonical name 到 alias 列表的映射）、特殊说话人映射（`special_speakers`，如旁白、系统、???）、处理帧率（`target_fps`，默认 2.0）、复核阈值（`review_threshold`，默认 0.7）、VLM 兜底配置（`vlm_enabled`、`vlm_threshold`、`vlm_max_calls_per_video`、`vlm_model`），以及事件检测器的阈值覆盖（`stable_frames_threshold`、`post_growth_stable_threshold`、`similarity_threshold`、`min_text_length`、`empty_frames_threshold`）。当配置文件中指定了这些阈值覆盖时，`EventDetector` 会使用配置值而非代码中的默认值。

当前月下作品的 ROI 配置（`yuexia.yaml`）为：对话框位于 `x=0.14, y=0.815, w=0.7, h=0.125`，名字框位于 `x=0.14, y=0.73, w=0.11, h=0.075`。说话人别名包括舰长、姬子（别名姬子老师）、琪亚娜（别名琪亚）、芽衣、布洛妮娅（别名布洛妮）、德丽莎、符华、旁白和系统。

## 两阶段 Pipeline

新版管线将对话提取拆分为两个独立阶段运行，相比旧版一体化管线的优势在于事件检测和最终 OCR 可以独立调优和重跑。

Stage 1（`frame_extractor.py`）的 `FrameExtractor` 类在初始化时创建 PaddleOCR 引擎和 `game_dialogue` 预处理 profile，组合为一个带预处理的 OCR 函数传给 `EventDetector`。处理视频时，它用 PyAV 解码视频流，按 `target_fps` 计算帧间隔进行采样，每帧裁剪出对话框和名字框的 ROI 区域，将对话框裁剪送入状态机处理。当状态机返回一个最终确定的事件时，FrameExtractor 将当前的完整帧、对话框裁剪和名字框裁剪分别保存到 `frames/`、`dialog_crops/` 和 `name_crops/` 子目录下，文件名使用事件 ID。视频处理结束后还会调用 `detector.flush()` 处理可能残留的未完成事件。所有事件的元数据（event_id、start_timestamp、end_timestamp）写入 `events_metadata.json`。

Stage 2（`batch_ocr.py`）的 `BatchOCRProcessor` 类读取 Stage 1 输出目录中的所有 `event_*_dialog.png` 和对应的 `event_*_name.png` 文件。对每个事件，它分别用 `game_dialogue` 和 `game_namebox` 预处理 profile 处理对话框和名字框图像，然后送入 PaddleOCR 识别，对识别结果分别调用 `clean_ocr_text` 和 `clean_speaker_name` 做文本清洗。最终结果以 JSONL 格式输出，每行包含 event_id、text、text_confidence、speaker 和 speaker_confidence。

## 环境要求

项目需要 Python 3.11+ 运行环境，推荐使用 conda 创建独立环境。核心依赖包括 Pillow（>=10.0）、numpy（>=1.24）、PyAV（>=12.0，用于视频帧提取）、PyYAML（>=6.0）、opencv-python（>=4.8，用于 CLAHE 预处理和 ROI 校准）。OCR 引擎需要安装 PaddlePaddle 和 PaddleOCR：PaddleOCR 要求 `paddleocr>=3.4`，PaddlePaddle GPU 版本需要根据 CUDA 版本选择安装（CUDA 13.0 或 CUDA 12.6），CPU 版本安装 `paddlepaddle>=2.6`。可选依赖包括 EasyOCR 和 RapidOCR 作为备用引擎，以及 anthropic SDK 用于 VLM 兜底。

使用 GPU 加速时 PaddleOCR 的处理速度显著快于 CPU。以 OCR 驱动的 Stage 1 为例，处理一个视频（约 1 小时长度）在 GPU 下大约需要 1 小时，而基于像素的检测方式（旧方案，不做 OCR）大约只需要 5 分钟，但后者无法正确处理打字机效果。

## 运行命令

Stage 1 帧提取的运行命令为：`python -m tools.frame_extractor "视频路径" tools/configs/yuexia.yaml --output-dir "输出目录" --fps 2.0`。这会在输出目录下创建 `frames/`、`dialog_crops/`、`name_crops/` 三个子目录和 `events_metadata.json`。批量处理可使用 `batch_extract_frames.sh` 脚本。

Stage 2 批量 OCR 的运行命令为：`python -m tools.batch_ocr "Stage1输出目录" --output ocr_results.jsonl`。这会读取 `dialog_crops/` 和 `name_crops/` 中的图像做 OCR，结果保存为 JSONL 文件。批量处理可使用 `batch_ocr_all.sh` 脚本。

旧版一体化管线仍然可用：`python -m tools.dialogue_extractor "视频路径" tools/configs/yuexia.yaml --output-dir "输出目录"`，支持 `--batch` 和 `--video-pattern` 参数进行批量处理。

分析截断率可使用 `analyze_truncation.py` 脚本，它对比状态机检测的事件文本和最终 OCR 结果来统计截断比例。

所有命令都应在项目根目录下运行，且需要激活包含 PaddleOCR 的 conda 环境（通常命名为 paddleocr）。

## 已知问题

说话人识别存在噪声问题。名字框 OCR 有时会产生无意义的短字符串（如 'y'、'f'、'-\`' 等），这些噪声来自名字框区域的 ROI 校准不够精确，可能框入了 UI 装饰元素或对话框边缘的图形。解决方案是对名字框的 ROI 坐标进行更精细的校准，但这属于较低优先级的工作。`speaker_extractor.py` 中的 `strict_whitelist` 模式可以部分缓解这个问题，它只接受在已知说话人集合中匹配的名字（允许编辑距离为 1 的模糊匹配），不匹配的走继承机制。

打字机截断是当前正在重点解决的问题。旧管线的截断率为 23.3%（768 个事件中有 179 个被截断），主要原因是 `_is_text_replacement` 将快速增长的打字机文本误判为替换。修复方案是在使用 SequenceMatcher 之前先做子串和前缀包含检查。目标是将截断率降到 5% 以下。

ROI 校准需要针对每个视频单独验证。虽然同一部作品的所有视频通常共享相同的 UI 布局，但不同章节之间可能存在微小差异（如 UI 更新、分辨率变化），建议在处理新视频前先用样本帧验证 ROI 的准确性。`roi_calibrator.py` 提供了交互式校准工具，但目前的 ROI 配置是通过截图估算手动编写的。
