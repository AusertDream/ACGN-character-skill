# 当前工作进展

## 总体目标

完成统一 OCR pipeline，从游戏剧情视频中自动提取对话文本，用于角色 Skill 生成。

## 最新进展（2026-05-08）

**全量 7 视频处理完成**，使用手工标注的 ROI 配置 + 统一 pipeline 重新提取：

| 视频 | 事件数 | 文本行数 | 说话人去噪前 | 说话人去噪后 |
|------|--------|----------|--------------|--------------|
| ep01 | 1041 | 1037 | 修改 10 条 | 0.96% |
| ep17 | 2086 | 2064 | 修改 107 条 | 5.13% |
| ep18p1 | 1082 | 1075 | 修改 113 条 | 10.44% |
| ep18p2 | 1116 | 1103 | 修改 24 条 | 2.15% |
| ep18p3 | 1246 | 1239 | 修改 78 条 | 6.26% |
| ep18side | 173 | 171 | 修改 15 条 | 8.67% |
| ep19 | 2256 | 2247 | 修改 56 条 | 2.48% |
| **合计** | **9000** | **8936** | **403 条** | **4.48%** |

**质量评估**（subagent 全面检查）：
- 截断率：<0.2%（自适应状态机有效）
- 文本可读性：4/5（剧情连贯、对话通顺）
- OCR 精度：ep01 最高（4.7/5），ep18side 最低（1.0/5，但说话人"少女"是正确的）
- 说话人识别：ep01 92.7%，其他 73-87%
- OCR 后缀噪声：已通过 LLM 去噪处理（deepseek-v4-flash），修改 403 条

**输出位置**：`/data2/training_data/ocr_output/`，每个视频包含：
- `stage1_frames/` — 事件帧和 ROI 裁切
- `ocr_results.jsonl` — Stage 2 原始 OCR
- `ocr_results_merged.jsonl` — 打字机前缀合并后
- `ocr_results_corrected.jsonl` — 正则纠错后
- `ocr_results_denoised.jsonl` — LLM 说话人去噪后
- `dialogue.txt` — 纯文本对话

## 已完成

1. **统一 pipeline 架构**：`unified_pipeline.py` 串联 6 个阶段（Auto ROI → Frame Extraction → Batch OCR → Post-merge → Text Correction → Text Output）
2. **自适应状态机**：增长率追踪 + MAD skip + Levenshtein 停时判据，截断率从 23.3% 降至 <0.2%
3. **手工 ROI 标注**：`roi_annotator.py` 网页工具，全部 7 个视频手工标注 dialog_box 和 name_box
4. **说话人去噪**：LLM 批量清理 OCR 后缀噪声（EKR、福、享、Ta] 等），修改 403 条
5. **GPU 管理**：通过 `CUDA_VISIBLE_DEVICES` 指定 GPU，内部统一用设备 0，避免设备映射冲突
6. **文档修复**：`text_output.py` 兼容简化 JSONL 格式，生成 dialogue.txt

## 待完成

- 用提取的对话文本生成/更新月下的角色 Skill
- 测试 LLM 纠错（`--llm-correct`，需配置 DEEPSEEK_API_KEY）

## GPU 约束

仅使用 GPU 2 和 GPU 3（NVIDIA L40S 45GB）。GPU 0/1 被其他用户占用，严禁使用。

运行方式：
```bash
CUDA_VISIBLE_DEVICES=3 python -m tools.process_all_videos
```

## 环境

所有命令需在 `conda activate paddleocr` 后运行。训练视频和输出位于 `/data2/training_data/`。
