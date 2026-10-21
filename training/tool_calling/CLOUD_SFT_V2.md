# SFT v2：补充歧义澄清与候选筛选

本包包含200条训练：120条v1原样回放、80条新样本；新样本从240条训练专用池选出。
25类各8条，所有四种工具、缺失信息澄清、权限与取消行为继续覆盖。
新增同标题多候选、筛选后仍歧义、按来源或年份唯一定位、完整ID复制、文档范围与权限样本。
候选顺序打乱，返回数量不改变授权检索范围。数据仍是合成数据，human_reviewed=false。

沿用已成功运行的sft.py、模型snapshot、V3提示词、评分器和训练设置。
单卡4090，NF4、BF16、q/v rank8，learning_rate=1e-4；prompt不计loss，答案和EOS计loss。
先2步smoke；正式训练从原始Qwen3-8B新建adapter，200条一轮50步，随后同200题验证。
旧90%adapter及其结果保留在本地，正式实验只改变训练样本。

验证集错题用于发现技能缺口，题目、话题、ID、DOI、文档与新训练样本隔离。
因此该验证集已经参与实验设计，不能作为未触碰的最终测试；盲测继续保留。
预期收益未测量，不能把本地reference检查当作模型成绩。

## 1. 上传、找到文件、解压

准备执行时再租单张24GB RTX4090，上传tool-calling-sft-v2.zip到JupyterLab。
下面在/home/featurize内寻找文件，避免JupyterLab相对目录与系统路径不一致。
只允许一个匹配文件；目录存在就停止，不覆盖或删除。

```bash
python - <<'PY'
from pathlib import Path
import os, zipfile, venv
base = Path('/home/featurize')
matches = []
for root, dirs, files in os.walk(base):
    depth = len(Path(root).relative_to(base).parts)
    dirs[:] = [d for d in dirs if not d.startswith('.') and d != 'models'] if depth < 4 else []
    matches += [Path(root)/n for n in files if n == 'tool-calling-sft-v2.zip']
assert len(matches) == 1, '未找到唯一训练包，停止并检查上传位置/文件名'
target = base/'tool-sft-v2'
assert not target.exists(), '目录已存在，停止'
with zipfile.ZipFile(matches[0]) as z:
    assert z.testzip() is None
    assert len(z.namelist()) == len(set(z.namelist())) == 15
    assert all(not Path(n).is_absolute() and '..' not in Path(n).parts and '\\' not in n for n in z.namelist())
    z.extractall(target)
venv.EnvBuilder(with_pip=True).create(target/'.venv')
print('SETUP_OK:', target)
PY
```

## 2. 固定环境与预检

```bash
cd /home/featurize/tool-sft-v2
source .venv/bin/activate
python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128 && \
python -m pip install -r requirements-grpo.txt && \
python sft.py --phase smoke
```

预期preflight_only、实际optimizer_steps=0、generation_calls=0。
不使用system-site-packages；pip升级提示可以忽略。

## 3. 模型下载并校验

```bash
python fetch_sft_model.py --model-dir /home/featurize/tool-sft-v2/models/Qwen3-8B --execute
```

只在downloaded_and_verified或already_verified后继续。
若保留了原模型，可将后续model-dir改为该完整本地snapshot路径。
模型12文件、全部版本、GPU及生成参数必须匹配原基线，不能只按模型名称判断。
安装依赖和显式模型下载联网；训练及评估强制offline。

## 4. 两步smoke

```bash
CUDA_VISIBLE_DEVICES=0 USE_TF=0 python sft.py --phase smoke --execute --ack-synthetic-data \
  --model-dir /home/featurize/tool-sft-v2/models/Qwen3-8B --out runs/sft-smoke
```

必须complete、2steps、8examples、adapter_changed=true。
v1的50步训练约246秒，实际分配峰值15.33GiB、预留22.31GiB；新版长度不同，需重新smoke确认。
任何失败立即停止，保留run摘要；不自动retry/resume，也不重复已成功的命令。

## 5. 正式50步训练

```bash
CUDA_VISIBLE_DEVICES=0 USE_TF=0 python sft.py --phase train --execute --ack-synthetic-data \
  --model-dir /home/featurize/tool-sft-v2/models/Qwen3-8B \
  --smoke-dir runs/sft-smoke --out runs/sft-train
```

必须complete、50steps、200examples、权重更新且loss/gradient有限。
此阶段重新创建原始基座LoRA；不加载smoke或旧90%的adapter。

## 6. 同200题after评估

```bash
CUDA_VISIBLE_DEVICES=0 USE_TF=0 python sft.py --phase after --execute \
  --model-dir /home/featurize/tool-sft-v2/models/Qwen3-8B \
  --train-dir runs/sft-train --out runs/sft-after
```

必须complete、completed=200、truncated_outputs=0；每题一次greedy nonthinking生成。
不运行真实论文工具、Retriever或付费LLM API。验证答案不在云端包中。

## 7. 打包、下载、归还

成功或失败停止后，都可以打包已有结果。ZIP存在时停止，不覆盖。

```bash
python - <<'PY'
from pathlib import Path
import zipfile
root = Path.cwd()
target = root/'sft-v2-results.zip'
with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as z:
    for name in ('sft-bundle.json', 'before/run.json', 'before/predictions.jsonl'):
        z.write(root/name, name)
    for p in sorted((root/'runs').rglob('*')):
        if p.is_file():
            z.write(p, p.relative_to(root).as_posix())
with zipfile.ZipFile(target) as z:
    assert z.testzip() is None
print('RESULTS_ZIP_OK:', target)
PY
```

JupyterLab右键下载tool-sft-v2/sft-v2-results.zip到电脑，确认完成后归还实例。
脚本退出和关闭浏览器不会归还实例。结果包含adapter，排除模型大权重与虚拟环境。
新实例可能需要重新下载基座；旧90%adapter已在本地保存。

## 8. 本地与70%基座、90%v1共同评分

解压结果到全新outputs/tool-calling-sft-v2-results目录，再在本地仓库根执行：

```powershell
.\.venv\Scripts\python.exe -B -X utf8 training/tool_calling/sft_refresh.py compare `
  --dataset outputs/tool-calling-v2 --bundle outputs/tool-calling-sft-v2 `
  --results outputs/tool-calling-sft-v2-results `
  --v1-results outputs/tool-calling-sft-evaluation-20261004 `
  --out outputs/tool-calling-sft-v2-comparison.json
```

输出总正确率、权限/结构错误、10个验证模板及v1到v2的对错变化。
候选选择条件为总正确率不降、越权及结构错误不增加、歧义澄清改善、年份选择不退步。
未满足条件时保留旧90%adapter，不自动增加训练轮数或转GRPO。
本轮仅准备和评估SFT；GRPO是否继续需要后续训练组采样的奖励差异证据。
