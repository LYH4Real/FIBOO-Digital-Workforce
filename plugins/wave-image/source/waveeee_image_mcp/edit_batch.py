from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from .client import WaveeeeClient, WaveeeeError
from .storage import ImageStorage, safe_filename


class EditBatchRunner:
    def __init__(self, client: WaveeeeClient, output_dir: Path, *, storage: ImageStorage | None = None, sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.storage = storage or ImageStorage(self.output_dir)
        self.sleep = sleep

    def run(self, tasks: Iterable[dict], *, concurrency: int = 4, retries: int = 2) -> list[dict]:
        task_list = list(tasks)
        if not task_list:
            raise ValueError("tasks 不能为空")
        if not 1 <= concurrency <= 16:
            raise ValueError("concurrency 必须是 1 到 16 之间的整数")
        if not 0 <= retries <= 5:
            raise ValueError("retries 必须是 0 到 5 之间的整数")
        for index, task in enumerate(task_list, start=1):
            if not isinstance(task, dict) or not str(task.get("prompt", "")).strip():
                raise ValueError(f"第 {index} 个任务的 prompt 不能为空")
            paths = task.get("image_paths", task.get("images", task.get("image", [])))
            if isinstance(paths, str):
                paths = [paths]
            if not isinstance(paths, list) or not 1 <= len(paths) <= 10:
                raise ValueError(f"第 {index} 个任务的 image_paths 必须是 1 到 10 个路径")
            if any(not Path(str(path)).is_file() for path in paths):
                raise ValueError(f"第 {index} 个任务包含不存在的参考图片")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        results: list[dict | None] = [None] * len(task_list)
        with (self.output_dir / "manifest.jsonl").open("w", encoding="utf-8") as handle:
            with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="waveeee-edit") as pool:
                futures = {pool.submit(self._run_one, i, task, retries): i for i, task in enumerate(task_list)}
                for future in as_completed(futures):
                    result = future.result()
                    results[futures[future]] = result
                    # The coordinator alone writes complete rows as pages finish.
                    handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                    handle.flush()
        return [result for result in results if result is not None]

    def _run_one(self, index: int, task: dict, retries: int) -> dict:
        task_id = str(task.get("id") or f"task-{index + 1}")
        prompt = str(task["prompt"]).strip()
        paths = task.get("image_paths", task.get("images", task.get("image", [])))
        if isinstance(paths, str):
            paths = [paths]
        started_at = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        started_clock = time.perf_counter()
        resolved_model = task.get("model") or getattr(getattr(self.client, "settings", None), "model", None)

        def finish(result: dict) -> dict:
            result.update({
                "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "duration_ms": max(0, round((time.perf_counter() - started_clock) * 1000)),
                "resolved_model": resolved_model,
            })
            return result

        attempts = 0
        while True:
            attempts += 1
            try:
                response = self.client.edit(prompt, image_paths=[str(path) for path in paths], model=task.get("model"), size=task.get("size", "1024x1024"), n=task.get("n", 1), response_format=task.get("response_format", "auto"))
                paths_out = self.storage.persist(response, safe_filename(task_id))
                return finish({"id": task_id, "success": True, "prompt": prompt, "reference_images": [str(path) for path in paths], "parameters": {k: task.get(k) for k in ("model", "size", "n", "response_format")}, "images": [str(path) for path in paths_out], "attempts": attempts})
            except Exception as exc:
                retryable = isinstance(exc, WaveeeeError) and exc.retryable
                if not retryable or attempts > retries:
                    return finish({"id": task_id, "success": False, "prompt": prompt, "reference_images": [str(path) for path in paths], "parameters": {k: task.get(k) for k in ("model", "size", "n", "response_format")}, "images": [], "attempts": attempts, "error": str(exc)})
                self.sleep(min(2 ** (attempts - 1), 8))
