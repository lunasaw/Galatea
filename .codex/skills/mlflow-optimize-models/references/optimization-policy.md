# MLflow Optimization Policy

Use this reference after `scripts/analyze_experiment.py` has collected the current
Experiment evidence.

## Selection contract

- Obtain MLflow metadata through the Tracking API; never depend on backend database
  files or server-local paths.
- Download Artifacts through MLflow APIs/proxy so remote deployments and credentials
  remain encapsulated by the server.
- Choose hyperparameters with validation or cross-validation metrics.
- Reserve test/holdout data for final descriptive evaluation.
- Compare only Runs with compatible data, splits, preprocessing, labels, and features.
- Distinguish best observed from optimal; always state search space and budget.
- Retrain a selected configuration cleanly when exploratory state can affect results.

## Evidence to action

| Evidence | Prefer first | Escalate to code when |
| --- | --- | --- |
| Too few Runs or no varied parameters | Expand a controlled search | Search configuration is not expressible |
| One Epoch or truncated Runs | Increase budget with Early Stopping | Resume/checkpoint behavior is missing |
| Training improves, validation degrades | Regularization, augmentation, simpler model, earlier stop | Metrics or callbacks cannot expose/control it |
| Training and validation both plateau poorly | Learning-rate search, better features/data, transfer learning | Scheduler, preprocessing, loss, or architecture is limiting |
| High variance across seeds | Repeat finalists, stabilize data order and numerics | Seeding or distributed determinism is broken |
| Objective and secondary metrics disagree | Declare one primary objective and constraints | Multi-objective or constrained selection is required |
| Weak minority/class/slice performance | Add validation slice metrics and approved constraints | Loss weighting, sampling, calibration, or thresholding is needed |
| Quality gate fails | Block promotion and improve the model | Gate definition lacks an approved product requirement |
| Artifact or lineage verification fails | Repair tracking/storage before more training | Logging and recovery logic is incomplete |
| Latest Run is not validation-best | Keep the validation winner as lead | Selection logic chose the wrong Run |
| Winner comes from dirty code | Reproduce from a clean revision | Source capture or reproducibility controls are missing |

## Parameter reasoning

- Treat grouped historical means as leads, not causal estimates; Runs may differ in
  several parameters.
- Prefer one-factor or designed follow-up trials around promising regions.
- Use log scales for learning rates and regularization strengths.
- Repeat finalists across seeds before interpreting small score differences.
- Record conditional parameters explicitly, including frozen layers, preprocessing,
  sampling, scheduler, and loss settings.
- Avoid expanding architecture families until baseline optimization has coverage.

## Framework routing

- TensorFlow/Keras: inspect data pipelines, normalization, callbacks, checkpoint
  monitor/mode, optimizer schedules, frozen layers, and `training=True` behavior.
- PyTorch: inspect Dataset/DataLoader splits, train/eval modes, gradient scaling,
  schedulers, checkpoint state, distributed samplers, and rank-zero MLflow logging.
- scikit-learn: prefer Pipelines, cross-validation, leakage-safe preprocessing,
  calibrated scoring, and persisted feature schemas.
- XGBoost/LightGBM/CatBoost: inspect validation sets, early stopping, imbalance weights,
  depth/leaves, learning rate versus estimators, subsampling, and categorical handling.
- Ray/distributed jobs: verify identical config propagation, resource requests,
  deterministic data partitioning, retry semantics, and a single authoritative Run.

## Metric discipline

- Infer objective direction carefully: accuracy/AUC/F1 usually maximize; loss/error,
  latency, cost, and calibration error usually minimize.
- Require an explicit mode for ambiguous custom metrics.
- Keep accuracy, macro/slice metrics, calibration, latency, and resource cost visible
  when they are product constraints, even if only one is primary.
- Do not lower a quality threshold merely to make a Run pass.

## Verification ladder

1. Static validation and focused unit tests.
2. Read-only MLflow analysis and tuning plan.
3. Minimal smoke run with distinct Run identity.
4. Approved validation-only search budget.
5. Clean champion retraining.
6. One final holdout evaluation, Artifact recovery check, and human promotion review.
