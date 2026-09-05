# Kaggle House Prices 表格回归

本项目使用 Kaggle `house-prices-advanced-regression-techniques` 官方数据，在
`train-model/ray-kaggle-house-prices/` 内提供可复现的表格回归训练、MLflow 跟踪、完整性证据和 Kaggle 提交产物。

## 数据与目标

- 数据目录：`/data/ai/chenzhangyue/code/data/house-prices/`
- 数据来源：`kaggle://competitions/house-prices-advanced-regression-techniques`
- 训练集 1,460 行，推理集 1,459 行；官方推理集没有 `SalePrice` 标签。
- 目标：`log1p(SalePrice)` 空间 RMSE，等价于 Kaggle RMSLE；优化方向为 `min`。
- 训练、验证、内部最终留出集都来自带标签的 Kaggle `train.csv`；Kaggle `test.csv` 只用于 Champion 的最终推理和提交，不参与选择、调参或早停。

预处理器在每个交叉验证训练折内拟合，并完全回放到验证、内部留出和推理数据。模型族包括 Elastic Net、Gradient Boosting、XGBoost、LightGBM 和 CatBoost；调优只使用开发集 OOF 证据，最终预测使用 OOF 稳健约束得到的非负 blend 权重。

## 运行

配置检查、只读计划和预估可快速完成的低风险 Smoke 可以在本地运行：

```bash
PYTHON=/data/conda/envs/attend-ray-py312/bin/python
$PYTHON scripts/train.py --config configs/smoke.yaml --check-config
$PYTHON scripts/train.py --config configs/smoke.yaml --plan
$PYTHON scripts/train.py --config configs/smoke.yaml
```

正式 Trial/Champion、长时或资源密集训练优先通过 `job/ci.py` 构建并发布不可变 release，再由
`job/cd.py --mode train` 或 Galatea Tool 提交 Ray Job。预估可快速完成的低风险探索可直接运行参数化入口，
但项目结构、固定入口、依赖、release、数据或切分契约不匹配时必须阻塞，不得改用本地命令绕过预检；
本地结果不能声明为 governed Ray Run 或最终证据。

训练会把 MLflow Run、数据清单、完整性报告、OOF 预测和模型选择报告写入配置的 MLflow Artifact 位置；本地生成的提交文件位于 `platform-data/ray-kaggle-house-prices/outputs/submission.csv`。

Champion 需要在 `configs/champion.yaml` 中明确启用内部留出评估和提交生成，并且使用已经选定的参数；项目不会自动修改 Model Registry alias。

## 运行测试

```bash
/data/conda/envs/attend-ray-py312/bin/python -m unittest discover \
  -s tests -p 'test_*.py'
```

数据集、checkpoint、模型、提交和 release 均属于运行时状态，保存在 `platform-data/` 或数据目录，不提交到 Git。
