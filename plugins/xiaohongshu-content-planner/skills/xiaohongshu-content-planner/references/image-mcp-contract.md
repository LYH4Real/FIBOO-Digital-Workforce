# 生图 MCP：请求前检查与结果尺寸验收

本合同对应已核对的 **Waveeee Image MCP 0.4.2**：`server.py` 的工具定义、`client.py` 的尺寸验证与真实发送字段、`batch.py` / `edit_batch.py` 的批量执行与日志。换服务或升级后先读取实际 `tools/list` / `server_info`，出现差异时更新合同，不能套用不兼容参数。

## 尺寸要进入参数，不只进入提示词

- 每张请求都显式填写 `size`，包括批量中的**每个 tasks 项**。批量顶层没有 `size`；写到那里不会代替逐项设置。
- 未给其他明确选择时，小红书 3:4 竖版可建议 `1248x1664`；它是项目建议，不是服务唯一合法值。用户明确选择横版、方图或其他尺寸时尊重该选择。
- 现有服务允许 `WIDTHxHEIGHT`：宽高均大于 0、是 16 的倍数、每边严格小于 3840，总像素在 655360–8294400（含边界）之间，长短边比例不超过 3:1。例如 `1440x1920` 也合法；`1254x1254` 不是合法请求尺寸。
- `prompt: "3:4 竖图"` 或文档预览比例不能代替 `size`，MCP 返回中的 `size` 也不是实际图片像素证明。

## 工具与参数的真实对应

| 用途 | 工具名 | 图片参数所在位置 |
|---|---|---|
| 无参考图，单张生成 | `generate_image` | `arguments.prompt`、`size`、可选 `model/n/response_format/filename/output_subdir` |
| 有实际参考文件，单张编辑 | `edit_image` | 同上，增加 `arguments.image_paths` |
| 无参考图，批量生成 | `generate_batch_images` | `arguments.tasks[]` 内各自的 `id/prompt/size/model/n/response_format` |
| 有实际参考文件，批量编辑 | `edit_batch_images` | 同上，增加每项各自的 `image_paths` |

批量公共参数只有 `tasks`、可选 `concurrency`（1–16）、`retries`（0–5）、`output_subdir`。每批 1–100 个任务。单项 `n` 为 1–4，`response_format` 为 `auto`（默认）、`url` 或 `b64_json`，不是图片文件格式。`auto` 或省略时不向上游发送该字段；另外两种值显式发送。保存时始终按实际返回的 `data[].url` 或 `data[].b64_json` 自动处理，并根据真实图片内容选择扩展名。已有接口响应需要恢复本地图片时，使用 `save_image_response`，不要为了保存 Base64 或取回图片重新生图；该保存工具和 `server_info` 不属于本预检的四个生成/编辑工具。

`image_paths` 是 **MCP 所在机器上可读取的 1–10 个绝对文件路径**，不是网页链接、文件名、Markdown 图片标签或远程 URL。先取得用户提供/授权使用的参考文件，再调用编辑工具。原笔记构图参考、产品包装参考、粉体质感参考应在提示词中按传入顺序注明各自角色，不能只把产品图当作所有参考。用户提供过参考图文时，每个任务至少一张原帖图必须实际进入 `image_paths`，即使该页只做轻度参考。参考策略见 [image-workflow.md](image-workflow.md)。

## 可复用请求包

下面是结构样例；示例路径不是业务资料，执行前必须换成当前机器上的真实文件。封装层 `schema_version/run_id/tool/expectations/reference_context/planning_context` 不传入 MCP，只有 `arguments` 才是工具参数。完整生产请求使用 `schema_version: 2`；旧版 1 没有策划确认门，当前检查器会拒绝。

```json
{
  "schema_version": 2,
  "run_id": "20260907-153000-a1b2",
  "tool": "edit_batch_images",
  "arguments": {
    "output_subdir": "runs/20260907-153000-a1b2",
    "concurrency": 1,
    "retries": 0,
    "tasks": [
      {
        "id": "task-01-p01",
        "prompt": "【参考强度】严格参考。3:4竖版。严格还原参考图1的三张左侧生活小图、右侧手持产品、主体占比、文字层级与自然光；参考图2锁定目标产品包装。顶部叠加文字仅为：细节，分开看。不得迁移参考图1的品牌、卖点或水印。",
        "image_paths": ["<task>/references/layout.jpg", "<task>/references/product.png"],
        "size": "1248x1664",
        "n": 1,
        "response_format": "auto"
      }
    ]
  },
  "expectations": {
    "task-01-p01": {"ratio": "3:4", "size": "1248x1664"}
  },
  "reference_context": {
    "provided": true,
    "visual_strength_by_task": {"task-01-p01": "strict"},
    "source_image_ids_by_task": {"task-01-p01": ["source-note-a-p01"]},
    "source_images_by_task": {
      "task-01-p01": ["<task>/references/layout.jpg"]
    }
  },
  "planning_context": {
    "facts_input": "<task>/task-output/01-product-facts.json",
    "facts_report": "<task>/task-output/01-product-facts-report.json",
    "reference_manifest": "<task>/task-output/02-reference-manifest.json",
    "reference_report": "<task>/task-output/02-reference-report.json",
    "plan_approval": "<task>/task-output/04-plan-approval-v1.json"
  }
}
```

示例用 `concurrency:1/retries:0` 便于首图排查，不是所有生产批次必须串行或禁止合理重试。生产并发与重试由用户授权范围和服务状态决定。`model` 省略时使用 MCP 配置；没有核实配置就不要声称实际用了某模型。

单图使用同一外层结构，另外填 `"id":"task-01-p01"`，并将该页的 `prompt/image_paths/size/...` 直接放到 `arguments`（没有 `tasks`）。`filename` 可设为该稳定 ID。单图不强制 `output_subdir`，但推荐同样使用独立运行目录；默认目录可能已有历史同名文件，最终路径以工具返回为准。

文件名会被服务端转换：单图先用 `Path.stem` 去掉最后一个扩展名，再执行 `safe_filename`；批量直接对 ID 执行 `safe_filename`。服务清洗非法字符、去除首尾点和下划线，并给 Windows 设备名加前缀；返回多张时另加 `-1`、`-2` 等序号。0.4.2 按真实图片字节使用 `.png`、`.jpg`、`.webp` 或 `.gif`，再以独占创建方式写入；目标已存在时增加 `-2`、`-3` 等避让序号，不覆盖旧图片。预检仍计算 `predicted_output_stems` 并拒绝批内同名映射（例如 `cover` / `cover_`、`Cover` / `cover`），这是便于留档追溯的额外项目规则；同时拒绝 Windows 设备名等不明确命名。预测只包含文件名主体，不保证最终扩展名或避让序号。单图 `cover.png` 与 `cover.jpg` 经过清洗可能具有相同主体，必须保存并使用真实返回路径，不能自行拼接 `.png`。

`expectations` 可省略；填写时只能包含本次请求的 ID，`ratio`/`size` 来自用户已确认选择，不从历史稿中的自动默认值冒认已确认。ID 使用稳定的字母数字、点、下划线、短横线，例如 `task-01-p01`；批量每页各一个 ID 且不重复。`run_id` 每次实际调用都新建，重试目录可为新 run，页 ID 保持对应关系。

`reference_context` 必填，用来防止有参考任务静默退化。没有任何参考图文时只写 `{"provided":false}`。用户提供过参考时，写 `provided:true`，并用 `visual_strength_by_task` 为每个 ID 填 `strict`、`moderate` 或 `light`，用 `source_image_ids_by_task` 和 `source_images_by_task` 一一对应列出该页原帖图。检查器要求使用编辑工具、提示词有对应的中文参考强度标记、图片 ID 与已验证语义清单路径一致，且原帖图实际进入该页 `image_paths`。产品图不能冒充原帖图。

`planning_context` 必填：`facts_input` 与 `facts_report` 必须成对提供，`plan_approval` 必须证明用户确认了当前策划 JSON 和 HTML 的哈希；有参考时还必须成对提供 `reference_manifest` 与 `reference_report`。请求预检会复算输入哈希并重新运行产品事实/参考语义检查，任何缺失、冲突、路径错配、材料变化或策划变更都阻断生图。文件格式见 [生产阶段门](workflow-gates.md)。

## 请求前：运行预检，然后准确调用工具

```text
python -X utf8 scripts/image_request_guard.py preflight --input request.json --output preflight.json
```

如果整批确实都已确认 3:4，可加 `--strict-ratio 3:4`。混合横竖图时用各 ID 的 `expectations`，不要添加统一竖版限制。

预检会检查实际工具字段、每项非空提示词和显式合法 `size`、数量/ID、实际输出名碰撞、参考图路径、预期尺寸/比例冲突。有参考时还会阻止生成工具、缺少逐页原帖图或提示词强度标记。显式填写 `ratio:null` 等无效值会被拒绝，不等于省略。缺尺寸、缺原图或合同冲突时退出非零，**不要直接带病调用或替用户改需求**。通过后，读取 `preflight.json` 的 `mcp_call.name` 与 `mcp_call.arguments`，将它们原样用作工具名和参数；不要把整个预检报告传给 MCP，也不要在重新手写调用时漏掉 `size` 或参考文件。`page_request_records` 从这份真实调用逐页抽取 ID、任务参数和公共参数，作为逐页真实请求留档；不允许事后另写一套提示词冒充请求记录。

预检本身是离线操作，不生成图片、不消耗生图额度、不代表用户已授权付费生成。生成前仍需用户已确认相应内容和本次生成范围；输出格式/尺寸变化后应重新核对。

## 结果后：读真实文件，而不是相信“成功”字样

调用后把**真实完整返回**另存为 `mcp-result.json`。可以是 MCP 的 `structuredContent`、整个含 `content` 的工具结果或 JSON-RPC 包装；不要根据自己期望的输出编造返回对象。单图工具只返回 `images/size/...`，没有 `manifest.jsonl`，不要虚构单图 manifest。

```text
python -X utf8 scripts/image_request_guard.py inspect --request request.json --result mcp-result.json --output image-check.json
```

脚本按稳定 ID 对齐批量结果；检查每张实际本地文件的类型、容器元数据和存储宽高，报告 `pixels_match` 与 `ratio_match`，并验证数量及成功/缺失/重复结果。它识别 PNG、JPEG、静态 WebP 的真实文件头，不依赖扩展名；未知格式、GIF、动画 WebP、明显截断、无法读出的尺寸不能通过。服务能保存某种格式不代表本项目元数据检查支持该格式。

- **退出 0**：本阶段预检或元数据尺寸检查通过。
- **退出 1**：真实结果缺失、失败、数量或尺寸/比例不一致，报告中有逐项原因。
- **退出 2**：输入/协议无效、预检不通过或文件读写失败。

即使比例正确，`1440x1920` 也不等于请求的 `1248x1664`：报告保留比例通过、精确像素不通过的区别。若业务愿意接受同等比例的不同像素，由用户明确接受该差异并记录；不能修改旧请求冒充本来就请求了这个尺寸。

报告保留 `full_decode_performed:false`、`visual_review_required:true`，并声明 `review_mode:human_on_delivery`、`automatic_visual_review:false`、`visual_review_status:pending_human_review`、`publish_ready:false`。`ok:true` 只表示文件、格式、数量和存储尺寸检查通过；`visual_review_required:true` 表示仍需内容审核，不要求自动调用视觉工具。这不是完整图像解码、坏图检测或视觉验收。

默认检查后直接交付图片供用户人工审稿，不启动独立 Agent 或视觉工具、不做审查裁剪/放大、不评分或视觉自动重试。人工待审时仍可交付，但不能宣称图像内容、合规或发布验收通过。仅用户明确要求专项审图时才按[专项评估合同](workflow-gates.md#6-可选专项独立视觉评估与有限重试)使用独立评估记录及 `visual_review_guard.py`；该脚本不是默认交付前置条件。JPEG 的编码宽高不含 EXIF 自动旋转，显示方向留给人工或所请求的专项评估核对。失败图片不自动裁切、拉伸或冒充通过。

## 留存与覆盖风险

每次调用保存一组独立材料：`request.json`（准确实际请求）、`preflight.json`、原始 MCP 返回、`image-check.json`、该次产图。报告文件由脚本新建，已有同名文件会拒绝覆盖。

现有批量服务用写入模式重建 `manifest.jsonl`，同一输出目录再次调用可覆盖旧日志。因此本项目批量预检额外要求 `output_subdir` 是含完整 `run_id` 路径段的相对子目录，不含 `..` 或盘符。这是**本项目留存规范，不是 MCP 原生必填条件**。该检查只验证路径形式，无法证明服务端从未用过此目录；调用前还要核对目标目录并使用新的运行标识。服务源码和已有历史日志不由本脚本修改。

输出名预测按请求 `n` 推算；实际服务按返回 `data` 数量决定多图序号、按图片内容决定扩展名，并对已存在文件增加避让序号。若数量异常，仍以结果检查发现的问题为准。此预检不证明输出目录可写、磁盘空间足够或最终路径与预测相同；单图服务在生成后才写本地文件，调用前应先确认真实输出目录可用，防止已生成却保存失败。
