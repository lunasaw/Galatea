# Ray Job 发布

本项目的正式入口是 `scripts/train.py`。`job/` 目录用于构建不可变 Ray release；发布内容必须排除 `job/`、`tests/`、`notebooks/` 和运行时缓存，且不得覆盖内容不同的既有 release。

正式 Trial/Champion、长时或资源密集训练应优先运行 `job/ci.py` 发布 release，再运行 `job/cd.py --mode train` 通过 Ray Jobs API 提交。预估可快速完成的低风险检查、Smoke 和探索实验可以直接运行 `scripts/train.py`，但不得把本地结果声明为 governed Ray Run 或最终证据。项目结构、固定入口、依赖、release、数据或切分契约不匹配时必须阻塞，不得用本地命令绕过失败的预检。只有通过对应的预检、计划和入口检查后，才可继续执行；审批策略为 `never` 时，不能通过受治理 Galatea Tool 提交或晋级。

正式发布默认读取项目根目录 `conda.yaml` 并生成 `runtime_env.conda`；`release.json` 会保存
环境来源和 SHA-256。pip 是显式 Smoke/调试覆盖，必须提供 requirements 文件或
`--pip-package`，不能与 Conda 混用：

```bash
python job/ci.py --runtime-mode pip \
  --pip-requirements /path/to/requirements-ray-smoke.txt --no-cd
```

正式 Ray 节点请设置 `RAY_CONDA_HOME=/data/conda`，不要依赖共享环境中的模型包版本。
