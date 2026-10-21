# 学术工具调用 GRPO：离线数据、基线与小规模训练准备

本目录只准备**单步选工具、填参数和停止/澄清决策**。不修改生产工作流，不接管
AcademicPaperResearcher 的固定路由，不训练写作、事实判断或多轮 Agent。
数据/评分/验包只用 Python 标准库；可选云端推理需GPU依赖，本地准备不安装模型、不读.env、
不联网、不租GPU、不产生API费用。
本地准备不启动云实例；基线/训练必须通过显式 `--execute` 单独启动。

## 当前交付与边界

- 保留原50条逐项编写的合成校准任务：search_papers 10、resolve_paper 8、read_paper 10、
  search_library 10、clarify 8、finish 4。中文任务含英文查询和 DOI。
- 所有论文、DOI、文档和返回结果都是**明确虚构的离线测试记录**，不是现实证据。
- 尚未人工审核、尚未训练；正确答案自检通过不代表模型通过。
- 50条共享同一个模拟世界，仅作校准。**禁止随机拆成训练/测试后声称泛化提升。**
- 新增V2：1000训练、200验证、200盲测，分别25/10/10种约束组合模板。包含来源排除、
  请求撤销/改选、同名论文消歧、恶意metadata、权限冲突等；模板名、主题、实体隔离。
  **这是45模板的参数化合成扩充，不是1400个独立真实任务**；语义相似性仍可能存在。
- 1400条参考动作及8040个错误动作检查只是生成器/评分器自检，不是模型成绩。
- V2云端基线已完成：Qwen3-8B非思考4-bit、RTX4090，200条中129条正确（64.5%），
  388.784秒，实际工具调用0。结果位于 `outputs/tool-calling-baseline-evaluation-20261004/`。
  原基线包/提示/标签/评分器全部保留，不用新的提示词覆盖旧成绩。
- V3仅收窄缺参规则：query仅属于检索工具，DOI仅属于解析工具；read_paper不要求二者。
  加强固定reason标签说明。任务、上下文、工具schema、标签和奖励不变。
- 已准备单卡4-bit LoRA GRPO入口（见下文），本地只用fake trainer验证衔接，
  **真实GPU训练、显存、奖励组差异和训练效果尚未验证**。
- 当前只接受用户明确指定的检索串及大小写/空白等价写法；不对任意语义相同的
  检索词改写做自动判断。因此不是开放式“搜索 query 优化”训练。

## 接口与生产映射

四个工具取自 `gpt_researcher/academic_library/research.py:PaperTools`。
`tools.json` 是**训练侧序列化接口**，还没有注册成生产 LLM tools。

| 训练接口 | 生产接口/限制 |
|---|---|
| search_papers(query, providers, per_source=6) | providers JSON数组未来转tuple；1..6唯一provider；query 1..4096；per_source 1..20 |
| resolve_paper(identifier) | Crossref DOI解析，须授权crossref；支持裸DOI、doi:及doi.org链接的训练子集 |
| read_paper(paper_id) | 生产接收Paper对象；未来只能由可信候选表把ID映射回原Paper，禁止模型自行构造对象或任意URL |
| search_library(query, document_ids, limit=12) | document_ids转tuple；本训练包装要求1..64个授权ID；limit 1..64 |

注意：生产低层 search_library 的空 document_ids 可以表示全库；训练侧**明确禁止**
这个隐式全库入口。授权属于包装层策略，不能声称生产低层已经替模型做好全部检查。
运行模式和白名单由可信会话上下文提供，模型不能修改；local禁止远端工具，web禁止本地工具。
库ID为32位小写hex。候选顺序确定性打乱，避免永远选第一条的捷径。

输出仅有三种，不执行模型输出的代码：

```json
{"action":"call","tool":"search_papers","arguments":{"query":"固态电池界面","providers":["openalex"],"per_source":6}}
{"action":"clarify","reason":"missing_identifier"}
{"action":"finish","reason":"no_tool_needed"}
```

clarify/finish是本试验的决策标签，不是新增生产工具。
每个模拟环境只允许消费一次决策；不重试、不执行第二次工具。

## 文件

- `contracts.py`：严格JSON解析、参数合同和四个工具schema。
- `environment.py`：完全离线、固定目录的模拟执行；只返回mock ID/status。
- `cases.py`：50条任务、上下文、参考动作和结果目标。
- `rewards.py`：评分与可供未来TRL使用的reward callback；不导入TRL。
- `prepare.py`：生成、完整性校验、批量评分、错误动作自检。
- `test_offline.py`：网络被禁止的本地行为测试。
- `dataset.py`：V2生成、跨split查重、参考/错误动作验证、评分、公开云端包。
- `baseline.py`：只读本地模型推理，无真实工具、无下载、无自动retry。
- `test_dataset.py`：分割/泄漏/评分覆盖、云端默认不执行、单次生成及失败留档检查。
- `CLOUD_BASELINE.md` / `requirements-baseline.txt`：待授权后使用的云端运行说明与依赖。
- `prompt_v3.py`：版本化的新system规则，不改变V2原文件或user消息。
- `grpo_bundle.py`：校验V2后导出1000条训练标签及200条公开验证提示，无盲测/验证答案。
- `grpo.py` / `requirements-grpo.txt`：默认预检，同提示基线、LoRA GRPO及训练后推理。
- `test_grpo.py`：本地验包、奖励、门禁与fake trainer衔接；不是GPU训练证据。

## V2本地准备与云端包

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s training/tool_calling -p "test_*.py" -v
.\.venv\Scripts\python.exe -B training/tool_calling/dataset.py build --out outputs/tool-calling-v2
.\.venv\Scripts\python.exe -B training/tool_calling/dataset.py check --data outputs/tool-calling-v2
.\.venv\Scripts\python.exe -B training/tool_calling/dataset.py export-cloud --data outputs/tool-calling-v2 --out outputs/tool-calling-baseline-v2.zip
```

生成目录及zip必须不存在，不覆盖旧50条校准包。已有V2时只执行check。
`review-sample.md`抽取35条训练/验证模板供审核；盲测答案不进入该文件或云端包。
云端包解压后的`python baseline.py`只验SHA、消息表面和200/20数量，不加载GPU。
后续命令、评分、模型与显存限制见[CLOUD_BASELINE.md](CLOUD_BASELINE.md)。
云端基线按分类报告，默认20条只作试跑；只有完整200条才是完整验证集成绩。

模拟搜索采用固定query映射，不模拟排名、真实全文获取、RAG质量、限流或多轮恢复。
environment不读取参考答案或goal；评分器另外比较任务约束和模拟返回目标，不能
因为某个错误参数碰巧返回同一条结果就判对。

## 评分

- +1：动作正确、参数满足任务、授权通过，模拟结果精确满足目标。
- 0：合法且有权限，但工具/参数/决策或结果不符合任务。
- -1：非法JSON/schema、未知工具、越权、捏造候选、额外字段或多个调用。
- 合法但没完成任务不会获得“格式正确”奖励；不按输出字数或调用数量加分。
- 接受JSON键顺序/空白、默认参数、省略数量、DOI规范化、来源/文档数组顺序等价；
  不接受丢失或添加范围。固定查询可规范化大小写和连续空白。
- 所有样本权重暂时相同。常量策略可能在少量无需调用任务上得分，不能只看平均分；
  后续基线必须报告每类任务成功率、越权率、参数错误率和不必要调用率。

## 本地运行

仓库根目录，建议加 `-B` 避免生成pycache：

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s training/tool_calling -p test_offline.py -v
.\.venv\Scripts\python.exe -B training/tool_calling/prepare.py build --out outputs/tool-calling-pilot-v1
.\.venv\Scripts\python.exe -B training/tool_calling/prepare.py check --data outputs/tool-calling-pilot-v1
```

build只允许创建新目录，不覆盖旧文件。已经生成时只执行check。

产物：

- `prompts.jsonl`：只有样本ID和模型messages，没有答案/评分标准/隐藏模拟世界。
- `oracle.jsonl`：私有评分输入，包含reference、goal_ids、world；**不能拼入prompt**。
- `review.md`：供人审核的50条完整任务、权限、预期调用及结果。
- `tools.json`：当前训练schema。
- `self-check.json`：参考答案与错误动作的自检；model_accuracy=null。
- `manifest.json`：数据SHA及五个生产合同文件SHA；生产变更后旧包拒绝校验。

SHA用于发现偶发变动，不是签名或抗恶意篡改机制。oracle是可信训练配置，
只把模型response当作不可信JSON；本包不是通用sandbox。

以后模型生成每条一个原始response，保存为以下JSONL格式（不能用参考答案冒充模型输出）：

```json
{"id":"pilot:001","response":"{\"action\":\"call\",\"tool\":\"search_papers\",\"arguments\":{\"query\":\"固态电池界面\",\"providers\":[\"openalex\"]}}"}
```

```powershell
.\.venv\Scripts\python.exe -B training/tool_calling/prepare.py score --data outputs/tool-calling-pilot-v1 --predictions PATH_TO_MODEL_PREDICTIONS.jsonl
```

必须包含全部50个唯一ID，不丢弃失败行后虚报成功率；超时可记录空response使其失败。
stdout只输出计数/固定code/逐任务分数，不回显原始response。score不会调用模型。

## 再进入训练的门禁

先人工核对任务、参考动作和权限，尤其是多个同名候选、无需调用和澄清边界；
当前所有 `human_reviewed` 均为false，脚本不会自动替人审批。
随后扩充并冻结独立数据集，在本地/云端跑基线，检查奖励是否能区分候选。
如果模型格式错误过多，先评估SFT；若基线已接近满分，或同组候选长期同分，
不要盲目GRPO。基线GPU运行已验收，GRPO入口只完成离线验证，必须先试训再决定是否扩大。
通用JSON决策协议使用Qwen聊天模板，不等同于模型原生tool_call token格式。

最后用独立盲测验证真实改善，再单独决定生产接入；不改全局LLM配置或现有Graph。
本轮不上传数据，不下载公开数据集，不操作Featurize账户，不暂存或提交。

## V3：先同提示基线，再100条GRPO试训

当前数据不再扩增或改标签；复核1000训练/200验证参考动作和错误动作，另检查固定
clarify/finish策略得分。35种训练/验证模板各抽一条做助手语义复核，不等同于人工审批。
盲测200条保持保留，不进入云包或训练、选参、日常评分。

本地准备（无GPU依赖）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -B -m unittest discover -s training/tool_calling -p "test_*.py" -v
.\.venv\Scripts\python.exe -X utf8 -B training/tool_calling/grpo_bundle.py --data outputs/tool-calling-v2 --out outputs/tool-calling-grpo-v3.zip
```

zip必须是新路径，不覆盖旧云端基线包。训练oracle随新包发送给训练评分回调；
**不拼进prompt**。HF训练dataset仅含已渲染的公共prompt及opaque case_id，标签由回调查表。
所有工具都是本地mock，不注册真实工具；无.env、API key、网络检索或自动模型下载。
SHA仅用于误改检测，不是数字签名，也不能防止持有文件的人伪造实验记录。

后续获准使用GPU时，解压到**新目录**，沿用之前本地模型目录，不能覆盖旧 `tool-baseline`。
使用Linux独立虚拟环境，勿继承不匹配的系统torchvision。依赖采用基线已验证的
torch2.9.1/cu128、torchvision0.24.1/cu128、transformers4.57.3，以及固定TRL0.26.0、PEFT0.18.0：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install torch==2.9.1 torchvision==0.24.1 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements-grpo.txt
python grpo.py
```

默认只有预检，不加载模型。不要现在运行以下付费命令；真正开始时使用已有本地snapshot：

```bash
# 1. 新prompt下完整200条baseline，无训练；不是重新解释旧64.5%成绩
CUDA_VISIBLE_DEVICES=0 python grpo.py --phase before --execute \
  --model-dir /home/featurize/tool-baseline/models/Qwen3-8B --out runs/v3-before

# 2. 100条、每模板4条、每题4个候选；单卡每update一组，最多100step/400个候选
CUDA_VISIBLE_DEVICES=0 python grpo.py --phase train --execute --ack-synthetic-data \
  --model-dir /home/featurize/tool-baseline/models/Qwen3-8B --out runs/v3-pilot \
  --baseline-dir runs/v3-before --train-limit 100 --steps 100

# 3. 同prompt/模型snapshot/版本/量化/非思考/greedy条件下生成200条训练后结果
CUDA_VISIBLE_DEVICES=0 python grpo.py --phase after --execute \
  --model-dir /home/featurize/tool-baseline/models/Qwen3-8B --out runs/v3-after \
  --baseline-dir runs/v3-before --adapter runs/v3-pilot/adapter
```

`--ack-synthetic-data`只确认知道数据是合成任务，不代表已人工审核。
`--smoke`只适用于before的20条试跑，不能解锁train/after。每个输出目录必须全新。
训练只用q_proj/v_proj的rank8 LoRA，固定G=4、beta=0.02、dr_grpo、温度0.7；
采样训练不等于greedy基线。先逐条tokenize确保prompt≤8192，不截断超长输入；
输出≤384。奖励同原评分器：+1正确、0合法但错任务、-1非法或越权。
连续32组没有奖励差异或没有正例则停止；不自动扩大样本、换奖励或重试。
记录实际奖励组差异、优化步数、耗时、显存峰值；OOM/异常留失败摘要，禁止自动resume。
失败或不完整的训练输出不能作为after的合法adapter。

把before/after的 `runs` 下载回本地，分别使用原评分器：

```powershell
.\.venv\Scripts\python.exe -B training/tool_calling/dataset.py score --data outputs/tool-calling-v2 --predictions PATH_TO_V3_BEFORE/predictions.jsonl
.\.venv\Scripts\python.exe -B training/tool_calling/dataset.py score --data outputs/tool-calling-v2 --predictions PATH_TO_V3_AFTER/predictions.jsonl
```

比较整体、分类正确率及越权率；V2→V3-before是提示词变化，V3-before→V3-after才是训练比较。
没有充分改善或越权率上升时不扩大；1000条完整训练需另行决定。最终盲测评估入口仍需在
实验方案锁定后单独开放，当前命令有意不开放盲测。不要把验证集成绩当作生产真实性保证。
脚本结束不会退租；保存产物后需在Featurize结束租用。

实现依据：[固定版本TRL接口](https://huggingface.co/docs/trl/v0.26.0/en/grpo_trainer)、
[PEFT量化训练说明](https://huggingface.co/docs/peft/v0.18.0/en/developer_guides/quantization)。
本地fake测试不证明整个CUDA/TRL运行环境可用，下一次GPU任务首先验证导入和短训练。
