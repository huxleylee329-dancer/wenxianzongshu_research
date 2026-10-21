# SectionWriter 云端后训练准备包（Featurize / 15 元小实验）

## 当前完成程度

这是独立的 **离线准备工具**，不是生产功能、不是新的 Milestone。
不修改 SectionWriter、DTO、Gate、Reviewer、Composer、Graph 或项目依赖。
本地只需 Python 3.11 + Pydantic；不安装 GPU 依赖，不读取 `.env`，不调用服务商。
未租用 GPU、未上传文件、未下载模型、未进行训练。CUDA/显存/吞吐仍待云端短跑验证。

模型候选为 `Qwen/Qwen3-8B`，`enable_thinking=False`。
首轮使用 4-bit NF4 + LoRA（rank 8，q_proj/v_proj）+ TRL GRPO；不用第二个
vLLM 模型副本，降低部署复杂度，但生成速度可能较慢。不是全参数训练。
训练栈固定在已核对的 TRL 0.26.0 接口，不随意安装最新版本混用。
24GB 并非任意上下文都能容纳；OOM 后停止，不自动重试或更换配置收费。

## 必须知道的训练边界

1. RAG 提供证据，训练的是“如何用证据写一个短节”，不是让模型记住整个论文库。
2. 复用生产 `_prepare_attempt` / `_SYSTEM_MESSAGE` / `_parse_response`，不复制 parser。
   每个候选只解析一次，不调用 Writer adapter 的内部 retry，不调用前序节点。
3. 生产 user message 是字符上限；本实验是 **2048 prompt tokens / 384 completion tokens**。
   包括 chat template 后由实际 tokenizer 计数，超限整条拒绝，不左截断。
   不覆盖生产 65536 字符长上下文，不能据此声称长篇能力提升。
4. 全新训练数据没有默认真值。现有 `supported` review、人工导出或格式通过都
   **不能自动当作事实正确标签**。
5. 预算不支持对所有候选调用昂贵事实裁判。本版 reward 是保守的
   **人工审核句式＋精确证据归属的封闭评分表**，仅适用于受控短节：
   - 生产 parser 失败：-1；
   - 输出由审核过的句式及匹配 citation 构成：按覆盖的不同事实比例给 0..1；
   - 任意未识别的改写/附加断言/错引：0；不是宣判该说法事实错误；
   - 不因字数、重复事实、引用数量给额外分；没有格式通过的额外正奖励。
   这不是开放域语义蕴含裁判。真实改写可能误扣分；人工复核必不可少。
   若基线从未得到正分或前 8 批没有有效相对奖励，停止，不能靠训练硬跑解决。
6. 当前 6 个 synthetic 样例仅检查软件。它们明确是虚构实验，不是论文事实，
   工具拒绝把它们打包为真实训练数据。当前已准备的真实可训练样本数为 **0**。

## 本地：零费用步骤

在仓库根目录执行（不会导入应用/Provider 或访问网络）：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s training/section_writer -p test_offline.py -v
.\.venv\Scripts\python.exe -B training/section_writer/prepare.py demo --out outputs/section-writer-grpo/demo.jsonl
.\.venv\Scripts\python.exe -B training/section_writer/prepare.py check --data outputs/section-writer-grpo/demo.jsonl
```

输出采用独占创建；已有文件不覆盖。示例已生成时只运行 check。

### 准备真实样本

需要**明确指定**一个真实已审批的 PersistentState JSON（不会遍历 TEMP 或知识库）。
只验证和构造内存 Writer 执行视图，绝不保存伪造 checkpoint 或自动批准目录：

```powershell
.\.venv\Scripts\python.exe -B training/section_writer/prepare.py export --state PATH_TO_APPROVED_STATE.json --group paper-topic-01 --out outputs/section-writer-grpo/pending-01.jsonl
```

如果只有报告正文/citation-audit/Composition，没有完整 state，不能恢复原始生产 prompt；
不要据此合成假 checkpoint。可以在之后提供已批准 state，或单独整理合法的离线案例。

导出逐节包含生产 prompt、允许 ID、原始 provenance（只作标注用，不混入 prompt）、
论文 URL/证据指纹、生产源文件 SHA。默认 `annotation_approved=false`、`cloud_ok=false`。
不要上传 `.env`、密钥、整个仓库或未授权论文；私有知识库材料先确认上传和训练许可。
`cloud_ok` 是人工许可记录，不是法律许可检查器。

标注 `rubric.units`，每项：

```json
{
  "claim_id": "experiment-scope",
  "variants": ["经人工核查的完整短句，包含适用条件和必要限定。"],
  "source_ids": ["evidence-source:000001"],
  "evidence_quotes": [{
    "source_id": "evidence-source:000001",
    "block_index": 0,
    "quote": "从原始证据逐字复制的支持片段，不是新写的总结"
  }]
}
```

`variants` 是不含 marker 的完整表达，可有多种人工认可的改写。对应引用紧随该表达。
工具要求 quote 同时存在于对应 source 的原始 evidence block 和生产 Writer
实际可见的 context 中；**只在被截掉的 provenance 中存在不算可训练依据**。
它只能验证锚点存在，事实支持与句式忠实仍需人工审核。
不强迫引用第一个 source，不伪造/插入正文引用。评分表绝不进入模型 prompt。

建议先准备约 60 个训练短节、20 个评估短节，涵盖不同论文/主题/证据条件。
同一论文的八节不能分别进入 train/eval。自动将共享 topic、URL 或证据块的样本
合并分组再切分；仍需人工核对 DOI 镜像/近似重复和主题泄漏。少量评估只能给方向，
不是统计上稳健的质量证明。训练最小门禁为 16 train / 4 eval。

审核并授权后合并 JSONL，运行：

```powershell
.\.venv\Scripts\python.exe -B training/section_writer/prepare.py split --data outputs/section-writer-grpo/reviewed.jsonl --out outputs/section-writer-grpo/split
.\.venv\Scripts\python.exe -B training/section_writer/run.py --data outputs/section-writer-grpo/split
.\.venv\Scripts\python.exe -B training/section_writer/prepare.py bundle --data-dir outputs/section-writer-grpo/split --out outputs/section-writer-grpo/featurize-reviewed.zip
```

不带 `--data-dir` 的 bundle 只生成工具、合成样例和 byte-identical 生产依赖快照，
**不含真实训练数据**。生成产物写在已忽略的 `outputs/`，不会自动上传。
bundle 采用白名单，不包含 `.env`/应用服务/完整 checkpoint/用户知识库。
快照仅加载确定性 DTO/prompt/parser；`runtime.py` 跳过应用 __init__，因此只应在
此独立脚本进程中运行，不在生产服务进程 import 该工具。

## Featurize：准备完成、价格确认后才开机

公开报价参考：4090 普通版 1.87 元/小时；按分钟计费，租用即开始收费。
不要误选 4090 48G 或长租。最低充值金额、实际价格和其他费用须在控制台确认。
模型准备、环境安装、评估的开机时间也收费；预留 3 元意味着算力预算最多 12 元。
脚本依据**同一个实际租用开始时间**计算剩余预算，不能每条命令重置。

1. 上传前确认数据是自己的、许可允许训练，并保持私有；不上传 `.env`。
2. 在 `/home/featurize/work/section-writer-grpo/` 解压。
3. 建独立 Python 3.11 虚拟环境，保留/安装适配驱动的 CUDA torch；然后安装
   `requirements-cloud.txt`。**这一步尚未执行，版本组合须现场验证。**
4. 单独准备本地 `Qwen/Qwen3-8B` 权重快照，记录官方模型 revision；下载需另获执行授权。
   本训练入口 `local_files_only=True`，禁用 Hub 网络，不会自动下载模型或裁判。
5. 先验证 `nvidia-smi`、Python/torch/CUDA/bitsandbytes/TRL，并做短基线测显存/耗时。
   未通过不启动训练；不在小预算里盲目升级依赖。

以下只是**未来执行模板**，目前不可对 synthetic demo 执行：

```bash
# 采用实际租用时间，不要用每次运行时的当前时间替代！
START='实际ISO时间，必须带时区'
MODEL='/home/featurize/work/models/Qwen3-8B'
python -B run.py --data split --model-dir "$MODEL" --phase before --out before \
  --execute --rental-start "$START" --hourly-price 1.87 --reserve-yuan 3
python -B run.py --data split --model-dir "$MODEL" --phase train --out train-run \
  --baseline-dir before --execute --rental-start "$START" --hourly-price 1.87 --reserve-yuan 3 --max-steps 32
python -B run.py --data split --model-dir "$MODEL" --phase after --out after \
  --adapter train-run/adapter --baseline-dir before --execute --rental-start "$START" --hourly-price 1.87 --reserve-yuan 3
```

以上只有三个独立阶段，不是失败 retry。任何失败先停止并检查，不能自动重跑。
父进程超时结束训练进程，保存每 4 步的 LoRA 检查点；硬超时可能损失最后几步。
**结束 Python 不会停止平台计费。下载并验证结果后，必须在 Featurize 归还实例。**
不承诺仅靠本脚本绝对保证账单小于 15 元；其他收费/平台结算也必须检查。

## 如何看结果

基线/训练/评估还逐文件核对本地 safetensors、tokenizer/config 的 SHA，不能中途
替换同名模型或数据再比较。模型文件校验时间也属于租用时间。
比较 `before/summary.json` 和 `after/summary.json` 的首次 parser 通过率、
封闭 rubric 得分/覆盖率。日志不输出 prompt、正文或 evidence；评估原文只写入明确的
本地 `human-review.jsonl` 供人工核查，不自动上传，不作为事实真值。
还应盲评：有无新增事实、错误数字/条件、错引、机械照抄和章节任务偏离。
只看 reward 上升不足以批准部署；微型语料提高也不能宣称全领域能力提升。

结果未验证前不接生产。将来只通过现有 SectionWriter client-factory 注入适配模型，
不全局改 `STRATEGIC_LLM`（否则可能连 Reviewer 也变更），不改 Graph 或其他节点。
训练奖励不调用生产 Reviewer，因此不会把同一模型的自评当作训练真值。

参考（准备时核对，不是已实测声明）：
- https://huggingface.co/Qwen/Qwen3-8B
- https://huggingface.co/docs/trl/v0.26.0/grpo_trainer
- https://huggingface.co/docs/peft/developer_guides/quantization
- https://featurize.cn/vm/available
- https://docs.featurize.cn/docs/manual/instance-rent
