"""UTF-8 JSON and human-readable Markdown export."""

import json
from pathlib import Path


def _value(value) -> str:
    return "未提供" if value is None else str(value)


def to_markdown(note: dict) -> str:
    author = note.get("author") or {}
    stats = note.get("stats") or {}
    lines = [f"# {note.get('title') or '无标题笔记'}", ""]
    for warning in note.get("warnings") or []:
        lines.extend([f"> 提示：{warning}", ""])
    lines.extend([
        f"- 作者：{_value(author.get('nickname'))}",
        f"- 笔记 ID：{note['note_id']}",
        f"- 类型：{_value(note.get('type'))}",
        f"- 发布时间：{_value(note.get('published_at'))}",
        f"- 更新时间：{_value(note.get('updated_at'))}",
        f"- IP 属地：{_value(note.get('ip_location'))}",
        f"- 点赞：{_value(stats.get('likes'))}；收藏：{_value(stats.get('collects'))}；评论：{_value(stats.get('comments'))}；分享：{_value(stats.get('shares'))}",
        f"- 来源：<{note['source_url']}>",
        f"- 抓取时间：{_value(note.get('fetched_at'))}", "", "## 正文", "",
        note.get("description") or "（页面未提供正文）", "",
    ])
    tags = note.get("tags") or []
    if tags:
        lines.extend(["## 话题", "", " ".join("#" + str(t.get("name") or "") for t in tags), ""])
    if note.get("images"):
        lines.extend(["## 图片", ""])
        for i, item in enumerate(note["images"], 1):
            if item.get("url"):
                lines.extend([f"![图片 {i}]({item['url']})", ""])
    if (note.get("video") or {}).get("urls"):
        lines.extend(["## 视频地址", ""])
        lines.extend(f"- <{url}>" for url in note["video"]["urls"])
        lines.append("")
    return "\n".join(lines)


def serialize(note: dict, format: str = "json") -> str:
    if format == "md":
        return to_markdown(note)
    return json.dumps(note, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def save_output(path: str, content: str) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
