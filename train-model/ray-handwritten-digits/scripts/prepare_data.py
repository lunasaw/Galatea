#!/usr/bin/env python3
"""校验 Kaggle 手写数字压缩包并解压为十个类别目录。"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path


EXPECTED_DATASET = "olafkrastovski/handwritten-digits-0-9"


def _find_image_root(extracted: Path) -> Path:
    class_names = {str(index) for index in range(10)}
    candidates = [extracted, *[path for path in extracted.rglob("*") if path.is_dir()]]
    for candidate in candidates:
        if class_names.issubset({path.name for path in candidate.iterdir() if path.is_dir()}):
            return candidate
    raise RuntimeError("压缩包中没有找到 0 到 9 类别目录")


def main() -> None:
    parser = argparse.ArgumentParser(description="准备 Kaggle 手写数字数据")
    parser.add_argument("--archive", type=Path, default=Path("/data/ai/chenzhangyue/code/data/handwritten-digits-kaggle/dataset.zip"))
    parser.add_argument("--output", type=Path, default=Path("/data/ai/chenzhangyue/code/data/handwritten-digits-kaggle/images"))
    args = parser.parse_args()
    if not args.archive.is_file():
        raise FileNotFoundError(args.archive)
    with zipfile.ZipFile(args.archive) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"压缩包校验失败: {bad}")
        staging = args.output.parent / ".extracting-handwritten-digits"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        archive.extractall(staging)
    source = _find_image_root(staging)
    if args.output.exists():
        shutil.rmtree(args.output)
    args.output.mkdir(parents=True)
    for index in range(10):
        shutil.copytree(source / str(index), args.output / f"digit_{index}")
    shutil.rmtree(staging)
    counts = {f"digit_{index}": sum(1 for item in (args.output / f"digit_{index}").iterdir() if item.is_file()) for index in range(10)}
    print({"dataset": EXPECTED_DATASET, "output": str(args.output.resolve()), "counts": counts, "total": sum(counts.values())})


if __name__ == "__main__":
    main()
