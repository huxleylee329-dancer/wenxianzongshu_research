# 单卡4090：200条工具决策SFT热身

这是独立于原GRPO包的小规模SFT实验，不是GRPO训练，也不承诺提高准确率。
固定Qwen3-8B、NF4双量化、BF16、q_proj/v_proj rank8 LoRA；25类训练模板各8条，
共200条训练样本，一轮50个优化步。仅对答案和结束EOS计算loss，prompt与padding不计loss。
先做2步smoke，再从原始基座重新开始正式训练；不接着训练smoke adapter。

数据仍为参数化合成任务，`human_reviewed=false`。训练参考答案进入监督目标，
不进入模型输入prompt；验证答案和盲测数据不在云端包中。现有V3基线为140/200（70%），
prompt、评分器、验证题不变；最终提升只记为SFT收益，不声称GRPO收益。

脚本默认仅预检，不加载GPU、不自动下载模型、不调外部LLM或真实工具。
只有安装依赖和显式模型下载需要联网；训练和推理使用本地模型并强制offline。
以下每一步成功后才执行下一步；任何错误立即停止，不自动retry/resume、不修改门禁。
训练过程失败/取消可能没有可用adapter，不承诺保存未完成权重。

## 1. 准备实例、上传与解压

先人工确认云GPU租用及费用，再租单张24GB RTX4090。Python使用3.11。
把本地 `tool-calling-sft-v1.zip` 上传至 `/home/featurize`；不要覆盖原GRPO目录。
首次执行：

```bash
cd /home/featurize
test ! -e tool-sft-v1 && \
python -m zipfile -e tool-calling-sft-v1.zip tool-sft-v1 && \
cd tool-sft-v1 && \
python -m venv .venv
```

必须看到成功返回的命令提示符，没有错误才继续。目录已存在则停止，勿重复解压覆盖。
云端当前目录应为 `/home/featurize/tool-sft-v1`。

## 2. 安装固定依赖

不使用 `--system-site-packages`，避免继承不兼容的系统torchvision。

```bash
cd /home/featurize/tool-sft-v1
source .venv/bin/activate
python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128 && \
python -m pip install -r requirements-grpo.txt
```

安装成功后做无训练预检：

```bash
python sft.py --phase smoke
```

预期为 `preflight_only`、GPU未加载、0次生成、0个训练步；这不代表已完成GPU验收。

## 3. 显式下载并校验模型

原实例已归还时不能假定原模型仍在。下载器依据原基线模型文件SHA校验，
不能把“同样叫Qwen3-8B”当作同一个snapshot。任何文件哈希不符立即停止，不能换expected值。

```bash
python fetch_sft_model.py --model-dir /home/featurize/tool-sft-v1/models/Qwen3-8B --execute
```

这一步会联网下载公开模型，不调用模型推理或外部付费LLM API；云实例仍按时间计费。
成功后执行导入检查：

```bash
USE_TF=0 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python - <<'PY'
import torch
import torchvision
from peft import LoraConfig
from transformers import Trainer, TrainingArguments
from transformers.models.qwen3.modeling_qwen3 import Qwen3ForCausalLM
print("PyTorch:", torch.__version__)
print("Torchvision:", torchvision.__version__)
assert torch.cuda.is_available() and torch.cuda.device_count() == 1
assert torch.cuda.is_bf16_supported()
print("GPU:", torch.cuda.get_device_name(0))
print("IMPORT_OK")
PY
```

代码、200条选择、旧基线和模型均有SHA门禁。运行环境/GPU或模型与原基线不匹配时停止；
不要自行绕过门禁。需另行安排新before基线，而不是把不同配置结果直接与70%比较。

## 4. 先做2步smoke

只有上述检查全部通过后执行：

```bash
CUDA_VISIBLE_DEVICES=0 USE_TF=0 python sft.py --phase smoke --execute --ack-synthetic-data \
  --model-dir /home/featurize/tool-sft-v1/models/Qwen3-8B \
  --out runs/sft-smoke
```

`--ack-synthetic-data`表示知晓使用合成数据，不代表已经人工审核全部标签。
确认结果 `status=complete`、`optimizer_steps=2`，并检查有限loss、权重实际更新及显存峰值。
出错则停止，不继续正式训练；不以忽略异常、缩短输入或更改标签方式强行运行。
本地fake单元测试无法证明真实CUDA可用；实际时间和显存以这一步记录为准。

## 5. 正式训练一轮200条

此阶段重新加载原始基座并创建新LoRA；smoke只作为已完成环境检查证据。

```bash
CUDA_VISIBLE_DEVICES=0 USE_TF=0 python sft.py --phase train --execute --ack-synthetic-data \
  --model-dir /home/featurize/tool-sft-v1/models/Qwen3-8B \
  --smoke-dir runs/sft-smoke --out runs/sft-train
```

预期完整成功、200条、50个优化步；产物在 `runs/sft-train/adapter`。
所有输出目录必须全新，不能覆盖、自动resume或拿smoke adapter冒充正式结果。
最多只执行这一轮，不自动增加epoch、全1000条训练或转GRPO。

## 6. 同200条验证题做after推理

仅正式训练完整成功后执行。每题一次贪心生成，nonthinking、相同token上限和量化设置；
没有真实工具调用，没有验证参考答案进入模型。此阶段0个优化步。

```bash
CUDA_VISIBLE_DEVICES=0 USE_TF=0 python sft.py --phase after --execute \
  --model-dir /home/featurize/tool-sft-v1/models/Qwen3-8B \
  --train-dir runs/sft-train --out runs/sft-after
```

完整结果应为 `status=complete`、200条、0截断。失败、缺失或重复ID不能宣称200题完整成绩。
不要重跑已有成功组合。云端只生成答案，评分留在本地，不上传验证oracle。

## 7. 打包、下载，随后归还

完整成功或失败停止后都先保存已有run摘要。执行下列命令会创建新ZIP，存在同名文件则拒绝：

```bash
python - <<'PY'
from pathlib import Path
import zipfile

root = Path.cwd()
archive_path = root / "sft-results.zip"
with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for relative in ("sft-bundle.json", "before/run.json", "before/predictions.jsonl"):
        archive.write(root / relative, relative)
    for path in sorted((root / "runs").rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(root).as_posix())
with zipfile.ZipFile(archive_path) as archive:
    assert archive.testzip() is None
print("RESULTS_ZIP_OK:", archive_path.resolve())
PY
```

在JupyterLab找到 `tool-sft-v1/sft-results.zip`，右键Download到电脑，确认下载成功。
包里包含adapter（若已成功保存）、before证据和全部run输出，不包含模型大权重或虚拟环境。
随后在Featurize页面归还实例并确认已归还；脚本退出或关闭浏览器不会自动停止计费。
归还前未下载/未持久保存的内容可能丢失。若不愿等待大模型备份，可只下载结果ZIP，
明确接受下次重新下载模型；不需要为了保留空闲实例持续计费。

## 8. 回到本地评分

保留原 `outputs/tool-calling-sft-v1` 云包及数据集，解压下载ZIP到一个新目录，例如
`outputs/tool-calling-sft-results`。在Windows仓库根使用原评分器入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B training/tool_calling/sft_bundle.py score `
  --dataset outputs/tool-calling-v2 --bundle outputs/tool-calling-sft-v1 `
  --after outputs/tool-calling-sft-results/runs/sft-after `
  --train-dir outputs/tool-calling-sft-results/runs/sft-train `
  --out outputs/tool-calling-sft-comparison.json
```

比较总正确率、工具类别、越权率和正确/错误互转；不把70%以上的单次数字自动等同生产质量。
若成绩无改善或安全指标下降，停止扩大。只有独立检查后才决定是否继续GRPO，
不把SFT收益、prompt收益、GRPO收益混为一谈。盲测仍保留，不参与选参。

实现依据：[固定版本Trainer接口](https://github.com/huggingface/transformers/blob/v4.57.3/src/transformers/trainer.py)、
[PEFT量化训练说明](https://huggingface.co/docs/peft/v0.18.0/en/developer_guides/quantization)。
本包显式构造预分词labels，不依赖自动assistant mask或packing；仍需云端smoke证明该环境可运行。
