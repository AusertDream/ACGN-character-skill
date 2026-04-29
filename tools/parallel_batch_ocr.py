#!/usr/bin/env python3
"""多 GPU 并行批量 OCR 处理（基于 PaddleOCR 官方并行推理模式）"""
import argparse
import json
import os
import sys
from multiprocessing import Manager, Process
from pathlib import Path
from queue import Empty
from typing import List, Dict
import numpy as np
from PIL import Image

# 设置 PaddlePaddle 显存使用率
os.environ['FLAGS_fraction_of_gpu_memory_to_use'] = '0.95'
os.environ['FLAGS_eager_delete_tensor_gb'] = '0.0'


def worker(
    gpu_id: int,
    task_queue,
    batch_size: int,
    output_file: Path
):
    """Worker 进程：从队列中取任务，批量处理 OCR"""
    from paddleocr import PaddleOCR

    # 初始化 OCR（每个 worker 独立初始化）
    ocr = PaddleOCR(
        lang='ch',
        device=f'gpu:{gpu_id}',
        text_recognition_batch_size=batch_size,
    )

    results = []
    batch = []
    should_end = False

    while not should_end:
        try:
            task = task_queue.get_nowait()
        except Empty:
            should_end = True
        else:
            batch.append(task)

        # 处理批次
        if batch and (len(batch) >= batch_size or should_end):
            try:
                # 加载图片
                images = []
                for event_dir in batch:
                    text_img = Image.open(event_dir / 'text.png').convert('RGB')
                    speaker_img = Image.open(event_dir / 'speaker.png').convert('RGB')
                    images.append((event_dir.name, 'text', np.array(text_img)))
                    images.append((event_dir.name, 'speaker', np.array(speaker_img)))

                # 批量 OCR
                image_arrays = [img[2] for img in images]
                ocr_results = ocr.predict(image_arrays)

                # 解析结果
                for (event_id, img_type, _), result in zip(images, ocr_results):
                    if isinstance(result, dict):
                        texts = result.get('rec_texts', [])
                        scores = result.get('rec_scores', [])
                        text = ' '.join(texts) if texts else ''
                        confidence = float(np.mean(scores)) if scores else 0.0
                    else:
                        text, confidence = '', 0.0

                    # 查找或创建结果条目
                    entry = next((r for r in results if r['event_id'] == event_id), None)
                    if entry is None:
                        entry = {'event_id': event_id, 'text': '', 'text_confidence': 0.0,
                                'speaker': '', 'speaker_confidence': 0.0}
                        results.append(entry)

                    if img_type == 'text':
                        entry['text'] = text
                        entry['text_confidence'] = confidence
                    else:
                        entry['speaker'] = text
                        entry['speaker_confidence'] = confidence

                print(f"[GPU {gpu_id}] Processed batch of {len(batch)} events", flush=True)

            except Exception as e:
                print(f"[GPU {gpu_id}] Error processing batch: {e}", file=sys.stderr, flush=True)

            batch.clear()

    # 写入结果
    with open(output_file, 'w', encoding='utf-8') as f:
        for result in results:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')

    print(f"[GPU {gpu_id}] Finished. Wrote {len(results)} results to {output_file}", flush=True)


def detect_gpu_count() -> int:
    """检测可用 GPU 数量"""
    import subprocess
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index', '--format=csv,noheader'],
            capture_output=True, text=True, check=True
        )
        return len(result.stdout.strip().split('\n'))
    except:
        return 1


def main():
    parser = argparse.ArgumentParser(description='多 GPU 并行批量 OCR 处理')
    parser.add_argument('frames_dir', type=Path, help='帧目录（包含 event_XXXXXX 子目录）')
    parser.add_argument('--output-dir', type=Path, default=Path('/data2/training_data/ocr_output'),
                       help='输出目录')
    parser.add_argument('--batch-size', type=int, default=256,
                       help='每个 worker 的批处理大小')
    parser.add_argument('--instances-per-gpu', type=int, default=1,
                       help='每个 GPU 运行的 worker 实例数')
    parser.add_argument('--gpus', type=str, default=None,
                       help='使用的 GPU ID（逗号分隔，如 "0,1,2,3"），默认使用所有 GPU')

    args = parser.parse_args()

    if not args.frames_dir.exists():
        print(f"Error: {args.frames_dir} does not exist", file=sys.stderr)
        return 1

    # 确定使用的 GPU
    if args.gpus:
        gpu_ids = [int(x.strip()) for x in args.gpus.split(',')]
    else:
        gpu_ids = list(range(detect_gpu_count()))

    print(f"Using GPUs: {gpu_ids}")
    print(f"Instances per GPU: {args.instances_per_gpu}")
    print(f"Batch size: {args.batch_size}")

    # 收集所有事件目录
    event_dirs = sorted(args.frames_dir.glob('event_*'))
    if not event_dirs:
        print(f"Error: No event directories found in {args.frames_dir}", file=sys.stderr)
        return 1

    print(f"Found {len(event_dirs)} events to process")

    # 创建输出目录
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # 使用 Manager 创建共享队列
    with Manager() as manager:
        task_queue = manager.Queue()

        # 填充任务队列
        for event_dir in event_dirs:
            task_queue.put(event_dir)

        # 启动 worker 进程
        processes = []
        worker_id = 0
        for gpu_id in gpu_ids:
            for instance in range(args.instances_per_gpu):
                # 使用短名称避免文件名过长
                import hashlib
                dir_hash = hashlib.md5(args.frames_dir.name.encode()).hexdigest()[:8]
                output_file = args.output_dir / f"ocr_{dir_hash}_gpu{gpu_id}_w{instance}.jsonl"
                p = Process(
                    target=worker,
                    args=(gpu_id, task_queue, args.batch_size, output_file)
                )
                p.start()
                processes.append(p)
                worker_id += 1
                print(f"Started worker {worker_id} on GPU {gpu_id}")

        # 等待所有进程完成
        for p in processes:
            p.join()

    print("All workers finished. Merging results...")

    # 合并所有输出文件
    # 使用短名称避免文件名过长
    import hashlib
    dir_hash = hashlib.md5(args.frames_dir.name.encode()).hexdigest()[:8]
    merged_output = args.output_dir / f"ocr_{dir_hash}.jsonl"
    all_results = []
    for output_file in args.output_dir.glob(f"ocr_{dir_hash}_gpu*.jsonl"):
        with open(output_file, 'r', encoding='utf-8') as f:
            for line in f:
                all_results.append(json.loads(line))

    # 按 event_id 排序
    all_results.sort(key=lambda x: x['event_id'])

    # 写入合并文件
    with open(merged_output, 'w', encoding='utf-8') as f:
        for result in all_results:
            f.write(json.dumps(result, ensure_ascii=False) + '\n')

    print(f"Merged {len(all_results)} results to {merged_output}")

    # 清理临时文件
    for output_file in args.output_dir.glob(f"ocr_{dir_hash}_gpu*.jsonl"):
        output_file.unlink()

    return 0


if __name__ == '__main__':
    sys.exit(main())
