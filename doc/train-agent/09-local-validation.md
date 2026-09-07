# 本地总体验证记录

日期：2026-09-07。范围：独立发布包、跨组件协议与故障边界、部署/真实验收文档。
环境：macOS，Python 3.11.13；官方 SDK 源码 characterization 使用 `459a79e`。
没有真实模型调用、训练、测试集访问、远端部署或生产 Alias 变更。

## 1. 验证结果

| 验证范围 | 结果 | 证据位置 |
| --- | --- | --- |
| MCP 包 | 37 项通过 | `services/galatea-mcp/tests/` |
| Runner 包（启用官方 SDK 源码测试） | 30 项通过 | `agents/training-agent-runtime/tests/` |
| Skill 包 | 10 项通过；六个 Skill、十个冻结场景 | `packages/galatea-training-skills/tests/` |
| reference workload | 22 项通过 | `train-model/ray-tabular-regression/tests/` |
| 仓库级跨组件/契约 | 9 项通过 | `tests/` |
| 两个 wheel 独立安装与 Skill 解包 | 19 个检查步骤通过；包内 76 项通过、1 项按设计跳过 | `verify_train_agent_installation.py` 的 `result.json` 和逐项日志 |
| 原插件 | `git diff HEAD -- plugins` 无变化，工作区无新增插件文件 | Git 核对 |
| Linux unit 实际检查 | pending；本机没有 systemd-analyze | 目标 Linux 部署验收执行 |

源码测试合计 **108 项通过**。独立安装故意不提供相邻 Codex checkout，因此官方 SDK 源码测试
在该环境中跳过；它已在源码验证环境单独启用并通过。源码 characterization 的 client/通知流
仍是测试替身，不证明发布 SDK/Runtime、真实模型或 Skill 发现已经验收。

## 2. 本轮补齐的边界

- 真实 MCP HTTP initialize、17 个工具与 Runner 协作完成 baseline → Trial → Champion →
  evaluate → 平台 accepted。每次完整场景四个计算替身任务、五次决策替身调用。
- 每个 Job 先置 Ray succeeded，延迟发布证据；Runner 进入 waiting_evidence，重建 Runner/Store
  后继续等待，等待 tick 没有模型调用增加。证据发布后恢复同一 Thread，最后核对 500 个模拟 token。
- 另跑完整场景，让决策替身替换平台已核验 report_ref；Runner 拒绝 completed。
  正常报告的 outcome、report_ref、evidence_refs 均与 MCP 当前登记报告相等，且 integrity=verified、
  final_test_status=passed。
- workload 生成的 Release/config/登记文件按各角色直接交给 MCP Registry.verify；
  对象存储校验仍为替身，真实冻结数据和授权必须在部署时补齐。
- 从仓库外的新目录构建 wheel，移除构建源码，在独立 venv 安装；验证包来源位于 venv、
  CLI 与随包 Schema 可用、依赖检查通过，MCP/Runner 无互相或 Ray/MLflow 源码依赖。
- Skill 包原先有一项测试硬编码相邻 MCP 路径，干净解包实测报 FileNotFoundError。
  已把契约同步测试移到仓库级，保留 MCP 发布契约比对；独立 Skill 包全部测试通过。
- 修正 Runner 部署示例的 Skill 路径为完整归档内的 `skills/training-campaign/SKILL.md`，
  安装检查现在核对样例路径确实存在。生成的 Skill `dist/` 归档纳入 Git 忽略。
- 补齐 [端到端手册](08-e2e-integration-runbook.md)、部署 inventory、恢复/回退步骤和真实验收记录格式；
  修正旧开发记录中关于完整 Skill/Runtime 摘要核验的过时描述；恢复说明按实现明确固定等待间隔、
  无自动 Thread 替换，以及 unit 模板尚未设置主机 CPU/内存限制。

首次按子目录默认 `python3` 复跑时，shell 选中了 Python 3.8.10，导致类型语法和依赖导入失败。
使用明确 Python 3.11.13 路径后通过。独立包安装检查固定同一构建解释器并创建专用 venv，
未为这些失败修改生产逻辑。

## 3. 重复执行

以下 `PYTHON` 指向预装本地测试依赖的明确 Python 3.11+ 路径。构建安装另需 setuptools/wheel。

```bash
PYTHON=/path/to/python3.11
PYTHONPATH=services/galatea-mcp/src "$PYTHON" -m unittest discover -s services/galatea-mcp/tests -v
PYTHONPATH=agents/training-agent-runtime/src "$PYTHON" -m unittest discover -s agents/training-agent-runtime/tests -v
"$PYTHON" -m unittest discover -s packages/galatea-training-skills/tests -v
"$PYTHON" -m unittest discover -s train-model/ray-tabular-regression/tests -v
"$PYTHON" -m unittest discover -s tests -v
CODEX_SDK_SOURCE=/path/to/accepted-source/sdk/python/src \
  PYTHONPATH=agents/training-agent-runtime/src "$PYTHON" -m unittest discover \
  -s agents/training-agent-runtime/tests -p test_official_sdk.py -v
"$PYTHON" scripts/verify_train_agent_installation.py \
  --wheelhouse /secure/build/wheelhouse --output /secure/build/new-installation-check
```

[独立安装脚本](../../scripts/verify_train_agent_installation.py)只使用预备 wheelhouse，保存解析后的
依赖快照与发布制品摘要。无额外源码的 venv 是独立性证据，不能代替目标 Linux/Python ABI 和
完整平台 extra 的部署验收。不要把本次临时日志目录作为长期恢复状态；发布时将证据复制到受保护验收目录。

## 4. 尚待真实环境验收

以下项目均保持 pending：[A01–A17](implementation/08-deployment-and-acceptance.md#3-验收矩阵)
在目标部署环境的对应观测、发布 SDK/Runtime 配对、真实 Skill 发现和模型调用、HTTPS 身份与权限、
systemd/cgroup 清理、Ray trainer/evaluator 身份与数据权限、真实 MLflow Artifact 下载/干净加载、
有明确预算的受治理训练与最终测试一次性使用。已有本地故障测试只能作为这些演练的准备证据。

随包 runtime-lock 保持 pending。参考 Campaign 仍默认三槽位且不授权 Trial；四任务真实验收需
管理员提前配置批准槽位与预算。模型输出和替身平台报告不能升级成真实 accepted 训练证据。
