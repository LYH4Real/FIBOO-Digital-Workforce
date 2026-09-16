"""Command-line and paste-to-fetch entry points."""

import argparse
import json
import os
import sys
from pathlib import Path

from .client import DEFAULT_PROFILE, fetch_note
from .errors import FetchError
from .output import save_output, serialize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="小红书笔记详情抓取：支持分享短链和整段分享文案。")
    commands = parser.add_subparsers(dest="command")
    fetch = commands.add_parser("fetch", help="抓取一篇笔记")
    fetch.add_argument("share_text", help="完整链接或分享文案，请用引号包裹；使用 - 从标准输入读取")
    fetch.add_argument("--mode", choices=["auto", "http", "browser"], default="auto")
    fetch.add_argument("--format", choices=["json", "md"], default="json")
    fetch.add_argument("--output", "-o", help="保存文件路径；省略时输出到终端")
    fetch.add_argument("--timeout", type=float, default=30, help="每种抓取方式的等待秒数，默认 30")
    fetch.add_argument("--cookie-file", help="HTTP 模式可用：UTF-8 Cookie 文件（或使用 XHS_COOKIE 环境变量）")
    fetch.add_argument("--profile-dir", default=DEFAULT_PROFILE, help="浏览器登录状态保存目录")
    fetch.add_argument("--headed", action="store_true", help="显示抓取浏览器，便于人工完成登录或验证")
    login = commands.add_parser("login", help="在本机浏览器中手动登录并保存登录状态")
    login.add_argument("--profile-dir", default=DEFAULT_PROFILE)
    login.add_argument("--timeout", type=float, default=180)
    return parser


def _interactive() -> int:
    print("小红书笔记抓取\n请粘贴完整分享链接或分享文案，然后按回车。", file=sys.stderr)
    text = input().strip()
    result = fetch_note(text)
    output_dir = Path(__file__).resolve().parent.parent / "output"
    for fmt in ("json", "md"):
        target = save_output(str(output_dir / f"{result['note_id']}.{fmt}"), serialize(result, fmt))
        print(f"已保存：{target}", file=sys.stderr)
    for warning in result.get("warnings") or []:
        print(f"提示：{warning}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    # Keep Chinese output readable in Windows terminals and redirected files.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command is None:
            if not sys.stdin.isatty():
                parser.print_help()
                return 2
            return _interactive()
        if args.command == "login":
            from .browser import login
            print("请在打开的浏览器中手动登录，完成后关闭该浏览器窗口。", file=sys.stderr)
            login(profile_dir=args.profile_dir, timeout=args.timeout)
            print(f"登录流程结束，浏览器状态目录：{args.profile_dir}", file=sys.stderr)
            return 0
        share_text = sys.stdin.read() if args.share_text == "-" else args.share_text
        cookie = (Path(args.cookie_file).read_text(encoding="utf-8-sig").strip()
                  if args.cookie_file else os.environ.get("XHS_COOKIE"))
        result = fetch_note(share_text, mode=args.mode, timeout=args.timeout, cookie=cookie,
                            profile_dir=args.profile_dir, headless=not args.headed)
        content = serialize(result, args.format)
        if args.output:
            target = save_output(args.output, content)
            print(f"已保存：{target}", file=sys.stderr)
        else:
            sys.stdout.write(content)
        for warning in result.get("warnings") or []:
            print(f"提示：{warning}", file=sys.stderr)
        return 0
    except FetchError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, UnicodeError):
        print(json.dumps({"error": {"code": "FILE_ERROR", "message": "文件读取或保存失败，请检查路径、UTF-8 编码和访问权限。"}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print("操作已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
