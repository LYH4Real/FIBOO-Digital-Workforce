# 生产阶段门与文件合同

用于完整策划与生图任务。只做参考拆解时不强制创建这些文件。所有路径写 MCP 所在机器可读的绝对路径；每轮输出放在任务目录的独立运行文件夹，不覆盖旧版本。

## 推荐目录

```text
task-output/
  00-material-inventory.json
  01-product-facts.json
  01-product-facts-report.json
  02-reference-manifest.json
  02-reference-report.json
  03-plan-v1.json
  03-plan-v1-report.json
  03-plan-v1.html
  04-plan-approval-v1.json
  runs/<run-id>/request.json
  runs/<run-id>/preflight.json
  runs/<run-id>/mcp-result.json
  runs/<run-id>/image-check.json
```

默认交付到 `image-check.json` 和生成图片。只有用户明确要求专项视觉评估时，额外创建 `visual-evaluation-<page>.raw.json`、`visual-review-input.json` 和 `visual-review.json`，不预先生成空评分或通过记录。

## 0. 读取目录

```text
python -X utf8 scripts/workflow_guard.py inventory --root <任务目录> --output <输出目录>/00-material-inventory.json
```

清单只有路径、大小和哈希，不代表已读内容。Agent 随后按清单逐个读取本任务相关文件，并说明已读、不可读、缺失三类结果。已经存在的历史输出也要识别，但不能当成当前事实来源自动复用。

## 1. 产品事实文件

最小结构：

```json
{
  "schema_version": 1,
  "product_scope": "明确产品、口味、规格与适用时间",
  "sources": [
    {"id": "brief-p4", "path": "<task>/brief.pptx", "locator": "第4页", "kind": "file"},
    {"id": "brief-p21", "path": "<task>/brief.pptx", "locator": "第21页", "kind": "file"},
    {"id": "user-20260908", "kind": "user_confirmation"}
  ],
  "facts": {
    "product_name": {
      "status": "conflict",
      "required_for_plan": true,
      "candidates": [
        {"value": "名称A", "source_id": "brief-p4"},
        {"value": "名称B", "source_id": "brief-p21"}
      ]
    },
    "core_claims": {"status": "confirmed", "required_for_plan": true, "value": ["确认表达"], "candidates": []},
    "dosage": {"status": "not_applicable", "required_for_plan": false, "reason": "本篇不出现用量", "candidates": []},
    "packaging": {"status": "confirmed", "required_for_plan": true, "value": {"flavor": "示例"}, "candidates": []}
  }
}
```

运行：

```text
python -X utf8 scripts/workflow_guard.py facts --input 01-product-facts.json --output 01-product-facts-report.json
```

关键字段出现多个候选值时，即使 Agent 把 `status` 误写成 `confirmed`，检查器仍会阻塞。解决冲突需增加：

```json
"resolution": {"value": "用户确认值", "source": "user_confirmation", "evidence": "用户明确回复原文或准确摘录"}
```

## 2. 参考图语义清单

每张原帖图片一项，不能只写文件名或 OCR：

```json
{
  "schema_version": 1,
  "images": [{
    "id": "source-note-a-p01",
    "path": "<task>/reference/01.jpg",
    "sha256": "真实文件哈希",
    "source_group": "note-a",
    "page_index": 1,
    "semantic_status": "verified",
    "inspection": {"method": "multimodal_model", "inspected_at": "ISO-8601时间"},
    "observation": {
      "summary": "画面主体与动作",
      "visible_text": ["实际可读文字；没有则写无可读文字"],
      "composition": "元素数量、位置、占比、裁切与文字层级",
      "shot": "机位、焦距感、景深与透视",
      "lighting": "光源方向、色温、明暗和闪光感",
      "style": "实拍、拼贴、信息图及整体气质",
      "imperfections": "凌乱、噪点、歪斜、污渍或明确没有这些特征",
      "product_or_brand": "可见品牌和产品；没有则明确写无"
    }
  }]
}
```

```text
python -X utf8 scripts/workflow_guard.py references --input 02-reference-manifest.json --output 02-reference-report.json
```

检查器验证真实路径、哈希、逐张观察字段和看图方式。它不能判断描述是否准确，因此 Agent 必须实际打开图片；文件改变后哈希门会强制重看。

## 3. 策划 JSON 与 HTML

策划 JSON 的必要结构：

```json
{
  "schema_version": 1,
  "status": "awaiting_user_approval",
  "task_id": "task-02",
  "title": "内容策划方案",
  "strategy": "本篇解决的问题、发布主体与内容路线",
  "reference_contract": {
    "narrative": "借鉴：……；不继承：……",
    "composition": "严格：……",
    "image_style": "严格：手机随拍……",
    "copy_tone": "借鉴：……；不复制原句"
  },
  "title_candidates": ["标题一", "标题二"],
  "body_copy": "正文成稿",
  "pages": [{
    "id": "task-02-p01",
    "purpose": "本页叙事作用",
    "visual_blueprint": "可观察的构图、机位、光线和真实细节",
    "on_image_text": ["只允许出现的叠加文字"],
    "reference_image_ids": ["source-note-a-p01"],
    "product_image_paths": ["<task>/product/front.png"],
    "product_required": true,
    "prompt": "逐页自包含提示词",
    "size": "1248x1664",
    "ugc_required": true
  }],
  "questions": ["最多三个真正影响作图的问题"]
}
```

```text
python -X utf8 scripts/workflow_guard.py plan --input 03-plan-v1.json --facts-report 01-product-facts-report.json --reference-report 02-reference-report.json --output 03-plan-v1-report.json
python -X utf8 scripts/build_plan_html.py --input 03-plan-v1.json --output 03-plan-v1.html
```

无参考图时省略 `--reference-report`。HTML 生成后停止并等待用户。收到明确确认后创建确认文件，记录 JSON 和 HTML 的 SHA256、`approved_by: user`、`approval_evidence` 与时间；运行 `workflow_guard.py approval` 验证。策划任一文件变化后重新生成 HTML 和确认文件。

## 4. 生图请求版本 2

请求包必须包含：

```json
"planning_context": {
  "facts_input": "<task>/output/01-product-facts.json",
  "facts_report": "<task>/output/01-product-facts-report.json",
  "reference_manifest": "<task>/output/02-reference-manifest.json",
  "reference_report": "<task>/output/02-reference-report.json",
  "plan_approval": "<task>/output/04-plan-approval-v1.json"
}
```

无参考任务省略 `reference_manifest` 和 `reference_report`。有参考时 `reference_context` 同时包含逐页 `visual_strength_by_task`、`source_image_ids_by_task` 和 `source_images_by_task`；图片 ID 与路径必须在语义报告中一一对应。预检会复算产品事实/语义输入的哈希并重新执行两道门，防止报告生成后材料被替换。完整字段见 [MCP 参数合同](image-mcp-contract.md)。

## 5. 文件检查后交付人工审稿（默认）

保存真实请求、逐页参数与 MCP 原始返回后，运行 `image_request_guard.py inspect`，核对文件、格式、数量、实际存储尺寸与比例，输出 `image-check.json`。默认不启动独立 Agent/视觉工具，不裁剪或放大成图作审查，不做七项评分，不执行视觉自动重试。

文件检查通过后交付图片和报告，标记“文件与尺寸检查通过，图像内容待人工审核”。缺图或文件检查失败逐页说明，不能假称全部完成。人工待审不阻塞交付；图像内容、文字、产品准确性、合规和发布状态均未获验证，不得把策划确认或文件检查当成成图验收。

报告中的 `review_mode:human_on_delivery`、`automatic_visual_review:false`、`visual_review_status:pending_human_review`、`publish_ready:false` 描述默认状态。保留的 `visual_review_required:true` 表示内容仍需审核，不是自动触发视觉工具的指令。文件检查器无法证明后续人工审稿已完成，其报告不回填成视觉通过。

## 6. 可选专项独立视觉评估与有限重试

仅用户明确要求专项审图时启用本节及 `visual_review_guard.py`；日常交付不要求这些评估文件，也不要求为了审图每页新建 Agent。专项评估支持独立视觉模型、视觉评估工具或真实人工复核；人工日常审稿不强制套用本节七项评分。

专项范围内每页由独立评估方保存真实原始 JSON。必须实际传入成图、对应原帖图、产品出镜页的真实产品图、策划蓝图、允许文字和已确认事实约束；记录各文件哈希、评估器名称与时间。无参考页可用 `reference_strength: none` 且原帖图数组为空。分数为 0—100：`composition`、`style`、`product_accuracy`、`text_accuracy`、`factual_safety`、`phone_capture_feel`、`commercial_ad_risk`。缺陷项包含 `dimension`、可观察 `evidence` 和下一次 `retry_instruction`，通过时 `findings` 可为空，不要求长篇分析。

严格参考构图≥85、风格≥80；产品、文字、事实准确均须100；要求素人感时手机随拍≥80、商业广告风险≤20。批量评估可减少往返，但每页须保留对应原图、成图、约束和结果。整图优先，专项问题有需要时才局部放大或裁剪辅助；保留原图，不用小缩略拼图判断包装细字。

`visual-review-input.json` 为：

```json
{
  "schema_version": 1,
  "run_id": "唯一运行ID",
  "max_auto_retries": 2,
  "pages": [{
    "id": "task-02-p01",
    "reference_strength": "strict",
    "ugc_required": true,
    "product_required": true,
    "attempt": 1,
    "raw_evaluation": "<task>/output/runs/run-id/visual-evaluation-task-02-p01.raw.json"
  }]
}
```

```text
python -X utf8 scripts/visual_review_guard.py --input visual-review-input.json --output visual-review.json
```

检查器忽略评估器自己写的 pass/fail，按固定阈值计算 `accept`、`retry` 或 `escalate_to_user_or_manual_edit`。它校验方法标签、字段和文件哈希，不能认证记录确实来自独立模型；内容 Agent 自述或自造评分不构成独立评估。没有实际评估能力或原始记录时写“专项视觉未测”，转人工，不伪造分数。

`retry` 只是建议，审图请求本身不授权付费生成。已有视觉返工授权时默认最多2次、最高配置3次，`max_auto_retries:0` 表示不自动返工；只处理失败页与真实可观察差异。到上限转用户或人工，保留每次新请求、原始返回和评估证据；专项评分通过也不等于业务审核或发布获准。
