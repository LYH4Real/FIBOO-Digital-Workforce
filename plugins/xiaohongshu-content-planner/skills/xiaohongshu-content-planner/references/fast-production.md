# 单篇快速生产

适用：已完成产品预处理、源文件仍有效的标准参考图任务。每页均有原帖参考，使用 `edit_batch_images`。无参考、特殊工具或需要新增产品时走原阶段合同，不强行套用。

以下命令中的 scripts 路径以 Skill 安装目录为基准。TRAE 当前工作目录通常是用户的任务文件夹：先定位实际 SKILL.md，在执行时改用带引号的脚本绝对路径；全部输入/输出路径也加引号。尖括号表示待替换项，不直接传给终端。

## 1. 开始计时，读取本次需求

任务开始先运行计时器；有效产品包用 warm，当次重新准备资料用 cold，单独产品维护用 prep：

```text
python -X utf8 scripts/task_timing.py begin --output-root <任务输出>/timing --mode warm
```

保留返回的 journal 路径。计时不是后台自动追踪：阶段切换必须 mark，结束必须 finish；未记录的等待不能事后捏造扣除。每个参考笔记/会话使用独立输出目录，避免并发写同一 plan/ref_p1。

计时标记尽量与本阶段已有本地操作放在同一次终端工具调用，不为一个 mark 单独增加模型往返。每个阶段标一次即可，不按页反复标记或频繁轮询 report；依赖命令成功后再执行下一步，不忽略前一步错误。

只枚举明确的本次 brief/参考图等输入；产品文件由缓存核对，历史成图不是新事实。首次先看目录层级再定范围，不盲目排除未知文件：

```text
python -X utf8 scripts/workflow_guard.py inventory --root <本任务目录> --include <brief文件> --include <参考图子目录> --output <任务输出>/00-material-inventory.json
python -X utf8 scripts/prepare_task.py context --cache <产品包>/cache.json --output-root <任务输出>/contexts
```

context 返回精简 `task-context.md` 和机器可读 `task-context.json`。先读摘要，按本篇需要查询其他事实和产品图语义，不再逐张上传未变的产品照片。已知要写用量时可加 `--required-facts product_name core_claims packaging dosage`。复算源文件哈希是本地校验，不是重新让模型看图。

实际阅读本次 brief；与产品包矛盾先问用户，不以缓存覆盖。新参考图逐张实际查看，按阶段合同生成 `02-reference-manifest.json`；本次已经成功提取的文字/图片观察直接复用，不再次写临时提取脚本。缓存变化先更新产品包，不能只改哈希假装读过。

## 2. 只创作一份策划，程序派生其他文件

参考合同仍先明确四维。如果本次旧 brief 的最低植入页数与用户当前“按每篇需要安排”冲突，采用当前明确要求并记录来源，不是自动删掉所有产品要求。

```text
python -X utf8 scripts/task_timing.py mark --journal <journal> --stage planning
```

按相关文案/视觉方法一次写成策划草稿，使用[策划 JSON 格式](workflow-gates.md)，额外加入 `fact_keys_used` 数组，列出本篇实际用到的产品字段，如 `["product_name", "core_claims", "packaging", "dosage"]`。需要的数据缺失先问用户。每页提示词仍须自包含、明确图序与参考强度。

需要指定 model/response_format 时，在策划根或页字段中写明并一起获批，不等批准后用命令参数换模型。保留计划原页数，不为测速删页。

```text
python -X utf8 scripts/prepare_task.py plan --context <task-context.json> --inventory <00-material-inventory.json> --reference-manifest <02-reference-manifest.json> --plan <策划草稿.json> --output-root <任务输出>/plans
```

一次生成唯一目录内的事实/参考报告、策划 JSON、报告和只读 HTML。返回 `paths` 是后续命令的定位依据，不从其他版本拼接路径。脚本不批准、不生图、不识别图片或判断事实真假；本次材料、产品缓存和参考绑定到策划，源文件变化后旧批准不能进入生成。

交付 `paths.plan_html` 并停止：

```text
python -X utf8 scripts/task_timing.py mark --journal <journal> --stage awaiting_user
```

只有真正等用户时使用 awaiting_user；平台超时/故障用 blocked，不能冒充等待用户扣时间。用户修改回 planning，创建新 bundle，不覆盖原策划，也不自动生成“批准”。

## 3. 批准后，编译请求、作图和文件检查

收到明确批准后按阶段合同保存原话与确认记录，指向本次 `paths.plan` / `paths.plan_html`。计时切 generation，再按[执行效率](execution-efficiency.md)运行 `prepare_generation.py`，采用同一 bundle 中的 facts/refs/report 路径。把预检返回的实际 MCP 参数原样调用工具，不再改写。

首轮先单任务测试，默认单批并发3、传输重试0，不是同时启动多篇各3并发。完成后切 validation，运行 `image_request_guard.py inspect` 核对文件、格式、数量与真实像素/比例，然后交付图片由用户人工审稿。默认不启动独立 Agent/视觉工具，不制作审查裁剪或放大图，不做七项评分或视觉自动重试；人工待审不阻塞交付。

用户明确要求专项审图时，才按[专项评估合同](workflow-gates.md#6-可选专项独立视觉评估与有限重试)执行；可批量评估，每页保留对应原图、成图、约束和结果。专项评估与返工耗时另外如实记录，不并入“文件检查”后宣称是同一验收范围。

有用户返工授权时切 rework，只编译失败页；改产品事实或已批准内容时重新策划批准。成功页保留，下载问题先寻找已返回图片，不直接重新付费生成。本 Skill 不提供云端调用强制中断、全局并发控制或下载恢复服务。

```text
python -X utf8 scripts/task_timing.py mark --journal <journal> --stage delivery
python -X utf8 scripts/task_timing.py finish --journal <journal> --outcome partial --pages <实际交付页数>
```

默认图片交付、人工审核待完成时使用 partial，并说明“生成交付完成，内容待人工审核”；缺页也用 partial，失败用 failed。只有请求范围及所需审核均完成时才可用 complete；它仍只是执行者声明，不证明质量或可发布。交付图片、真实尺寸和文件检查报告；未执行专项评估时不创建评分报告。计时器的 `quality.status:unverified` 与 `quality_and_time_acceptance:unverified` 不得改写成质量通过。

## 十分钟怎么评

十分钟目标按“材料读取、策划与HTML、生图、文件与尺寸检查、交付待审图片”的默认生产范围实测，不沿用含独立验图的旧耗时承诺。等待用户单列，同时报告用户看到的总耗时。交付后的人工审稿不在该轮生产计时内，须明确仍待完成；如用户另要求专项审图，另行计时并标明范围。返工、平台等待和错误计入主动处理时间，失败任务不从总体统计剔除。

分别报告 prep/cold/warm，不隐去产品准备成本。计时器跨进程核对时钟，不能证明可比时标记估算；时间阈值不等于质量验收或真实性证明。首次对照用同一7页任务、相同模型和可比负载，记录实际耗时、文件检查失败率、重试数和人工待审状态；不同审核范围分别报告，离线单元测试不是 TRAE 十分钟实测。
