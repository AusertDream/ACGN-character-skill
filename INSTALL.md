# 安装指南

## 环境要求

- Python 3.10+
- CUDA 12.x（用于 GPU 加速）
- Conda（推荐）

## 安装步骤

### 1. 激活 paddleocr 环境

```bash
conda activate paddleocr
```

### 2. 安装 PaddlePaddle GPU 版本

**重要：** PaddlePaddle 官方源缺少 `nvidia-cuda-cccl-cu12` 依赖，需要先从 PyPI 安装。

**CUDA 12.x（当前系统）：**
```bash
pip install nvidia-cuda-cccl-cu12==12.3.52
pip install paddlepaddle-gpu==3.1.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu123/
```

**CUDA 11.x：**
```bash
pip install nvidia-cuda-cccl-cu11
pip install paddlepaddle-gpu==3.1.0 -i https://www.paddlepaddle.org.cn/packages/stable/cu118/
```

**CPU 版本（不推荐，速度慢）：**
```bash
pip install paddlepaddle==3.1.0
```

### 3. 安装其他依赖

```bash
pip install -r requirements.txt
```

### 4. 验证安装

```bash
python -c "import paddle; print('PaddlePaddle version:', paddle.__version__); print('GPU available:', paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)"
python -c "from paddleocr import PaddleOCR; print('PaddleOCR imported successfully')"
```

预期输出应显示 PaddlePaddle 版本号和 `GPU available: True`。

## 常见问题

### protobuf 版本冲突

如果遇到 protobuf 相关错误，尝试：
```bash
pip install protobuf==3.20.0
```

### OpenCV 冲突

如果遇到 OpenCV 相关错误，卸载所有 opencv 包后重新安装：
```bash
pip uninstall opencv-python opencv-python-headless opencv-contrib-python -y
pip install opencv-python>=4.8
```

### GLIBC 版本问题

如果遇到 GLIBC 版本错误，确保系统 GLIBC 版本 >= 2.27。

## GPU 约束

系统共有 4 张 NVIDIA L40S GPU（每卡 45GB 显存）。**本项目仅使用 GPU 2 和 GPU 3**，GPU 0 和 1 被其他用户占用，严禁使用。所有命令行工具的 `--gpus` 或 `--gpu-id` 参数默认指向 2（或 2,3），不要修改为 0 或 1。

## 可选：LLM 纠错

如需启用 LLM OCR 纠错（`--llm-correct`），设置环境变量：
```bash
export DEEPSEEK_API_KEY="your-api-key"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"  # 可选，默认值
```

## GPU 使用验证

运行以下命令确认 GPU 2 和 3 可用：
```bash
nvidia-smi
python -c "import paddle; print('GPU count:', paddle.device.cuda.device_count())"
```
