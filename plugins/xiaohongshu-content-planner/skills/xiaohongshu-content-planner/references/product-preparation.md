# 产品预处理包：一次读取，按材料版本复用

## 它解决什么，不解决什么

产品预处理包是本机文件包，不是知识库服务、数据库、检索服务或新 Agent。把已实际读取的产品事实、已实际查看的产品图观察记录和材料清单绑定在同一个版本中；后续篇目先校验文件是否变化，再复用未变化材料的既有语义记录，减少重复读取产品文档和产品图片的等待。

`product_cache.py` 完全离线，只校验和打包已有记录：不提取产品事实、不识别图片、不自动确认冲突、不调用模型/MCP、不生图、不批准方案。校验成功只能说明记录符合约定且来源文件版本未变，**不能证明记录真实、有人确实看过图片或人类确实作出过确认**。业务真实性仍由实际读取、视觉观察和用户确认负责，禁止把脚本结果写成“视觉检查通过”。

产品事实与单篇 campaign brief 分离：

- 产品包：明确产品/规格/包装版本的名称、允许表达、使用方法、包装及产品图片语义。缺失、冲突和适用范围也应保留。
- 单篇 brief：这次给谁看、表达目的、场景、叙事、产品露出程度、标题正文、参考程度、每页设计与审批。不写进通用产品包。
- 竞品/原帖参考图及本次生成结果不因产品包存在而免读。需要新参考拆解、严格参考对比和成图质检时仍执行原流程。

同名文件内容变了、目录新增资料、删除旧资料，都会使旧包失效；不按 mtime 猜测文件是否未变。文件日期较新也不是事实优先级或用户确认的替代品。

## 推荐目录与准备顺序

命令里的 scripts 相对于本 Skill 安装目录，不是业务工作目录。执行时使用带引号的脚本绝对路径，中文或含空格的材料路径也加引号；不要在任务文件夹复制/重写同名脚本。

以下路径仅为示例，替换成用户授权的真实材料范围。

```text
<product-work>/
  materials/                 # 明确允许复用的产品来源，按目录纳入才会检测新文件
    product-info.txt
    product-front.png
    confirmations.txt         # 有冲突时保存真实用户确认的可追溯记录
  records/                   # 整理后的三个输入 JSON，放在材料扫描范围外
    facts.json
    assets.json
    inventory.json
  packages/                  # 新建版本包，绝不能落入未排除的材料扫描范围
```

1. 读取用户明确指定的材料。确认产品、口味/规格和包装适用范围；不得从旧案例或文件名继承事实。
2. 实际查看要复用的产品图，记录外观和拍摄语义。OCR 只辅助确认可见文字，不能代替对构图、外观和图片用途的观察。
3. 整理 `facts.json`、`assets.json`。关键事实冲突先停下来向用户合并提问；把真实确认记录保存在授权材料目录，并保留文件与消息/段落定位。不得由 Agent 自造“用户已确认”。
4. 材料整理结束后创建目录清单。目录扫描应包含实际原始资料和确认证据，不包含生成结果、历史输出、缓存或无关目录：

```powershell
python scripts/workflow_guard.py inventory --root <product-work>/materials --include . --output <product-work>/records/inventory.json
```

如果需要排除材料目录内的输出，显式加 `--exclude outputs`，可重复指定；不能自动隐藏新文件或根据文件名猜排除。只 include 某一个文件，便只能检测该文件的变化/删除，不能声称监控其兄弟文件新增。清单的 `scope.include`、`scope.exclude` 会完整保留到包内。

5. 打包。输入路径必须为本机绝对文件路径；输出使用新唯一子目录，重复 build 不覆盖旧包：

```powershell
python scripts/product_cache.py build --facts <product-work>/records/facts.json --assets <product-work>/records/assets.json --inventory <product-work>/records/inventory.json --output-root <product-work>/packages
```

命令返回精简 JSON，含 `cache_path`、`context_path`、事实与图片数量和警告，不把整份资料打进工具输出。生成目录包含 `cache.json`（完整原始记录）和 `product-context.md`（有长度限制的摘要）。摘要不代表完整资料或事实批准书。

6. 每次复用前 `check`，或让调用方使用 `load_valid_cache`；不能只读摘要直接跳过版本检查：

```powershell
python scripts/product_cache.py check --cache <product-work>/packages/product-cache-实际目录名/cache.json
```

检查重新按原 include/exclude 范围扫描、逐文件计算 SHA256 并比较路径/大小/哈希；同路径、同大小且 mtime 未变的内容修改也会阻断。还会校验三个原始 JSON 的字节哈希与包内对应记录一致。只改 JSON 排版或换行也会失效，这是有意采取的保守版本约束。返回码 0 表示本次记录与新鲜度校验通过，2 表示阻断；不会发起付费服务。

## facts.json 最小结构

沿用 `workflow_guard` 产品事实结构，增加可复用所需的明确来源定位。以下内容均为格式占位，必须替换为已读取的真实材料，不可当成产品事实。

```json
{
  "schema_version": 1,
  "product_scope": "示例产品 / 指定规格 / 当前包装",
  "sources": [
    {"id": "product-info", "kind": "file", "path": "<product-work>/materials/product-info.txt", "locator": "产品名称段落及第2节"}
  ],
  "facts": {
    "product_name": {"status": "confirmed", "required_for_plan": true, "value": "已确认的示例名称", "source_id": "product-info"},
    "core_claims": {"status": "missing", "required_for_plan": false},
    "dosage": {"status": "missing", "required_for_plan": false},
    "packaging": {"status": "missing", "required_for_plan": false}
  }
}
```

- 保留 `product_name`、`core_claims`、`dosage`、`packaging` 四个关键字段；不是要求为未知信息编造值。
- 状态：`confirmed`、`missing`、`conflict`、`not_applicable`。不适用应附 `reason`。`required_for_plan` 是已有事实门的必需约束，不能为绕过冲突/缺失而降级。
- 所有来源都必须有唯一 `id`、本机绝对 `path`、非空 `locator`，且来源文件实际存在并被清单纳入。多个字段需要不同定位时，可登记同一文件的不同来源 id。
- 每个可用事实（`confirmed` 或有有效 resolution）都必须有 `source_id`；解决过冲突的事实使用 `resolution.source_id`。其值不得为空。脚本不验证所引文字是否支持该值，实际读取者须负责核实。
- `missing`、`not_applicable` 以及可保留的非关键、非必需未解决冲突不进入可用事实摘要；后续篇目需要它们时先补材料/提问。关键冲突无论是否必需都阻断，其他必需冲突也阻断。
- 原始产品事实 JSON 不得已有 `cache_binding`，避免缓存递归。单篇任务的绑定由调用方在副本上附加，不能修改包内事实。

### 冲突解决记录

先把所有候选值与来源保留在 `candidates`。真实用户确认之后，在 sources 增加可查证文件记录，例如：

```json
{"id": "user-confirmation-01", "kind": "user_confirmation", "path": "<product-work>/materials/confirmations.txt", "locator": "2026-09-08 的第3条消息，确认名称适用范围"}
```

对应字段示例：

```json
{
  "status": "conflict",
  "required_for_plan": true,
  "candidates": [
    {"value": "资料中的旧名称", "source_id": "product-info"},
    {"value": "用户确认的新名称", "source_id": "user-confirmation-01"}
  ],
  "resolution": {
    "value": "用户确认的新名称",
    "source": "user_confirmation",
    "source_id": "user-confirmation-01",
    "evidence": "用户明确确认新名称的适用范围，原文见所引消息"
  }
}
```

仅有 `evidence: "用户已确认"` 不够；必须引到清单内的真实文件与定位。若字段同时有顶层 `value`，它必须等于 `resolution.value`，不能让两个冲突值一起进入快路径。`missing/not_applicable` 与 resolution 同时存在也须先消除记录矛盾。代码只检查这些记录和文件哈希，不能鉴定消息作者或保证确认真实发生；审批真实性仍不能交给 Agent 自证。

## assets.json 最小结构

没有产品图可以使用：

```json
{"schema_version": 1, "product_scope": "示例产品 / 指定规格 / 当前包装", "images": []}
```

这是“只有可复用文字事实”的包，输出会明确警告；后续产品出镜页必须先补图、实际查看、更新清单并重新建包。不能为满足格式从竞品图推导本产品外观。

有图片时，每张沿用参考图语义记录结构，并增加 `role` 和 `product_scope`：

```json
{
  "schema_version": 1,
  "product_scope": "示例产品 / 指定规格 / 当前包装",
  "images": [{
    "id": "product-front",
    "path": "<product-work>/materials/product-front.png",
    "sha256": "替换为该原始图片文件真实SHA256",
    "role": "pack_front",
    "product_scope": "示例产品 / 指定规格 / 当前包装",
    "semantic_status": "verified",
    "inspection": {"method": "human_confirmed", "inspected_at": "2026-09-08T00:00:00Z"},
    "observation": {
      "summary": "实际看到的包装外观及该图可用于什么",
      "visible_text": "实际能辨认的文字；看不清时如实记录不可辨认",
      "composition": "实际观察到的构图",
      "shot": "实际观察到的角度与景别",
      "lighting": "实际观察到的光照",
      "style": "实际观察到的图片风格",
      "imperfections": "实际观察到的瑕疵；没有明显瑕疵时如实记录"
    }
  }]
}
```

`inspection.method` 只接受 `multimodal_model`、`vision_tool`、`human_confirmed`，应如实填写实际执行者。`filename_only`、`ocr_only` 不合格。每项观察都需具体记录，禁止用模板占位伪装已看图。这里的 `verified` 是输入记录的声明，打包器只查结构，不会重新认图或产生新的视觉通过结论。

图片要求 PNG/JPEG/WebP 文件签名、实际文件哈希一致、路径已被 inventory 纳入、id 与实际路径不重复。`role` 为非空用途（如包装正面、背面标签、实物使用状态），不限定产品类别。根与各图片的 `product_scope` 必须等于 facts 的产品范围；不同规格/包装应独立建包，避免混用。

## cache.json 与代码接口

```text
schema_version: 1
kind: product_cache
created_at: UTC 时间
product_scope: 与 facts 完全一致
facts: 输入 facts.json 的完整 JSON，禁止已有 cache_binding
assets: 输入 assets.json 的完整 JSON
inventory: 输入 inventory.json 的完整 JSON，包含 root/scope/files
input_artifacts:
  - kind: product_facts      path: 原始 facts 绝对路径       sha256: 字节哈希
  - kind: product_assets     path: 原始 assets 绝对路径      sha256: 字节哈希
  - kind: material_inventory path: 原始 inventory 绝对路径   sha256: 字节哈希
validation_scope: 只验证记录与版本，不证明语义/用户确认真实性
```

`build_cache(facts_path, assets_path, inventory_path, output_root)` 返回精简结果及 `cache_path`；`load_valid_cache(cache_path)` 成功才返回完整 cache document，失败抛异常并阻止继续。不得捕获校验错误后回退为“相信旧摘要”。一篇任务完成产品包校验后按需使用记录，避免在同一准备步骤中额外重复手工扫描、复读全部事实和图片；跨关键执行阶段仍保留输入版本检查。

包是版本快照，不是防篡改数字签名。来源记录、原始 JSON 和旧包都应保留可追溯版本。源文件新增/删改、产品图替换、确认内容改变或资料位置迁移后，应实际读取受影响内容，重新处理冲突/语义并重新 inventory + build，不能只刷新哈希掩盖旧观察。涉及改名、旧包装延用等情况应明确适用范围后更新事实，而不是自行认定哪份资料优先。

新包不会修改已批准的篇目、提示词、参考图顺序或生图数量；既有审批若绑定旧输入，应由篇目流程重新确认，不得自动迁移审批。
