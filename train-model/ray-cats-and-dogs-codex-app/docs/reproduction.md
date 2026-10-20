# Codex App 复现说明

来源是同仓库的 `train-model/ray-cats-and-dogs/`，复现日期为 2026-10-19。
完整复制源码、配置、Notebook、测试和固定治理入口，排除 Python 缓存等生成文件。
源项目不作修改。历史 `*-repro-20261016.yaml` 保留供对照；本次使用下面的配置。

## 身份与环境

| 项目 | 本次配置 |
| --- | --- |
| 项目、MLflow Experiment、注册模型名称 | `ray-cats-and-dogs-codex-app` |
| Python 包 | `ray_cats_dogs`，与来源相同 |
| 环境锁及预装前缀 | `conda.yaml`、`/data/miniconda3/envs/ray-cats-and-dogs-py312` |
| 集群 | 用户选择的 `dev`；实际 GCS 与 Jobs 端点通过运行环境配置，不提交机器内网地址 |
| MLflow | 使用运行环境中的 `MLFLOW_TRACKING_URI`，同机示例为 `http://127.0.0.1:18103` |
| Runtime 发布前缀 | `s3://training-data/ray-runtime/ray-cats-and-dogs-codex-app` |
| 本地运行状态 | 仓库外 `platform-data/ray-cats-and-dogs-codex-app/` |

环境依赖完全复用源项目的锁定版本，不向共享环境安装本副本的 editable 包。
脚本从自己的 `src/` 导入，远端 Worker 使用新 Release Wheel。
Release 和治理回执分别在仓库外的
`platform-data/ray-cats-and-dogs-codex-app-release/` 和
`platform-data/ray-cats-and-dogs-codex-app-evidence/`。

## 执行顺序

1. 本地测试、全部配置检查、环境预热检查、Release dry-run。
2. 核对原数据快照与确定性切分：22,497 张训练、1,250 张验证、1,250 张测试。
   为遵守本地测试集隔离约束，本副本显式绑定已有的冻结 Manifest；本地计划及
   Smoke/Trial 只核对 train/validation 图像的字节摘要，最终测试图像在取得
   test-once claim 后才读取。新的 `prepare_run_dataset` 由计划与 Driver 共用，
   不改变模型、预处理、数据分片或训练实现。
3. 发布不可变 Release，使用 `job/governed.mjs plan` 获取精确 Readiness。
4. `configs/smoke.yaml`：单 GPU、一轮链路验证。
5. `configs/baseline-repro-20261019.yaml`：从头训练，最多 36 epoch，验证集连续
   3 epoch 不改善时提前停止。Seed 42、Batch 128、RMSprop、学习率 0.001、
   BF16、150×150 输入及模型结构均继承来源参数。
6. 仅依据新 Trial 的验证指标冻结最佳权重，通过
   `configs/champion-repro-20261019.yaml` 执行一次最终测试。
7. 通过 MLflow Tracking/Artifact API 核验终态、指标、Checkpoint 摘要、
   模型独立进程加载和质量门槛 `test_accuracy >= 0.80`。

所有真实训练和最终测试均需当前任务的精确方案确认和匹配的治理授权。
用户请求包括必要的 Runtime 发布；Registry Alias 修改不在本次范围。
源项目 README 中的历史运行 ID 和指标不算本次结果，只有新运行证据可用于验收。
受 GPU Kernel 和数据并行调度影响，复现不承诺逐位相同的权重。

本地验证：

```bash
/data/miniconda3/envs/ray-cats-and-dogs-py312/bin/python \
  -m unittest discover -s tests -p 'test_*.py'
/data/miniconda3/envs/ray-cats-and-dogs-py312/bin/python \
  scripts/train.py --config configs/smoke.yaml --check-config
/data/miniconda3/envs/ray-cats-and-dogs-py312/bin/python job/ci.py --dry-run
```
