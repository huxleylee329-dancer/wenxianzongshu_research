# 云端基线：准备完成后也不会自动开机

这是单步学术工具决策的**推理基线**，不是GRPO训练。默认仅验包，不导入torch，
不下载模型、不连接论文站点、不执行真实工具。无需API key或仓库.env。
云端包仅包含200条验证prompt、20条分层试跑ID和推理代码；不含训练/盲测数据及答案。

## 下一次经确认后才在Featurize执行

先人工确认实例、费用、CUDA驱动与可用磁盘。脚本不会租用/关闭实例；退出进程也不等于停止计费。
单卡串行4-bit基线以24GB显存作为准备目标，**尚未在GPU测过，不能保证峰值显存或耗时**。
当前选Qwen/Qwen3-8B，不使用用户不满意的4B模型；此选择不代表已证明其任务质量。

解压包到新目录。推荐Python3.11，依赖安装与模型准备都要等开机授权之后：

```bash
python -m pip install -r requirements-baseline.txt
# 事先准备完整Qwen/Qwen3-8B本地safetensors快照，包含tokenizer/chat_template/config。
# 本脚本不负责下载；--model-dir指向完整目录，不是HF仓库名。
python baseline.py
```

以上最后一行只做公开数据/代码SHA和数量校验，应该输出selected=20、generation_calls=0。
安装版本仍须结合实例CUDA实际验证；requirements不声称已GPU验收。

只在明确同意运行后，先20条检验显存、JSON输出和速度：

```bash
CUDA_VISIBLE_DEVICES=0 python baseline.py --execute \
  --model-dir /path/to/Qwen3-8B --out runs/base-smoke
```

20条结果没有OOM/截断等问题后，再单独决定跑完整200条（会重测试跑的20条）：

```bash
CUDA_VISIBLE_DEVICES=0 python baseline.py --execute --full \
  --model-dir /path/to/Qwen3-8B --out runs/base-validation
```

模型仅local_files_only，trust_remote_code=False，safetensors，NF4双量化；
enable_thinking=False，单条贪心生成，最多384新token。固定种子及版本/权重SHA记录在run.json。
贪心是本实验固定基线协议，不等同于模型卡的采样benchmark设置；训练后必须同协议复测。
模型接收常规聊天模板里的JSON决策协议，**不是模型原生tool-call token benchmark**。
全部选中prompt在模型加载前分词；任何超过8192输入token直接停止，不截断任务或权限。
每条只生成一次，无repair/retry/resume；错误会保留已完成predictions及失败run.json。
原始生成（合成任务，无私人论文）仅存结果文件，不回显终端。不把参考答案当模型输出。

## 把结果下载回本地再评分

下载predictions.jsonl及run.json，保留原始文件；不上传本地oracle。仓库根运行：

```powershell
# 20条仅报子集结果，不能声称200条验证集准确率。
.\.venv\Scripts\python.exe -B training/tool_calling/dataset.py score --data outputs/tool-calling-v2 --predictions PATH_TO_PREDICTIONS.jsonl --allow-subset
# 完整200条去掉--allow-subset；不齐全/重复/未知ID固定拒绝。
```

报告总成功率及分类成功率、权限违规率、格式错误率、任务不匹配率、不必要调用数。
部分运行只能在显式--allow-subset下评分，报告coverage及subset_only，不掩盖未执行样本。
输入prompt/私有oracle哈希只防偶发变动，不是防恶意篡改的签名。

## 开始GRPO之前仍需做

1. 人工抽查review-sample.md，重点审查澄清/无需调用和权限边界。当前human_reviewed=false。
2. 检查基线错误分布。如果近乎满分需增加真实难例；若主要JSON错误，先考虑少量SFT。
3. 再准备同题多候选的奖励方差检查及LoRA-GRPO训练入口。本包没有启动或假装完成训练。
4. 盲测200条留在本地，不能用来调prompt/奖励。当前评分命令主动拒绝盲测。

数据为45种参数化合成模板：train25×40=1000，validation10×20=200，blind10×20=200。
模板名、主题、实体、DOI、文档ID、完整prompt跨集合无交集；这**不能证明语义完全独立**，
也不代表1400条真实用户请求或实际论文。工具类别跨集合共享是任务本身所需。

参考：[Qwen3-8B模型卡](https://huggingface.co/Qwen/Qwen3-8B)、
[Transformers4.57.3 bitsandbytes](https://huggingface.co/docs/transformers/v4.57.3/quantization/bitsandbytes)。
