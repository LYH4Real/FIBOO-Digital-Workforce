"""Render a concise, read-only planning HTML from a validated plan JSON."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import sys

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from workflow_guard import load, validate_plan


def text(value):
    return escape(str(value), quote=True)


def list_items(values):
    return "".join(f"<li>{text(value)}</li>" for value in values)


def render(document):
    validate_plan(document)
    contract = document["reference_contract"]
    page_cards = []
    for index, page in enumerate(document["pages"], 1):
        on_image = page.get("on_image_text", [])
        refs = page.get("reference_image_ids", [])
        product_refs = page.get("product_image_paths", [])
        page_cards.append(f"""
        <article class="page-card">
          <div class="eyebrow">第 {index} 页 · {text(page['id'])}</div>
          <h3>{text(page['purpose'])}</h3>
          <p><strong>视觉蓝图：</strong>{text(page['visual_blueprint'])}</p>
          <p><strong>页面文字：</strong>{text('｜'.join(on_image) if on_image else '无叠加文字')}</p>
          <p><strong>原帖参考：</strong>{text('、'.join(refs) if refs else '从零创作')}</p>
          <p><strong>产品图片：</strong>{text('、'.join(product_refs) if product_refs else '本页不使用')}</p>
          <details><summary>查看生图提示词</summary><pre>{text(page['prompt'])}</pre></details>
          <div class="size">{text(page['size'])}</div>
        </article>""")
    questions = document.get("questions", [])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{text(document.get('title', '小红书内容策划方案'))}</title>
<style>
:root{{--bg:#f6f3ee;--card:#fffdfa;--ink:#24201d;--muted:#766d65;--accent:#a94f5d;--line:#e8ded5}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:960px;margin:auto;padding:42px 22px 72px}} .hero{{padding:34px;border-radius:22px;background:#2d2926;color:white}}
.hero p{{color:#ddd4cc}} h1{{margin:.1em 0;font-size:clamp(28px,5vw,46px)}} h2{{margin-top:42px}} .eyebrow{{font-size:13px;color:var(--accent);font-weight:700;letter-spacing:.08em}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}} .page-card,.section{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px}}
.page-card{{position:relative}} .page-card h3{{margin:.25em 0 .7em}} .size{{position:absolute;top:18px;right:18px;color:var(--muted);font-size:13px}}
pre{{white-space:pre-wrap;background:#f1ece6;padding:14px;border-radius:12px;font:13px/1.6 ui-monospace,monospace}} summary{{cursor:pointer;color:var(--accent)}}
.contract{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px}} .contract div{{padding:14px;border-left:4px solid var(--accent);background:white;border-radius:8px}}
.notice{{margin-top:28px;padding:16px;border:1px dashed var(--accent);border-radius:14px;color:var(--muted)}}
</style></head><body><main>
<section class="hero"><div class="eyebrow">等待用户确认 · 尚未开始生图</div><h1>{text(document.get('title', '小红书内容策划方案'))}</h1><p>{text(document['strategy'])}</p></section>
<h2>参考合同</h2><section class="contract">
<div><b>叙事逻辑</b><br>{text(contract['narrative'])}</div><div><b>逐页构图</b><br>{text(contract['composition'])}</div>
<div><b>图片风格</b><br>{text(contract['image_style'])}</div><div><b>文案语气</b><br>{text(contract['copy_tone'])}</div></section>
<h2>标题候选</h2><section class="section"><ol>{list_items(document['title_candidates'])}</ol></section>
<h2>正文</h2><section class="section"><p>{text(document['body_copy']).replace(chr(10), '<br>')}</p></section>
<h2>逐页分镜</h2><section class="grid">{''.join(page_cards)}</section>
<h2>确认前仍需回答</h2><section class="section"><ol>{list_items(questions) if questions else '<li>无；请确认是否按本方案进入生图。</li>'}</ol></section>
<div class="notice">本页只用于审阅策划，不含修改控件。请直接在与 Agent 的对话中指出标题、正文、页序、参考程度或画面要修改的地方；收到明确确认后才会调用生图 MCP。</div>
</main></body></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    destination = Path(args.output)
    if destination.exists():
        raise FileExistsError("策划 HTML 已存在；请新建版本，不能覆盖已审阅文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(load(args.input)), encoding="utf-8", newline="\n")
    print(destination)


if __name__ == "__main__":
    main()
