"""Reproducible bounded read probe: 100 durable tasks and ten concurrent views."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import median
import tempfile
from time import perf_counter
from galatea_app.projections import Workbench
from galatea_app.service import Context, TaskService


def run():
    with tempfile.TemporaryDirectory(prefix="galatea-capacity-") as root:
        service = TaskService(Path(root) / "tasks.db", workspaces={"work": root})
        ctx = Context("probe", "host", "session", "thread", "work", role="app")
        tasks = []
        for n in range(100):
            own = Context(
                "probe", "host", "session-" + str(n), "thread-" + str(n), "work"
            )
            tasks.append(
                service.create(
                    own, command_id="create-" + str(n), goal="Capacity probe"
                )["task_id"]
            )

        def read(n):
            start = perf_counter()
            for _ in range(20):
                view = Workbench(service).view(ctx, tasks[n % 100])
                assert view["task_snapshot"]["task"]["status"] == "draft"
            return (perf_counter() - start) * 1000 / 20

        start = perf_counter()
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(read, range(100)))
        elapsed = perf_counter() - start
        data = {
            "tasks": 100,
            "concurrent_views": 10,
            "reads": 2000,
            "seconds": elapsed,
            "reads_per_second": 2000 / elapsed,
            "median_read_ms": median(results),
            "max_worker_average_ms": max(results),
            "scope": "local SQLite read probe, not production SLA or training throughput",
        }
        service.close()
        return data


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--output")
    args = p.parse_args()
    data = run()
    value = json.dumps(data, indent=2)
    if args.output:
        Path(args.output).write_text(value + "\n")
    print(value)
