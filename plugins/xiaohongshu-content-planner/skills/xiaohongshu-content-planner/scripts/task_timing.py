"""Append-only local task timing. No prompts, credentials, models or network.

Start before reading/preparing materials. `complete` is a caller declaration,
not proof of quality. prep/cold/warm runs remain separate measurement scopes.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
import uuid

STAGES = ("intake", "planning", "awaiting_user", "generation", "validation", "rework", "delivery", "blocked")
MODES = ("prep", "cold", "warm")
OUTCOMES = ("complete", "partial", "failed")
PROCESS_ID = uuid.uuid4().hex


def require(condition, message):
    if not condition:
        raise ValueError(message)


def boot_identity():
    """Return an OS boot identity, or None; never infer it from wall-clock math."""
    try:
        if sys.platform.startswith("linux"):
            return "linux:" + Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        if sys.platform == "win32":
            import ctypes
            # SystemTimeOfDayInformation starts with the kernel BootTime value.
            query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
            query.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
            query.restype = ctypes.c_long
            buffer = ctypes.create_string_buffer(128)
            returned = ctypes.c_ulong()
            if query(3, buffer, len(buffer), ctypes.byref(returned)) == 0 and returned.value >= 8:
                boot = int.from_bytes(buffer.raw[:8], "little", signed=True)
                if boot > 0:
                    return f"windows:{boot}"
    except (OSError, ValueError, AttributeError):
        pass
    return None


def sample_clock():
    boot = boot_identity()
    # CLOCK_BOOTTIME includes suspend time. Other platforms use Python's actual
    # monotonic clock, recording its implementation instead of assuming domains.
    if hasattr(time, "CLOCK_BOOTTIME"):
        monotonic = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        implementation = "CLOCK_BOOTTIME"
    else:
        monotonic = time.monotonic_ns()
        implementation = time.get_clock_info("monotonic").implementation
    utc_ns = time.time_ns()
    return {
        "utc": datetime.fromtimestamp(utc_ns / 1_000_000_000, timezone.utc).isoformat(),
        "utc_ns": utc_ns, "monotonic_ns": monotonic, "pid": os.getpid(),
        "clock": {"host_id": hashlib.sha256(platform.node().encode()).hexdigest()[:20],
                  "boot_id": hashlib.sha256(boot.encode()).hexdigest() if boot else None,
                  "implementation": implementation, "process_id": PROCESS_ID,
                  "cross_process_comparable": boot is not None},
    }


@contextmanager
def journal_lock(journal):
    """An O_EXCL sidecar serializes writers/readers; abandoned locks fail closed."""
    lock_path = journal.with_name(journal.name + ".lock")
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("计时记录正在使用，或上次中断留下锁；请检查后重试，不自动破锁") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "process_id": PROCESS_ID}))
            handle.flush()
            os.fsync(handle.fileno())
        yield
    finally:
        lock_path.unlink()


def read_events(journal):
    raw = journal.read_bytes()
    require(raw and raw.endswith(b"\n"), "journal为空或末行不完整；禁止自动修补历史")
    events = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
    require(events[0].get("type") == "begin" and events[0].get("mode") in MODES,
            "journal缺少有效begin/mode")
    for index, event in enumerate(events):
        require(event.get("schema_version") == 1 and event.get("sequence") == index,
                "journal版本或事件序号不连续")
        require(event.get("run_id") == events[0].get("run_id"), "journal混入不同run")
        require(isinstance(event.get("clock"), dict), "journal缺少clock信息")
        require(type(event.get("utc_ns")) is int and type(event.get("monotonic_ns")) is int,
                "journal缺少实际时钟值")
        kind = event.get("type")
        require(kind in {"begin", "mark", "finish"}, "journal事件类型无效")
        require(index == 0 or kind != "begin", "journal有重复begin")
        if kind in {"begin", "mark"}:
            require(event.get("stage") in STAGES, "journal阶段无效")
        if kind == "finish":
            require(index == len(events) - 1, "journal在完成后仍有事件")
            require(event.get("outcome") in OUTCOMES and type(event.get("pages")) is int and event["pages"] >= 0,
                    "journal完成结果无效")
    return events


def append_event(journal, events, kind, **fields):
    require(events[-1]["type"] != "finish", "计时记录已完成，拒绝继续写入")
    event = {"schema_version": 1, "run_id": events[0]["run_id"], "sequence": len(events),
             "type": kind, **sample_clock(), **fields}
    with journal.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return event


def begin(output_root, mode="cold"):
    require(mode in MODES, "mode无效")
    stamp = sample_clock()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    directory = Path(output_root).resolve() / run_id
    directory.mkdir(parents=True, exist_ok=False)
    journal = directory / "timing.jsonl"
    event = {"schema_version": 1, "run_id": run_id, "sequence": 0, "type": "begin",
             "mode": mode, "stage": "intake", **stamp}
    with journal.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return {"ok": True, "run_id": run_id, "mode": mode, "stage": "intake", "journal": str(journal)}


def mark(journal, stage):
    require(stage in STAGES, "stage无效")
    journal = Path(journal).resolve()
    with journal_lock(journal):
        event = append_event(journal, read_events(journal), "mark", stage=stage)
    return {"ok": True, "journal": str(journal), "sequence": event["sequence"], "stage": stage, "utc": event["utc"]}


def comparable(left, right):
    a, b = left["clock"], right["clock"]
    if a.get("host_id") != b.get("host_id") or a.get("implementation") != b.get("implementation"):
        return False
    if a.get("process_id") == b.get("process_id"):
        return True
    return bool(a.get("cross_process_comparable") and b.get("cross_process_comparable")
                and a.get("boot_id") and a.get("boot_id") == b.get("boot_id"))


def summarize(events, now=None):
    finished = events[-1]["type"] == "finish"
    end = events[-1] if finished else (now or sample_clock())
    timeline = events if finished else events + [end]
    durations = {stage: 0.0 for stage in STAGES}
    warnings, bases = [], set()
    elapsed_known = True
    stage = events[0]["stage"]
    for index, (left, right) in enumerate(zip(timeline, timeline[1:])):
        wall = (right["utc_ns"] - left["utc_ns"]) / 1_000_000_000
        if comparable(left, right):
            seconds = (right["monotonic_ns"] - left["monotonic_ns"]) / 1_000_000_000
            basis = "monotonic"
            if abs(seconds - wall) > 2:
                warnings.append(f"interval_{index}: UTC与monotonic差异超过2秒，采用同一时钟域的monotonic")
        else:
            seconds, basis = wall, "utc_fallback"
            warnings.append(f"interval_{index}: 无法证明monotonic跨进程/重启可比，使用UTC估计")
        bases.add(basis)
        if seconds < 0:
            elapsed_known = False
            warnings.append(f"interval_{index}: 时钟倒退，无法计算可靠耗时")
        else:
            durations[stage] += seconds
        if right.get("type") == "mark":
            stage = right["stage"]
    total = sum(durations.values()) if elapsed_known else None
    waiting = durations["awaiting_user"] if elapsed_known else None
    mode = events[0]["mode"]
    reliable = elapsed_known and "utc_fallback" not in bases
    return {
        "ok": True, "run_id": events[0]["run_id"], "mode": mode, "finished": finished,
        "outcome": events[-1].get("outcome") if finished else "running",
        "outcome_is_caller_declaration": True,
        "pages": events[-1].get("pages") if finished else None,
        "started_at": events[0]["utc"], "measured_until": end["utc"],
        "elapsed_seconds": round(total, 6) if total is not None else None,
        "awaiting_user_seconds": round(waiting, 6) if waiting is not None else None,
        "active_seconds": round(total - waiting, 6) if total is not None else None,
        "stage_seconds": {key: round(value, 6) if elapsed_known else None for key, value in durations.items()},
        "clock_basis": sorted(bases), "timing_reliable": reliable, "warnings": warnings,
        "quality": {"status": "unverified", "evidence": None},
        "target": {"seconds": 600, "elapsed_within_target": total <= 600 if total is not None else None,
                   "final_measurement": finished, "quality_and_time_acceptance": "unverified"},
        "measurement_scope": "full_run_including_intake" if mode == "cold" else
                             ("product_preparation_only" if mode == "prep" else "warm_run_preparation_separate"),
        "cold_start_cost_included": mode == "cold", "mode_is_caller_declaration": True,
        "include_in_all_runs_denominator": True,
        "note": "总耗时包括等用户；active只扣除awaiting_user，blocked/rework均计入。失败与未完成样本保留；prep/cold/warm应分组报告。complete不证明质量或十分钟验收。",
    }


def report(journal):
    journal = Path(journal).resolve()
    with journal_lock(journal):
        return summarize(read_events(journal))


def finish(journal, outcome, pages):
    require(outcome in OUTCOMES, "outcome无效")
    require(type(pages) is int and pages >= 0, "pages必须为非负整数")
    journal = Path(journal).resolve()
    with journal_lock(journal):
        events = read_events(journal)
        events.append(append_event(journal, events, "finish", outcome=outcome, pages=pages))
        return summarize(events)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("begin")
    start.add_argument("--output-root", required=True)
    start.add_argument("--mode", choices=MODES, default="cold")
    transition = commands.add_parser("mark")
    transition.add_argument("--journal", required=True)
    transition.add_argument("--stage", choices=STAGES, required=True)
    end = commands.add_parser("finish")
    end.add_argument("--journal", required=True)
    end.add_argument("--outcome", choices=OUTCOMES, required=True)
    end.add_argument("--pages", type=int, required=True)
    status = commands.add_parser("report")
    status.add_argument("--journal", required=True)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        result = {"begin": begin, "mark": mark, "finish": finish, "report": report}[command](**args)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
