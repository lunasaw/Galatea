# 受治理 LLM 微调完整指南：从一台新机器到可验证模型

版本：2026-09-10。适用仓库：Galatea。目标读者：第一次接触模型微调的高中生，以及需要把实验变成
可复现工程流程的开发者。

本文把一次真实的 `wechat-persona` LoRA baseline 训练，整理成可以迁移到其他语言模型、数据集、
微调方法和执行后端的完整流程。Ray 是本仓库当前项目的具体执行实现；治理契约本身不把 Ray、LoRA、
因果语言模型或某一个指标当成架构边界。每个新项目复用同一控制面和证据模型，再由项目契约声明
具体后端、模型族、数据模式和质量指标。

本文同时包含：

- 一次已经完成的实际运行复盘；
- 任何新模型都可以套用的项目契约、配置、Release、执行和验收流程；
- 失败重试、时间预算、Artifact 路径、MLflow 指标和隐私隔离的经验；
- baseline 成功以后，如何在不破坏证据链的前提下继续 Trial、候选冻结、test-once 和 promotion。

本文遵循仓库的 [governed-training-workflow](../../.codex/skills/governed-training-workflow/SKILL.md)。凡是会更新参数、跑完整 epoch、生成 checkpoint/adapter，或创建持久训练证据的动作，都按 Training Run 处理。

## 阅读承诺和诚实边界

“只需要参照本文”在这里有一个严格、可检验的含义：读者不必再阅读其他说明文档，就能知道如何准备
机器、安装平台、建立项目、准备数据和模型、构建 Release、登记 Campaign、发起训练、观察结果、验证
Artifact、选择候选、做一次最终测试、恢复和备份。本文会引用仓库中的脚本、配置和 Schema；它们是
要执行的程序，不是额外的阅读前提。

但一份静态文档不能替你产生以下外部条件：

- 一台有足够磁盘、内存和兼容 NVIDIA GPU 的 Linux 机器；
- 你有权使用的模型权重，以及接受模型许可证所需的账号或 Token；
- 你有权用于指定用途的数据；私有数据还需要脱敏、人工审核和撤回机制；
- 组织中的真实审批人、算力预算和生产发布授权；
- 新模型特有的 loader、label/loss、checkpoint 和 evaluator 实现。

因此本文作出两层承诺：

1. 对本仓库当前受支持的单机架构，本文给出从空机器到平台和受治理 Training Run 的完整操作顺序。
2. 对任意其他模型或任务，本文给出完整的实现合同、目录模板、测试要求和迁移判定；不能声称只修改
   `model_id` 就能把因果语言模型、Seq2Seq、DPO、Reward Model 和多模态模型互相替换。

如果缺少外部权重、获准数据、现行 Release 或管理员授权，正确结果是 `blocked`，不是绕过 Galatea 在
本地偷偷训练。本文中的“完成”始终指证据完整，而不只是 GPU 跑完。

---

# 第一篇：从零搭建并跑通

这一篇按实际操作顺序编写。第一次实施时从 A 一直做到 Q，不要跳章。各阶段围绕目的、输入、命令、
预期结果、失败处理和继续条件组织；带命令的关键步骤会明确说明预期或停止条件。

## A. 先理解你要搭什么

### A.1 用学校作业理解训练治理

普通训练像“算出一个答案”。受治理训练像“交一份任何老师都能复查的实验报告”：

| 训练词 | 白话解释 | 类比 |
| --- | --- | --- |
| 基座模型 | 微调前已经学过大量知识的模型 | 已经读过很多书的学生 |
| Dataset | 让模型学习或考试的样本集合 | 练习题和试卷 |
| train | 用来更新模型参数的数据 | 练习题 |
| validation | 调参数、选方案的数据 | 模拟考试 |
| test/holdout | 方案冻结后只使用一次的数据 | 密封的期末试卷 |
| tokenizer/processor | 把文字、图片等变成模型能处理的数字 | 统一答题卡格式 |
| loss | 模型答案与目标答案的差距，通常越小越好 | 错题分数 |
| optimizer step | 真正修改一次模型参数 | 根据错题改进一次 |
| LoRA | 只训练少量附加参数，保留大部分基座参数不动 | 在课本旁增加一本薄笔记 |
| checkpoint | 可继续训练的中途存档 | 草稿和当前进度 |
| Artifact | 训练产生并长期保存的文件 | 试卷、报告、附件 |
| MLflow Run | 一次实验的参数、指标和文件记录 | 有唯一编号的实验报告 |
| Ray Job | 真正占用 CPU/GPU 执行的任务 | 被安排到实验室的一次实验 |
| Release | 冻结的代码、配置和环境包 | 封口后不能换页的实验材料 |
| Campaign | 管理员批准的一组步骤和总预算 | 实验申请单 |
| Plan/readiness | 提交前对身份、资源和权限的检查结果 | 实验室准入检查 |
| Driver | 唯一有权创建父 Run 和结束训练的入口 | 主考老师 |
| Trial | 只用 train/validation 比较的候选实验 | 模拟考试方案 |
| Champion | 候选冻结后从干净状态训练的最终候选 | 正式参赛版本 |
| test-once | 对冻结候选使用一次 test | 拆开密封试卷 |
| Promotion | 人工批准后更新生产模型指针 | 正式发布成绩 |
| SHA-256 digest | 64 位十六进制内容指纹；内容一变，指纹几乎一定变 | 文件指纹 |
| immutable | 创建后不允许原地改写 | 封存 |

### A.2 五个组件各做什么

```text
你/管理员
  |
  v
Galatea MCP -------------- 审批、预算、状态机、幂等和 test-once
  |
  v
Ray Job ------------------ 把固定 Driver 放到 CPU/GPU 上执行
  |
  +----> MLflow ----------- 保存 Run、参数、指标和 Artifact 索引
  |          |
  |          v
  +----> MinIO ------------ 保存数据快照、模型、checkpoint 和报告文件
  |
  `----> 项目代码 --------- loader、模型、loss、训练器和 evaluator
```

JupyterLab 只是可选的交互开发界面。它能做探索、画图、只读检查和 forward-only 测试，不能成为另一条
正式训练入口。

### A.3 三种结果必须分清

| 结果 | 说明 | 能否称为训练成功 |
| --- | --- | --- |
| `planned` | 配置和依赖检查通过，还没训练 | 否 |
| Ray `SUCCEEDED` | Driver 进程退出码为 0 | 还不能单独证明 |
| governed success | Ray 成功、MLflow `FINISHED`、证据和 Artifact 回读通过 | 是 |

### A.4 第一次执行命令前先读这一节

本文的命令都在 Bash 中执行。先用 `whoami`、`pwd` 和 `git status --short` 确认当前用户、目录和工作树；
不要在看不懂目标路径时使用 `sudo`。代码块中以 `#` 开头的是说明，不是输出。反斜杠 `\` 表示命令还
没有结束，要把下一行接在同一条命令后面。

示例中的 `replace-with-...` 和 `<...>` 都是必须替换的占位符。例如 `<RELEASE_ID>` 要换成上一步真实
返回的 Release ID，不能连尖括号一起提交。`$NAME` 或 `${NAME}` 是 shell 变量；它只在当前 shell 中
存在，退出终端或重新登录后必须重新执行对应的赋值命令。后续代码块引用 `DATASET_ROOT`、
`REGISTRATION_ROOT` 等变量时，也必须在同一个 shell 中继续，或先按前文重新赋值。

按以下节奏执行，而不是一次粘贴整篇文档：

1. 读完当前小节，确认标记是只读、本机写入、Training Run 还是最终测试/发布。
2. 替换占位符，并检查路径、账号、ID、预算和权限来自真实上一步或真实审批。
3. 一次只运行一个代码块；检查退出码和小节写明的继续条件。
4. 不满足继续条件就停止并修复，不能跳到下一节，也不能把示例 ID 当成真实证据。
5. 密码、Token、原始私有文本和 test 内容只放受控文件或安全输入，不粘贴到命令历史、Git 或聊天。

第一遍可以只做到 M.3 的 preflight。N.3、O.2、O.4、O.5 和 O.6 分别会训练、继续实验、使用最终测试
或发布；只有对应授权人明确批准时才运行。

## B. 安全级别和停止规则

本文给命令标记四种级别：

| 标记 | 会发生什么 | 是否需要额外确认 |
| --- | --- | --- |
| `[只读]` | 读取版本、状态、配置或 API | 通常不需要 |
| `[本机写入]` | 安装软件、创建目录、数据副本或服务记录 | 先确认路径和磁盘 |
| `[Training Run]` | 更新模型参数、使用 GPU、创建 Run/Artifact | 必须有 Campaign 和算力授权 |
| `[最终测试/发布]` | 读取 test 或改变生产模型别名 | 必须独立人工授权 |

遇到以下任意一项立即停止，不要用“先跑起来再说”处理：

- 不知道数据是否有权用于训练；
- 配置中出现密码、Token、私有 Endpoint 或原始敏感文本；
- train、validation、test 有重复会话、用户、模板族或近重复样本；
- 模型或 tokenizer 只写了可变的 `latest/main`，没有不可变 revision；
- `check` 或 `plan` 会更新参数、创建 checkpoint 或读取 test；
- 项目声明 Ray，但有人建议直接运行训练函数或通用 `ray job submit`；
- GPU 显存、系统内存、磁盘或授权时限没有余量；
- Ray、MLflow、Galatea 或 Artifact 的身份互相对不上；
- test 已经被用于调参；
- 用户只要求整理/分析，却没有授权付费训练或修改生产 alias。

## C. 新机器验收

### C.1 本文采用的可复现基线

为避免把当前机器的 IP、用户名和 GPU 型号写死，本文固定软件接口和目录，不固定网卡地址与 GPU 名称：

| 项目 | 基线 |
| --- | --- |
| 操作系统 | Ubuntu Server 22.04 或 24.04 LTS，x86_64，使用 systemd |
| 源码目录 | `/data/ai/chenzhangyue/code/galatea` |
| Conda 根目录 | `/data/conda` |
| 平台环境 | `attend-ray-py312`，Python 3.12.12 |
| LLM/Ray 环境 | `ray-llm-py312`，Python 3.12.12 |
| Galatea MCP 环境 | `/opt/galatea-mcp` |
| MinIO | API `127.0.0.1:9000`，Console `127.0.0.1:9001` |
| MLflow | `127.0.0.1:5000` |
| Ray Dashboard/Jobs API | `127.0.0.1:8265` |
| Galatea MCP | `127.0.0.1:8791/mcp` |

教学或单机验证建议至少有 16 GiB RAM、200 GiB 可用磁盘、1 张 16 GiB 以上 NVIDIA GPU。实际需求由
模型、序列长度、batch、量化和优化器决定；表中的值不是普适保证。没有 GPU 时可以完成安装、配置、
数据、Release、只读检查和 mocked/forward-only 测试，但不能完成本文的 GPU Training Run。

### C.2 `[只读]` 检查硬件和系统

```bash
uname -m
cat /etc/os-release
lscpu | sed -n '1,25p'
free -h
df -h /data 2>/dev/null || df -h /
nvidia-smi -L || true
```

继续条件：架构为 `x86_64`，系统受支持，磁盘和内存够用；需要 GPU 训练时 `nvidia-smi -L` 至少列出
一张 GPU。若 `nvidia-smi` 不存在或报驱动错误，先完成下一节。

### C.3 `[本机写入]` 安装系统包和 NVIDIA 驱动

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential ca-certificates curl git jq openssh-client openssl rsync sqlite3 unzip \
  ubuntu-drivers-common

ubuntu-drivers devices
sudo ubuntu-drivers install
sudo reboot
```

重启并重新登录后：

```bash
nvidia-smi
```

预期看到 GPU 名称、驱动版本和显存。PyTorch 的 wheel 会带所需 CUDA runtime；除非项目环境明确要求，
不要先安装另一个系统 CUDA Toolkit。`nvidia-smi` 仍失败时，查看 `journalctl -k -b`、Secure Boot 和
云厂商驱动说明；驱动正常前不得提交 GPU Job。

## D. 固定路径、克隆仓库和安装 Conda

### D.1 `[本机写入]` 创建源码目录

下面的路径与仓库当前项目入口一致。不要随意改路径后继续照抄；若必须改，先搜索并修改 systemd unit、
`galatea.project.yaml`、Release builder 和模型配置中的绝对路径。

```bash
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0755 \
  /data/ai/chenzhangyue/code
cd /data/ai/chenzhangyue/code
git clone https://github.com/lunasaw/Galatea.git galatea
cd /data/ai/chenzhangyue/code/galatea
git rev-parse --show-toplevel
git status --short
```

预期最后两条分别输出仓库绝对路径和空的状态。已有 checkout 时不要重新 clone，也不要删除未提交改动。

### D.2 `[本机写入]` 安装 Miniforge 到 `/data/conda`

以下示例固定一个安装器版本，并从同一官方 Release 下载 SHA-256 文件。若该版本不再可用，先在
Miniforge 官方 Release 页面选择明确版本，替换两个 URL；不能跳过校验或使用不明镜像。

```bash
MINIFORGE_VERSION=25.3.1-0
MINIFORGE_INSTALLER=Miniforge3-Linux-x86_64.sh
MINIFORGE_TMP=$(mktemp -d /tmp/galatea-miniforge.XXXXXX)

curl -fsSL \
  "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/${MINIFORGE_INSTALLER}" \
  -o "${MINIFORGE_TMP}/${MINIFORGE_INSTALLER}"
curl -fsSL \
  "https://github.com/conda-forge/miniforge/releases/download/${MINIFORGE_VERSION}/${MINIFORGE_INSTALLER}.sha256" \
  -o "${MINIFORGE_TMP}/${MINIFORGE_INSTALLER}.sha256"

(cd "${MINIFORGE_TMP}" && sha256sum -c "${MINIFORGE_INSTALLER}.sha256")
bash "${MINIFORGE_TMP}/${MINIFORGE_INSTALLER}" -b -p /data/conda
/data/conda/bin/conda --version
```

预期校验输出 `OK`，最后输出 Conda 版本。`/data/conda` 已存在时先用
`/data/conda/bin/conda --version` 判断它是否可用，不要覆盖安装。

## E. 创建三个隔离环境

### E.1 为什么不能只装一个环境

- 平台环境运行 JupyterLab 和 MLflow，不承载模型专属依赖。
- LLM 环境固定 Torch、Transformers、PEFT 和 Ray，训练节点必须完全一致。
- MCP 环境只负责治理和 API 客户端；升级训练库不能顺便改变控制面。

### E.2 `[本机写入]` 创建平台环境

```bash
source /data/conda/etc/profile.d/conda.sh
conda create -n attend-ray-py312 python=3.12.12 pip -y
conda activate attend-ray-py312
python -m pip install --upgrade pip
python -m pip install -r /data/ai/chenzhangyue/code/galatea/requirements.txt
python -m pip check
python --version
mlflow --version
```

继续条件：`python` 位于 `/data/conda/envs/attend-ray-py312/bin/`，Python 为 3.12，`pip check` 无错误。

### E.3 `[本机写入]` 创建 LLM/Ray 环境

当前端到端参考路线使用 `wechat-persona`，因此从它的环境合同创建运行环境；用 `-n` 统一新机器上的
环境名，不依赖 YAML 内部的项目名称：

```bash
source /data/conda/etc/profile.d/conda.sh
conda env create -n ray-llm-py312 \
  -f /data/ai/chenzhangyue/code/galatea/train-model/wechat-persona/conda.yaml
conda activate ray-llm-py312
python -m pip check
python - <<'PY'
import ray, torch, transformers, peft, mlflow

print("ray", ray.__version__)
print("torch", torch.__version__)
print("transformers", transformers.__version__)
print("peft", peft.__version__)
print("mlflow", mlflow.__version__)
print("cuda_available", torch.cuda.is_available())
print("gpu_count", torch.cuda.device_count())
if torch.cuda.is_available():
    print("gpu_0", torch.cuda.get_device_name(0))
PY
```

当前参考项目锁定 Ray 2.58.0、Torch 2.11.0、Transformers 5.16.1、PEFT 0.20.0、MLflow 3.14.0。
需要 GPU 训练时 `cuda_available` 必须为 `True`。若为 `False`，不要用 CPU 假装完成 GPU smoke；检查驱动、
wheel 来源和 `LD_LIBRARY_PATH`。

### E.4 `[本机写入]` 安装 Galatea MCP

仓库内 MCP 的 optional platform 依赖仍固定旧 Ray 客户端版本，因此新机器不要直接安装
`.[platform]`。Ubuntu 22.04 的系统 Python 通常是 3.10，但 MCP 要求 Python 3.11 以上，因此不要用
`python3 -m venv`；直接用已经校验过的 Miniforge 创建固定 Python 3.12 prefix。安装服务本体后，再
显式对齐到 LLM 集群使用的 Ray 2.58.0：

```bash
test ! -e /opt/galatea-mcp
sudo /data/conda/bin/conda create --prefix /opt/galatea-mcp \
  python=3.12.12 pip -y
sudo /opt/galatea-mcp/bin/python -m pip install --upgrade pip
sudo /opt/galatea-mcp/bin/python -m pip install \
  /data/ai/chenzhangyue/code/galatea/services/galatea-mcp
sudo /opt/galatea-mcp/bin/python -m pip install \
  'ray[default]==2.58.0' 'mlflow-skinny==3.14.0' 'boto3==1.43.89'
sudo /opt/galatea-mcp/bin/python -m pip check
/opt/galatea-mcp/bin/galatea-mcp --help
```

如果 `/opt/galatea-mcp` 已存在，不要覆盖；先用 `/opt/galatea-mcp/bin/python --version` 和
`pip check` 判断它是否是可复用的完整环境。继续条件是 Python 为 3.12、`pip check` 通过且 CLI 显示
`validate/register/preflight/reconcile/serve` 子命令。

### E.5 `[可选，本机写入]` 启动 JupyterLab

JupyterLab 不影响受治理训练闭环；只在需要交互探索、画图或编写 forward-only 组件测试时使用。平台
环境已经安装 JupyterLab 时可直接启动：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
cd /data/ai/chenzhangyue/code/galatea
jupyter lab --no-browser --allow-root --ServerApp.root_dir="$PWD"
```

浏览器访问终端打印的本机 URL。远程机器应通过 SSH 端口转发或有认证的 TLS 代理访问，不要把无认证
Jupyter 直接监听到公网。Notebook 只能用于探索、只读计划、forward-only fixture 和推理检查；任何
optimizer step、checkpoint 或持久训练证据仍必须走 Galatea 授权的固定 Driver。不需要交互开发时跳过
本节，不影响后续步骤。

## F. 部署 MinIO 对象存储

MinIO 保存大文件；MLflow 保存“这些文件属于哪个 Run”的记录。单机 MinIO 不等于高可用，后面必须做
异机备份。

### F.1 `[本机写入]` 安装固定版本二进制

```bash
MINIO_VERSION=RELEASE.2025-09-07T16-13-09Z
MC_VERSION=RELEASE.2025-08-13T08-35-41Z
MINIO_TMP=$(mktemp -d /tmp/galatea-minio.XXXXXX)

curl -fsSL \
  "https://dl.min.io/server/minio/release/linux-amd64/archive/minio.${MINIO_VERSION}" \
  -o "${MINIO_TMP}/minio"
curl -fsSL \
  "https://dl.min.io/server/minio/release/linux-amd64/archive/minio.${MINIO_VERSION}.sha256sum" \
  -o "${MINIO_TMP}/minio.sha256sum"
MINIO_SHA256=$(awk 'NR == 1 {print $1}' "${MINIO_TMP}/minio.sha256sum")
printf '%s  %s\n' "$MINIO_SHA256" "${MINIO_TMP}/minio" | sha256sum -c -

curl -fsSL \
  "https://dl.min.io/client/mc/release/linux-amd64/archive/mc.${MC_VERSION}" \
  -o "${MINIO_TMP}/mc"
curl -fsSL \
  "https://dl.min.io/client/mc/release/linux-amd64/archive/mc.${MC_VERSION}.sha256sum" \
  -o "${MINIO_TMP}/mc.sha256sum"
MC_SHA256=$(awk 'NR == 1 {print $1}' "${MINIO_TMP}/mc.sha256sum")
printf '%s  %s\n' "$MC_SHA256" "${MINIO_TMP}/mc" | sha256sum -c -

sudo install -o root -g root -m 0755 "${MINIO_TMP}/minio" /usr/local/bin/minio
sudo install -o root -g root -m 0755 "${MINIO_TMP}/mc" /usr/local/bin/mc
minio --version
mc --version
```

### F.2 `[本机写入]` 创建账号、目录和秘密文件

```bash
getent group minio >/dev/null || sudo groupadd --system minio
id minio >/dev/null 2>&1 || sudo useradd --system --gid minio \
  --home-dir /var/lib/minio --shell /usr/sbin/nologin minio

sudo install -d -o minio -g minio -m 0750 \
  /data/ai/chenzhangyue/code/galatea/platform-data/minio/data
sudo install -d -o root -g root -m 0750 /etc/minio

umask 077
MINIO_ADMIN_PASSWORD=$(openssl rand -hex 24)
MLFLOW_OBJECT_PASSWORD=$(openssl rand -hex 24)
GALATEA_READER_PASSWORD=$(openssl rand -hex 24)
DATA_PUBLISHER_PASSWORD=$(openssl rand -hex 24)

sudo tee /etc/minio/minio.env >/dev/null <<EOF
MINIO_ROOT_USER=galatea-minio-admin
MINIO_ROOT_PASSWORD=${MINIO_ADMIN_PASSWORD}
MINIO_VOLUMES=/data/ai/chenzhangyue/code/galatea/platform-data/minio/data
MINIO_OPTS="--address 127.0.0.1:9000 --console-address 127.0.0.1:9001"
EOF

sudo tee /etc/minio/mlflow-s3.env >/dev/null <<EOF
AWS_ACCESS_KEY_ID=mlflow
AWS_SECRET_ACCESS_KEY=${MLFLOW_OBJECT_PASSWORD}
AWS_DEFAULT_REGION=us-east-1
MLFLOW_S3_ENDPOINT_URL=http://127.0.0.1:9000
EOF

sudo tee /etc/minio/galatea-reader.env >/dev/null <<EOF
AWS_ACCESS_KEY_ID=galatea-reader
AWS_SECRET_ACCESS_KEY=${GALATEA_READER_PASSWORD}
AWS_DEFAULT_REGION=us-east-1
AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000
S3_ENDPOINT_URL=http://127.0.0.1:9000
EOF

sudo tee /etc/minio/data-publisher.env >/dev/null <<EOF
AWS_ACCESS_KEY_ID=data-publisher
AWS_SECRET_ACCESS_KEY=${DATA_PUBLISHER_PASSWORD}
AWS_DEFAULT_REGION=us-east-1
AWS_ENDPOINT_URL_S3=http://127.0.0.1:9000
EOF

sudo chmod 0600 \
  /etc/minio/minio.env \
  /etc/minio/mlflow-s3.env \
  /etc/minio/galatea-reader.env \
  /etc/minio/data-publisher.env
sudo chown root:root \
  /etc/minio/minio.env \
  /etc/minio/mlflow-s3.env \
  /etc/minio/galatea-reader.env \
  /etc/minio/data-publisher.env
unset MINIO_ADMIN_PASSWORD MLFLOW_OBJECT_PASSWORD GALATEA_READER_PASSWORD \
  DATA_PUBLISHER_PASSWORD
```

这些文件是密码，不得提交到 Git、复制到 Notebook 或粘贴到聊天中。

### F.3 `[本机写入]` 启动 MinIO

仓库中的 `minio.service` 不包含当前机器 IP，可以直接安装：

```bash
cd /data/ai/chenzhangyue/code/galatea
sudo systemd-analyze verify systemd/minio.service
sudo install -m 0644 systemd/minio.service /etc/systemd/system/minio.service
sudo systemctl daemon-reload
sudo systemctl enable --now minio.service
sudo systemctl is-active minio.service
curl -fsS http://127.0.0.1:9000/minio/health/live
```

预期服务为 `active`，健康请求退出码为 0。

### F.4 `[本机写入]` 创建 Bucket 和最小权限账号

以下策略文件放在 `/tmp`，应用后可删除。它们不包含密码。

```bash
(
set -euo pipefail
ADMIN_MC_CONFIG=$(mktemp -d /tmp/galatea-mc-admin.XXXXXX)
export MC_CONFIG_DIR="$ADMIN_MC_CONFIG"
cleanup_admin_mc() {
  unset MC_CONFIG_DIR
  if test -n "${ADMIN_MC_CONFIG:-}" && test "$ADMIN_MC_CONFIG" != /; then
    rm -rf -- "$ADMIN_MC_CONFIG"
  fi
}
trap cleanup_admin_mc EXIT
MINIO_ROOT_USER=$(sudo sed -n 's/^MINIO_ROOT_USER=//p' /etc/minio/minio.env)
MINIO_ROOT_PASSWORD=$(sudo sed -n 's/^MINIO_ROOT_PASSWORD=//p' /etc/minio/minio.env)
mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"

mc mb --ignore-existing local/training-data
mc mb --ignore-existing local/mlflow-artifacts
mc anonymous set private local/training-data
mc anonymous set private local/mlflow-artifacts
mc version enable local/training-data
mc version enable local/mlflow-artifacts

cat >/tmp/mlflow-artifacts-policy.json <<'JSON'
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket"],"Resource":["arn:aws:s3:::mlflow-artifacts"]},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":["arn:aws:s3:::mlflow-artifacts/*"]}
]}
JSON

cat >/tmp/galatea-reader-policy.json <<'JSON'
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketVersions"],"Resource":["arn:aws:s3:::training-data"]},
  {"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],"Resource":["arn:aws:s3:::training-data/*"]}
]}
JSON

cat >/tmp/data-publisher-policy.json <<'JSON'
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketVersions","s3:ListBucketMultipartUploads"],"Resource":["arn:aws:s3:::training-data"]},
  {"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion","s3:PutObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],"Resource":["arn:aws:s3:::training-data/*"]}
]}
JSON

MLFLOW_OBJECT_PASSWORD=$(sudo sed -n 's/^AWS_SECRET_ACCESS_KEY=//p' /etc/minio/mlflow-s3.env)
GALATEA_READER_PASSWORD=$(sudo sed -n 's/^AWS_SECRET_ACCESS_KEY=//p' /etc/minio/galatea-reader.env)
DATA_PUBLISHER_PASSWORD=$(sudo sed -n 's/^AWS_SECRET_ACCESS_KEY=//p' /etc/minio/data-publisher.env)
mc admin user add local mlflow "$MLFLOW_OBJECT_PASSWORD"
mc admin user add local galatea-reader "$GALATEA_READER_PASSWORD"
mc admin user add local data-publisher "$DATA_PUBLISHER_PASSWORD"
mc admin policy create local mlflow-artifacts-policy /tmp/mlflow-artifacts-policy.json
mc admin policy create local galatea-reader-policy /tmp/galatea-reader-policy.json
mc admin policy create local data-publisher-policy /tmp/data-publisher-policy.json
mc admin policy attach local mlflow-artifacts-policy --user mlflow
mc admin policy attach local galatea-reader-policy --user galatea-reader
mc admin policy attach local data-publisher-policy --user data-publisher

mc version info local/training-data
mc version info local/mlflow-artifacts

unset MINIO_ROOT_USER MINIO_ROOT_PASSWORD MLFLOW_OBJECT_PASSWORD \
  GALATEA_READER_PASSWORD DATA_PUBLISHER_PASSWORD
rm -f -- \
  /tmp/mlflow-artifacts-policy.json \
  /tmp/galatea-reader-policy.json \
  /tmp/data-publisher-policy.json
cleanup_admin_mc
trap - EXIT
unset ADMIN_MC_CONFIG
unset -f cleanup_admin_mc
)
```

命令格式是 `mc admin policy attach ALIAS POLICY --user USER`；若版本变化，先用
`mc admin policy attach --help` 核对。继续条件是两个 Bucket 私有、版本控制启用、`mlflow` 只能写
Artifact Bucket、`galatea-reader` 只能读训练数据 Bucket、`data-publisher` 只能读写训练数据 Bucket
且不能删除对象或版本。临时 `mc` 配置随 root alias 一起删除，避免管理员密码留在默认 `~/.mc`；日常
训练不能使用 MinIO root 或 `data-publisher` 凭据。

## G. 部署 MLflow Tracking 和 Artifact 代理

### G.1 `[本机写入]` 创建服务

仓库 unit 含当前旧主机的 Host/IP，不可直接照搬。新单机使用下面的 loopback-only unit：

```bash
getent group galatea-mlflow >/dev/null || \
  sudo groupadd --system galatea-mlflow
id galatea-mlflow >/dev/null 2>&1 || \
  sudo useradd --system --gid galatea-mlflow \
    --home-dir /var/lib/galatea-mlflow --shell /usr/sbin/nologin galatea-mlflow
sudo install -d -o galatea-mlflow -g galatea-mlflow -m 0750 \
  /data/ai/chenzhangyue/code/galatea/platform-data/mlflow

sudo tee /etc/systemd/system/mlflow.service >/dev/null <<'UNIT'
[Unit]
Description=MLflow Tracking Server for Galatea
Wants=network-online.target
Requires=minio.service
After=network-online.target minio.service

[Service]
Type=simple
User=galatea-mlflow
Group=galatea-mlflow
WorkingDirectory=/data/ai/chenzhangyue/code/galatea
Environment="PATH=/data/conda/envs/attend-ray-py312/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/etc/minio/mlflow-s3.env
ExecStart=/data/conda/envs/attend-ray-py312/bin/mlflow server --host 127.0.0.1 --port 5000 --backend-store-uri sqlite:////data/ai/chenzhangyue/code/galatea/platform-data/mlflow/mlflow.db --serve-artifacts --artifacts-destination s3://mlflow-artifacts --allowed-hosts localhost,localhost:5000,127.0.0.1,127.0.0.1:5000
Restart=on-failure
RestartSec=5s
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemd-analyze verify /etc/systemd/system/mlflow.service
sudo systemctl daemon-reload
sudo systemctl enable --now mlflow.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
```

预期输出 `OK`。四个 `/` 的 SQLite URI 是正确的：前三个属于协议，第四个是绝对路径开头。

### G.2 `[本机写入，非训练]` 验证 Run 和 Artifact 往返

这一步会创建一个平台安装测试 Run，但不会加载模型或更新参数：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000

python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import mlflow

mlflow.set_experiment("platform-install-check")
with mlflow.start_run(run_name="artifact-roundtrip") as run:
    payload = b"galatea artifact proxy ok\n"
    mlflow.log_text(payload.decode(), "checks/result.txt")
    with TemporaryDirectory() as directory:
        downloaded = Path(mlflow.artifacts.download_artifacts(
            run_id=run.info.run_id,
            artifact_path="checks/result.txt",
            dst_path=directory,
        ))
        assert downloaded.read_bytes() == payload
        print("run_id", run.info.run_id)
        print("sha256", hashlib.sha256(downloaded.read_bytes()).hexdigest())
PY
```

继续条件：打印一个 32 位 Run ID 和 64 位 SHA-256，MLflow UI 的 Artifact 中能看到
`checks/result.txt`。客户端只设置 Tracking URI，不持有 MLflow Server 的 MinIO 凭据。

## H. 部署单机 Ray Head

仓库原 `ray-head.service` 含旧机器 IP 和 `accelerator_type:GC50`，新机器绝不能直接安装它。下面使用
loopback 地址和自动探测 GPU，不虚报自定义资源。

### H.1 `[本机写入]` 创建服务

```bash
getent group galatea-ray >/dev/null || sudo groupadd --system galatea-ray
id galatea-ray >/dev/null 2>&1 || \
  sudo useradd --system --gid galatea-ray \
    --home-dir /var/lib/galatea-ray --shell /usr/sbin/nologin galatea-ray
getent group video >/dev/null && sudo usermod -a -G video galatea-ray
getent group render >/dev/null && sudo usermod -a -G render galatea-ray
sudo install -d -o galatea-ray -g galatea-ray -m 0750 \
  /var/lib/galatea-ray /var/cache/ray/pip
sudo tee /etc/systemd/system/ray-head.service >/dev/null <<'UNIT'
[Unit]
Description=Single-node Ray Head for Galatea
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=galatea-ray
Group=galatea-ray
WorkingDirectory=/data/ai/chenzhangyue/code/galatea
Environment="PATH=/data/conda/envs/ray-llm-py312/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="RAY_CONDA_HOME=/data/conda"
Environment="CONDA_DEFAULT_CHANNELS="
Environment="CONDA_PKGS_DIRS=/data/conda/pkgs"
Environment="PIP_CACHE_DIR=/var/cache/ray/pip"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/data/conda/envs/ray-llm-py312/bin/ray start --head --node-ip-address=127.0.0.1 --port=6379 --dashboard-host=127.0.0.1 --dashboard-port=8265 --disable-usage-stats --block
ExecStop=/data/conda/envs/ray-llm-py312/bin/ray stop --grace-period 30
Restart=on-failure
RestartSec=5s
TimeoutStartSec=60s
TimeoutStopSec=45s
LimitNOFILE=1048576
KillMode=control-group
UMask=0027
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
UNIT

sudo systemd-analyze verify /etc/systemd/system/ray-head.service
sudo systemctl daemon-reload
sudo systemctl enable --now ray-head.service
/data/conda/envs/ray-llm-py312/bin/ray status
curl -fsS http://127.0.0.1:8265/api/version | jq .
```

继续条件：Ray 版本与项目环境一致，资源列表中的 GPU 数与 `nvidia-smi -L` 一致。Ray Pending 时检查真实
CPU/GPU/内存和 placement；不能通过把一张 GPU 宣称成四张来解除阻塞。

Dashboard/Jobs API 会保存 Job 的 runtime metadata；某些 Ray CLI 版本打印完整 Job 时也会打印
`runtime_env`。因此端口必须保持 loopback 或放在严格认证代理后，只有受信管理员可访问。不要把未经
筛选的 `ray job list`、Job JSON 或 Dashboard 截图贴到工单和聊天；查看状态时只输出必要 ID 和状态。

### H.2 `[只读]` 取得并保存 Head 身份

Galatea 会把计划绑定到一个具体 Ray Head。使用 State API 读取，不要猜：

```bash
/data/conda/envs/ray-llm-py312/bin/python - <<'PY'
from ray.util.state import list_nodes

nodes = list_nodes(
    address="http://127.0.0.1:8265",
    filters=[("is_head_node", "=", True), ("state", "=", "ALIVE")],
    limit=2,
)
assert len(nodes) == 1, nodes
print(nodes[0].node_id)
PY
```

记下输出，后面写入 MCP 配置的两个 `expected_head_id`。Ray Head 重建后此 ID 会变化；MCP 应阻断旧
配置，管理员重新核对后更新，不能自动接受陌生集群。

## I. 准备模型和项目

### I.1 选择正确的参考项目

当前仓库有两个 LLM 参考面：

- `train-model/llm-lora-playground/` 适合合成数据、tokenizer/loss mask、LoRA、Artifact 和本地契约测试；
- `train-model/wechat-persona/` 已实现现行 Galatea MCP V1 的 Release、Campaign、S3 数据视图和
  `GALATEA_EXECUTION_BINDING` 验证，是正式 governed submission 的参考。

不要把二者的入口拼成一条伪造流程。尤其不要把 `llm-lora-playground` 的旧 Release 发布脚本输出直接
登记到现行 MCP；它不会生成 MCP V1 的 `projects.json/campaign.json`，其旧 Driver metadata 名称也与
现行 binding 不同。迁移一个新模型时，应复用 `wechat-persona` 的现行控制面边界，再替换项目自己的
loader、model、objective、checkpoint 和 evaluator。

### I.2 `[本机写入]` 下载不可变模型快照

先在模型发布页确认许可证，并把 `<MODEL_COMMIT>` 替换为 40 或 64 位不可变提交 ID。不要使用
`main`、`latest` 或只有模型名的可变快照作为正式身份。

```bash
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0750 \
  /data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
test -z "$(find /data/ai/chenzhangyue/code/model/Qwen3.5-0.8B \
  -mindepth 1 -print -quit)"
source /data/conda/etc/profile.d/conda.sh
conda activate ray-llm-py312

python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="Qwen/Qwen3.5-0.8B",
    revision="<MODEL_COMMIT>",
    local_dir="/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B",
)
PY

MODEL_SNAPSHOT_ROOT=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
test -f "$MODEL_SNAPSHOT_ROOT/config.json"
(
  cd "$MODEL_SNAPSHOT_ROOT"
  find . -type f ! -path './.cache/*' \
    ! -name MODEL_SNAPSHOT.sha256 -print0 \
    | LC_ALL=C sort -z \
    | xargs -0 sha256sum >MODEL_SNAPSHOT.sha256
  sha256sum -c MODEL_SNAPSHOT.sha256
)
sudo chgrp -R galatea-ray "$MODEL_SNAPSHOT_ROOT"
sudo chmod -R g+rX "$MODEL_SNAPSHOT_ROOT"
chmod -R a-w "$MODEL_SNAPSHOT_ROOT"
```

需要登录时用 `hf auth login` 的安全输入，不把 Token 写入命令行、配置或文档。随后把相同 commit 写入
项目的 `model_revision` 和 `tokenizer_revision`。逐文件校验必须全部输出 `OK`；只读权限是防误改保护，
不是权限边界，正式身份仍由 revision、文件清单和 Release 共同证明。若换模型，必须先完成第 17 节的
迁移测试，不能只改路径。

### I.3 `[只读]` 运行不产生训练的项目测试

```bash
cd /data/ai/chenzhangyue/code/galatea
source /data/conda/etc/profile.d/conda.sh
conda activate ray-llm-py312

export PYTHONPATH="$PWD/train-model/llm-lora-playground/src"
python train-model/llm-lora-playground/scripts/train_lora.py \
  --config train-model/llm-lora-playground/configs/toy-lora-smoke.yaml \
  --check-config
python -m unittest discover \
  -s train-model/llm-lora-playground/tests -p 'test_*.py'

export PYTHONPATH="$PWD/train-model/wechat-persona/src"
python train-model/wechat-persona/scripts/submit_train.py \
  --config train-model/wechat-persona/configs/formal-sft-v2-baseline.yaml \
  --check-config
python -m unittest discover \
  -s train-model/wechat-persona/tests -p 'test_*.py'
```

这组测试可以验证 split、assistant-only mask、LoRA target module、恢复、Artifact contract 和直接训练
被拒绝。两组测试都必须通过；mocked checkpoint 或 forward-only fixture 不是正式训练证据。参考项目的
现有正式配置绑定的是已有私有快照身份，新机器使用自己的获准数据时必须按 J.7 同步更新五份配置。

## J. 准备数据：从原始材料到三个密封数据包

### J.1 每条 SFT 样本的最小形状

```json
{
  "sample_id": "stable-unique-id",
  "group_id": "conversation-or-near-duplicate-group",
  "messages": [
    {"role": "system", "content": "行为约束"},
    {"role": "user", "content": "输入"},
    {"role": "assistant", "content": "目标回答"}
  ],
  "metadata": {"source": "authorized-source", "split": "train"}
}
```

最后一条必须是非空 assistant。`group_id` 代表不能拆到不同 split 的整体，例如同一次对话、同一用户、
同一模板族或近重复样本组。

### J.2 正确的数据流水线

```text
只读原始输入
  -> 授权/consent 验证
  -> 解析和 role 映射
  -> 标准化
  -> PII/secret/canary 脱敏扫描
  -> session/duplicate group
  -> 人工审核，uncertain=0
  -> 按 group 确定性切分
  -> train.jsonl / validation.jsonl / test.jsonl
  -> 每个文件 SHA-256、行数、大小、Schema 版本
  -> 上传 MinIO，记录 VersionId
```

切分前去重，切分后检查交集。test 在 candidate freeze 前只允许验证对象 metadata，不得下载内容。

### J.3 `[本机写入，数据管理员]` 准备受控输入

`wechat-persona` 参考导入器支持 `.txt`、`.csv`、`.json`、`.jsonl` 和 `.html`。源记录至少要能得到时间、
说话人和文本；微信 JSON 也可使用 `createTime/senderUsername/content/renderType/isSent` 字段。把获准的
原始导出放入受控目录，不要复制进 Git 仓库：

```bash
RAW_ROOT=/srv/galatea-private/wechat-persona/raw
CONTROL_ROOT=/srv/galatea-private/wechat-persona/control
SOURCE_EXPORT="${RAW_ROOT}/export.json"
CONSENT_FILE="${CONTROL_ROOT}/consent.json"
SPEAKER_MAP="${CONTROL_ROOT}/speaker-map.json"

sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 0700 \
  "$RAW_ROOT" "$CONTROL_ROOT"
umask 077
# 由数据管理员把获准的真实导出安全地放到 $SOURCE_EXPORT；不要在这里填写示例聊天。
test -f "$SOURCE_EXPORT"
test ! -L "$SOURCE_EXPORT"
sha256sum "$SOURCE_EXPORT"
```

Consent 不是技术人员自行宣布的许可。数据主体和组织审批完成后，由授权管理员记录范围；下面命令只把
已经作出的决定写成机器可校验 JSON，不能代替授权本身：

```bash
CONSENT_ID=replace-with-approved-consent-id
SUBJECT_ID=replace-with-pseudonymous-subject-id
EVIDENCE_REF=replace-with-controlled-approval-reference
VERIFIED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RETENTION_UNTIL=$(date -u -d '+365 days' +%Y-%m-%dT%H:%M:%SZ)
WITHDRAWAL_KEY=$(openssl rand -hex 16)

jq -n \
  --arg consent_id "$CONSENT_ID" \
  --arg subject_id "$SUBJECT_ID" \
  --arg verified_at "$VERIFIED_AT" \
  --arg retention_until "$RETENTION_UNTIL" \
  --arg withdrawal_key "$WITHDRAWAL_KEY" \
  --arg evidence_ref "$EVIDENCE_REF" \
  '{
    consent_id: $consent_id,
    subject_id: $subject_id,
    adult_verified: true,
    purposes: ["processing", "persona_style", "evaluation"],
    scope: {
      message_types: ["text"], media_types: [],
      time_start: null, time_end: null,
      third_party_policy: "exclude",
      retention_until: $retention_until,
      withdrawal_key: $withdrawal_key
    },
    status: "verified", ledger_version: "consent-v1",
    verified_at: $verified_at, evidence_ref: $evidence_ref
  }' >"$CONSENT_FILE"
chmod 0600 "$CONSENT_FILE"
unset WITHDRAWAL_KEY
```

`self_speaker` 是操作者一方在源文件中的标签，`target_speaker` 是要学习其回复风格的一方。标签本身也按
敏感数据管理，不写入日志：

```bash
SELF_SOURCE_LABEL=replace-with-exact-source-label-for-self
TARGET_SOURCE_LABEL=replace-with-exact-source-label-for-target
jq -n \
  --arg self "$SELF_SOURCE_LABEL" \
  --arg target "$TARGET_SOURCE_LABEL" \
  '{self_speaker: $self, target_speaker: $target}' >"$SPEAKER_MAP"
chmod 0600 "$SPEAKER_MAP"
unset SELF_SOURCE_LABEL TARGET_SOURCE_LABEL
```

继续条件：源文件、Consent 和 speaker map 都是受控目录中的普通文件；Consent 状态为 `verified`、未过期、
包含 `processing/persona_style/evaluation`，第三方内容默认排除。不满足时停止，不要通过改状态字段绕过。

### J.4 `[只读后本机写入，非训练]` 预检并生成 review-only 数据

先运行与写入路径相同的完整内存预检。输出只含 ID、摘要、版本、计数和聚合隐私结果：

```bash
cd /data/ai/chenzhangyue/code/galatea
source /data/conda/etc/profile.d/conda.sh
conda activate ray-llm-py312
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
export WECHAT_PERSONA_RAW_ROOT=/srv/galatea-private/wechat-persona/raw

python train-model/wechat-persona/scripts/import_chat.py \
  --config train-model/wechat-persona/configs/import.yaml \
  --check \
  --source /srv/galatea-private/wechat-persona/raw/export.json \
  --consent /srv/galatea-private/wechat-persona/control/consent.json \
  --speaker-map-file /srv/galatea-private/wechat-persona/control/speaker-map.json \
  | tee /tmp/wechat-import-preflight.json

jq -e '
  .status == "planned" and .will_write == false and
  .unknown_role_count == 0 and .duplicate_message_id_count == 0 and
  .timestamp_parse_failure_count == 0 and
  .privacy_counts.messages.hard_leak_count == 0 and
  .privacy_counts.sessions.hard_leak_count == 0 and
  .privacy_counts.candidates.hard_leak_count == 0 and
  (.candidate_counts.train > 0) and
  (.candidate_counts.validation > 0) and
  (.candidate_counts.test > 0)
' /tmp/wechat-import-preflight.json
```

只有上面的 `jq -e` 返回 0 才执行写入；写入目标必须是新目录，程序拒绝覆盖同一 dataset ID：

```bash
REVIEW_ONLY_PARENT=/srv/galatea-private/wechat-persona/review-only
IMPORT_RESULT=/srv/galatea-private/wechat-persona/control/import-result.json
install -d -m 0700 "$REVIEW_ONLY_PARENT"
umask 077

python train-model/wechat-persona/scripts/import_chat.py \
  --config train-model/wechat-persona/configs/import.yaml \
  --execute \
  --source /srv/galatea-private/wechat-persona/raw/export.json \
  --consent /srv/galatea-private/wechat-persona/control/consent.json \
  --speaker-map-file /srv/galatea-private/wechat-persona/control/speaker-map.json \
  --output-root "$REVIEW_ONLY_PARENT" \
  | tee "$IMPORT_RESULT"

jq -e '.status == "completed" and .will_write == true' "$IMPORT_RESULT"
NEW_DATASET_ROOT=$(jq -er '.dataset_root' "$IMPORT_RESULT")
test -f "$NEW_DATASET_ROOT/review/candidates.jsonl"
test -f "$NEW_DATASET_ROOT/manifests/source_manifest.json"
test -f "$NEW_DATASET_ROOT/manifests/split_manifest.json"
test -f "$NEW_DATASET_ROOT/manifests/lineage.jsonl"
test -f "$NEW_DATASET_ROOT/reports/privacy_report.json"
```

这一步只能生成脱敏消息、session、待审核候选、血缘和聚合报告，不能生成正式训练数据。预检与执行输出的
dataset/split/candidate digest 或计数不同，发现 PII/secret、未知角色、重复 ID、时间解析失败、空 split，
或者目标目录已存在时，停止并以新版本修复，不能原地改写旧版本。

### J.5 `[人工审核，本机写入，非训练]` 审核每条候选并编译

审核人只查看已脱敏候选。每条候选必须恰好记录一次 `keep`、`redact_keep` 或 `reject`；`uncertain` 表示
未完成，不能进入正式快照。先创建只允许当前审核人读取的追加日志：

```bash
DATASET_ID=$(jq -er '.dataset_id' "$NEW_DATASET_ROOT/manifests/source_manifest.json")
REVIEW_ROOT="/srv/galatea-private/wechat-persona/reviews/${DATASET_ID}-v1"
test ! -e "$REVIEW_ROOT"
install -d -m 0700 "$REVIEW_ROOT/candidates"
install -m 0600 /dev/null "$REVIEW_ROOT/events.jsonl"
install -m 0600 /dev/null "$REVIEW_ROOT/reviewed-rows.jsonl"
```

对每个 `sample_id` 重复下面过程。先提取一条候选并由有权限的人检查上下文、目标角色、残留 PII/secret、
第三方内容、媒体依赖、重复、质量与安全性：

```bash
SAMPLE_ID=replace-with-one-sample-id
CANDIDATE_FILE="$REVIEW_ROOT/candidates/current-candidate.json"
jq -c --arg id "$SAMPLE_ID" 'select(.sample_id == $id)' \
  "$NEW_DATASET_ROOT/review/candidates.jsonl" \
  >"$CANDIDATE_FILE"
test "$(wc -l <"$CANDIDATE_FILE")" -eq 1
```

保留时运行：

```bash
REVIEWER_ID=replace-with-authorized-reviewer-id
python train-model/wechat-persona/scripts/review_app.py \
  --candidate "$CANDIDATE_FILE" \
  --event-log "$REVIEW_ROOT/events.jsonl" \
  --status keep --reviewer-id "$REVIEWER_ID"
```

拒绝时使用固定原因，例如 `secret_or_credential`、`pii_not_fully_redacted`、`third_party_content`、
`wrong_target_role`、`duplicate_or_near_duplicate`、`low_quality` 或 `unsafe_or_sensitive`：

```bash
python train-model/wechat-persona/scripts/review_app.py \
  --candidate "$CANDIDATE_FILE" \
  --event-log "$REVIEW_ROOT/events.jsonl" \
  --status reject --reviewer-id "$REVIEWER_ID" \
  --reason secret_or_credential
```

确实可以安全重写时，把完整候选 JSON 复制成新文件，只修改 `messages`，人工再次检查后运行：

```bash
EDITED_CANDIDATE="$REVIEW_ROOT/candidates/current-candidate.edited.json"
test -f "$EDITED_CANDIDATE"
python train-model/wechat-persona/scripts/review_app.py \
  --candidate "$CANDIDATE_FILE" \
  --event-log "$REVIEW_ROOT/events.jsonl" \
  --status redact_keep --reviewer-id "$REVIEWER_ID" \
  --reason pii_not_fully_redacted \
  --edited-candidate "$EDITED_CANDIDATE" \
  --reviewed-row-log "$REVIEW_ROOT/reviewed-rows.jsonl"
```

全部完成后让编译器核对 ID 集合、重复事件、审核人、内容 hash、二次隐私扫描和 split：

```bash
CANDIDATE_MANIFEST_SHA256=$(jq -er '.candidate_manifest_sha256' \
  "$NEW_DATASET_ROOT/manifests/split_manifest.json")
python train-model/wechat-persona/scripts/compile_reviewed.py \
  --candidates "$NEW_DATASET_ROOT/review/candidates.jsonl" \
  --events "$REVIEW_ROOT/events.jsonl" \
  --reviewed-rows "$REVIEW_ROOT/reviewed-rows.jsonl" \
  --candidate-manifest-sha256 "$CANDIDATE_MANIFEST_SHA256" \
  --output "$REVIEW_ROOT/reviewed.jsonl" \
  --review-summary "$REVIEW_ROOT/review-summary.json"

jq -e '
  .human_review_completed == true and .uncertain_count == 0 and
  .reviewed_hard_leak_count == 0 and
  .candidate_count == .event_count and
  (.status_counts.keep + .status_counts.redact_keep == .exported_count)
' "$REVIEW_ROOT/review-summary.json"
```

审核日志和编译输出都拒绝覆盖。任一候选缺事件、重复审核、`redact_keep` 缺编辑内容、内容 hash 不一致或
隐私复扫失败时停止。数据量太大而无法全部审核时，必须先实现并冻结确定性的 selection manifest，明确
声明只审核了子集；最简单且默认的合格路线是审核全部候选。

### J.6 `[本机写入，非训练]` 生成不可变正式快照

先从机器生成的 manifest 和 review summary 组装一次性数据构建配置；使用 YAML 解析器，不能手抄摘要：

```bash
SNAPSHOT_BUILD_CONFIG="$REVIEW_ROOT/snapshot-build.yaml"
python - "$NEW_DATASET_ROOT" "$REVIEW_ROOT" "$SNAPSHOT_BUILD_CONFIG" <<'PY'
import json
import sys
from pathlib import Path
import yaml

dataset_root, review_root, output = map(Path, sys.argv[1:])
if output.exists():
    raise SystemExit(f"refusing to overwrite {output}")
repo = Path("/data/ai/chenzhangyue/code/galatea")
config = yaml.safe_load(
    (repo / "train-model/wechat-persona/configs/import.yaml").read_text()
)
source = json.loads((dataset_root / "manifests/source_manifest.json").read_text())
split = json.loads((dataset_root / "manifests/split_manifest.json").read_text())
review = json.loads((review_root / "review-summary.json").read_text())
config["dataset"].update({
    "dataset_id": source["dataset_id"],
    "source_sha256": source["source_sha256"],
    "manifest_sha256": source["manifest_sha256"],
    "split_sha256": split["split_sha256"],
    "consent_digest": source["consent_digest"],
    "preprocessing_version": source["preprocessing_version"],
    "redaction_version": source["redaction_version"],
    "scanner_version": source["scanner_version"],
    "review_evidence_digest": review["review_evidence_digest"],
})
output.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
output.chmod(0o600)
PY

FORMAL_SNAPSHOT="/srv/galatea-private/wechat-persona/formal-snapshot/${DATASET_ID}-v1"
python train-model/wechat-persona/scripts/build_dataset.py \
  --config "$SNAPSHOT_BUILD_CONFIG" --execute --check-approved \
  --reviewed-jsonl "$REVIEW_ROOT/reviewed.jsonl" \
  --source-manifest "$NEW_DATASET_ROOT/manifests/source_manifest.json" \
  --split-manifest "$NEW_DATASET_ROOT/manifests/split_manifest.json" \
  --privacy-report "$NEW_DATASET_ROOT/reports/privacy_report.json" \
  --lineage "$NEW_DATASET_ROOT/manifests/lineage.jsonl" \
  --review-summary "$REVIEW_ROOT/review-summary.json" \
  --output "$FORMAL_SNAPSHOT"

jq -e '
  .formal_training_eligible == false and
  .requires_formal_dataset_ready_approval == true and
  (.sample_counts.train > 0) and (.sample_counts.validation > 0) and
  (.sample_counts.test > 0)
' "$FORMAL_SNAPSHOT/manifest.json"
sha256sum "$FORMAL_SNAPSHOT"/{train,validation,test}.jsonl
```

快照输出使用原子、不覆盖发布。这里的 `formal_training_eligible=false` 是故意的：程序验证通过不等于人类
已经批准训练。若输入证据不一致、某个 split 为空、session/split/lineage 不一致或输出目录已存在，修复后
使用新版本，不能修改已经生成的快照。

### J.7 `[管理员审批，本机写入]` canary、`FORMAL_DATASET_READY` 和五类配置

数据管理员先做 hash/count-only canary 扫描；这属于数据快照验收，不是用 test 选模型：

```bash
CANARY_REPORT="$REVIEW_ROOT/canary-report.json"
python train-model/wechat-persona/scripts/scan_canary.py \
  --snapshot "$FORMAL_SNAPSHOT" --output "$CANARY_REPORT"
jq -e '.status == "pass" and .match_count == 0' "$CANARY_REPORT"
```

授权人核对 Consent、人工审核、隐私报告、split、canary 和数据用途后，才可签署
`FORMAL_DATASET_READY`。下面的 `APPROVER_ID` 必须是真实、有权限的人；命令只是序列化签署结果：

```bash
APPROVER_ID=replace-with-authorized-approver-id
APPROVAL_FILE="$REVIEW_ROOT/formal-dataset-ready.json"
python - "$FORMAL_SNAPSHOT" "$REVIEW_ROOT" "$NEW_DATASET_ROOT" \
  "$CANARY_REPORT" "$APPROVAL_FILE" "$APPROVER_ID" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

snapshot, review_root, dataset_root, canary_path, output = map(Path, sys.argv[1:6])
approver = sys.argv[6]
if output.exists():
    raise SystemExit(f"refusing to overwrite {output}")
manifest = json.loads((snapshot / "manifest.json").read_text())
review = json.loads((review_root / "review-summary.json").read_text())
privacy = json.loads((dataset_root / "reports/privacy_report.json").read_text())
canary = json.loads(canary_path.read_text())
if not review["human_review_completed"] or review["uncertain_count"] != 0:
    raise SystemExit("human review is incomplete")
if privacy["status"] != "pass" or privacy["hard_leak_count"] != 0:
    raise SystemExit("privacy report is blocked")
if canary["status"] != "pass" or canary["match_count"] != 0:
    raise SystemExit("canary report is blocked")
value = {
    "status": "FORMAL_DATASET_READY",
    "dataset_id": manifest["dataset_id"],
    "dataset_manifest_sha256": manifest["manifest_sha256"],
    "snapshot_manifest_sha256": hashlib.sha256(
        (snapshot / "manifest.json").read_bytes()
    ).hexdigest(),
    "split_sha256": manifest["split_sha256"],
    "consent_digest": manifest["consent_digest"],
    "review_evidence_digest": manifest["review_evidence_digest"],
    "split_counts": manifest["sample_counts"],
    "canary_report_sha256": hashlib.sha256(canary_path.read_bytes()).hexdigest(),
    "approved_by": approver,
    "approved_at": datetime.now(timezone.utc).isoformat(),
}
output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
output.chmod(0o600)
PY
```

最后，把新快照、下载模型的不可变 commit 和签署证据同步到 baseline、Trial、两个 Champion 变体以及
evaluate 五份项目配置。该脚本保留各角色原有超参数和 test 权限，只更新共同身份：

```bash
MODEL_COMMIT=replace-with-the-downloaded-immutable-model-commit
python - "$FORMAL_SNAPSHOT" "$CANARY_REPORT" "$APPROVAL_FILE" \
  "$MODEL_COMMIT" <<'PY'
import hashlib
import json
import sys
from pathlib import Path
import yaml

snapshot, canary_path, approval_path = map(Path, sys.argv[1:4])
model_commit = sys.argv[4]
if len(model_commit) not in (40, 64) or any(c not in "0123456789abcdef" for c in model_commit):
    raise SystemExit("MODEL_COMMIT must be an immutable hexadecimal revision")
manifest = json.loads((snapshot / "manifest.json").read_text())
canary = json.loads(canary_path.read_text())
approval = json.loads(approval_path.read_text())
if approval["status"] != "FORMAL_DATASET_READY":
    raise SystemExit("formal dataset approval missing")
if canary["status"] != "pass" or canary["match_count"] != 0:
    raise SystemExit("canary report is blocked")
repo = Path("/data/ai/chenzhangyue/code/galatea")
names = (
    "formal-sft-v2-baseline.yaml", "formal-sft-v2-trial.yaml",
    "formal-sft-v2-champion.yaml", "formal-sft-v2-champion-trial.yaml",
    "formal-sft-v2-evaluate.yaml",
)
for name in names:
    path = repo / "train-model/wechat-persona/configs" / name
    config = yaml.safe_load(path.read_text())
    config["dataset"].update({
        "dataset_id": manifest["dataset_id"],
        "source_sha256": manifest["source_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "split_sha256": manifest["split_sha256"],
        "consent_digest": manifest["consent_digest"],
        "preprocessing_version": manifest["preprocessing_version"],
        "redaction_version": manifest["redaction_version"],
        "scanner_version": manifest["scanner_version"],
        "review_evidence_digest": manifest["review_evidence_digest"],
        "formal_dataset_ready": True,
        "counts": manifest["sample_counts"],
    })
    config["governance"].update({
        "formal_training_eligible": True,
        "human_review_completed": True,
        "withdrawn": False,
        "pii_scan_passed": True,
        "canary_scan_passed": True,
        "cross_split_session_count": 0,
        "formal_dataset_ready_evidence": {
            "approved_by": approval["approved_by"],
            "approval_sha256": hashlib.sha256(approval_path.read_bytes()).hexdigest(),
        },
        "canary_scan_evidence": {
            "scanner_version": canary["scanner_version"],
            "report_sha256": hashlib.sha256(canary_path.read_bytes()).hexdigest(),
            "scanned_sample_count": canary["scanned_sample_count"],
            "match_count": canary["match_count"],
        },
    })
    config["model"]["model_revision"] = model_commit
    config["model"]["tokenizer_revision"] = model_commit
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
PY

git diff -- train-model/wechat-persona/configs/formal-sft-v2-*.yaml
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
python train-model/wechat-persona/scripts/submit_train.py \
  --config train-model/wechat-persona/configs/formal-sft-v2-baseline.yaml \
  --check-config
python -m unittest discover \
  -s train-model/wechat-persona/tests -p 'test_*.py'

git add train-model/wechat-persona/configs/formal-sft-v2-*.yaml
git commit -m "training: bind approved wechat persona dataset"
test -z "$(git status --porcelain)"
```

人工复核 diff 和测试后再提交代码；L.2 的 Release builder 要求 clean commit。不得仅因为脚本能写
`true` 就伪造 Consent、审核或批准，也不得把 approval 文件、原始数据或私有文本提交进 Git。

### J.8 `[本机写入，数据管理员]` 上传冻结视图并取得 VersionId

重新指向 J.6 生成的正式快照，先在本地计算证据：

```bash
DATASET_ROOT=/srv/galatea-private/wechat-persona/formal-snapshot/replace-with-snapshot-directory
DATASET_ID=$(jq -er '.dataset_id' "${DATASET_ROOT}/manifest.json")
sha256sum \
  "${DATASET_ROOT}/train.jsonl" \
  "${DATASET_ROOT}/validation.jsonl" \
  "${DATASET_ROOT}/test.jsonl"
wc -l \
  "${DATASET_ROOT}/train.jsonl" \
  "${DATASET_ROOT}/validation.jsonl" \
  "${DATASET_ROOT}/test.jsonl"
```

使用有写权限的独立发布身份上传；不要把写权限给训练 Driver：

```bash
(
set -euo pipefail
# 使用一次性 mc 配置，不把发布凭据留在默认 ~/.mc。
DATA_PUBLISHER_MC_CONFIG=$(mktemp -d /tmp/galatea-mc-publisher.XXXXXX)
export MC_CONFIG_DIR="$DATA_PUBLISHER_MC_CONFIG"
cleanup_publisher_mc() {
  unset MC_CONFIG_DIR
  if test -n "${DATA_PUBLISHER_MC_CONFIG:-}" && \
      test "$DATA_PUBLISHER_MC_CONFIG" != /; then
    rm -rf -- "$DATA_PUBLISHER_MC_CONFIG"
  fi
}
trap cleanup_publisher_mc EXIT
DATA_PUBLISHER_PASSWORD=$(sudo sed -n \
  's/^AWS_SECRET_ACCESS_KEY=//p' /etc/minio/data-publisher.env)
mc alias set data-publisher http://127.0.0.1:9000 \
  data-publisher "$DATA_PUBLISHER_PASSWORD"
unset DATA_PUBLISHER_PASSWORD

mc cp "${DATASET_ROOT}/train.jsonl" \
  "data-publisher/training-data/datasets/wechat-persona/${DATASET_ID}/train.jsonl"
mc cp "${DATASET_ROOT}/validation.jsonl" \
  "data-publisher/training-data/datasets/wechat-persona/${DATASET_ID}/validation.jsonl"
mc cp "${DATASET_ROOT}/test.jsonl" \
  "data-publisher/training-data/datasets/wechat-persona/${DATASET_ID}/test.jsonl"

mc stat --versions \
  "data-publisher/training-data/datasets/wechat-persona/${DATASET_ID}/train.jsonl"
mc stat --versions \
  "data-publisher/training-data/datasets/wechat-persona/${DATASET_ID}/validation.jsonl"
mc stat --versions \
  "data-publisher/training-data/datasets/wechat-persona/${DATASET_ID}/test.jsonl"

OBJECT_VIEWS=/srv/galatea-private/wechat-persona/control/object-views.json
test ! -e "$OBJECT_VIEWS"
for SPLIT in train validation test; do
  KEY="datasets/wechat-persona/${DATASET_ID}/${SPLIT}.jsonl"
  LOCAL_FILE="${DATASET_ROOT}/${SPLIT}.jsonl"
  OBJECT_STAT=$(mc stat --json "data-publisher/training-data/${KEY}")
  test "$(jq -er '.size' <<<"$OBJECT_STAT")" -eq "$(stat -c '%s' "$LOCAL_FILE")"
  jq -n \
    --arg split "$SPLIT" \
    --arg bucket training-data \
    --arg key "$KEY" \
    --arg version_id "$(jq -er '.versionID' <<<"$OBJECT_STAT")" \
    --arg sha256 "$(sha256sum "$LOCAL_FILE" | awk '{print $1}')" \
    --argjson size_bytes "$(stat -c '%s' "$LOCAL_FILE")" \
    '{split:$split,bucket:$bucket,key:$key,version_id:$version_id,sha256:$sha256,size_bytes:$size_bytes}'
done | jq -s 'map({key:.split,value:(del(.split))}) | from_entries' \
  >"$OBJECT_VIEWS"
chmod 0600 "$OBJECT_VIEWS"

jq -e '
  ([.train,.validation,.test] | all(
    (.version_id | type == "string" and length > 0) and
    (.sha256 | test("^[0-9a-f]{64}$")) and
    (.size_bytes | type == "number" and . > 0)
  ))
' "$OBJECT_VIEWS"

cleanup_publisher_mc
trap - EXIT
unset DATA_PUBLISHER_MC_CONFIG
unset -f cleanup_publisher_mc
)
```

`object-views.json` 只含 Bucket、Key、VersionId、SHA-256 和 size，不含样本文本。保留在受控目录，L.3
会把它原子绑定进 Registry。URI 或文件名相同不代表内容相同；没有 VersionId 的对象不能进入正式
Registry。上传后不再覆盖这些 Key；需要修正就发布新 dataset ID 和新版本。

### J.9 私有数据的额外门

私有聊天、医疗、教育、金融或个人画像数据至少需要：用途明确的授权、第三方内容策略、PII/secret
扫描、人工审核、删除/撤回流程、访问日志和最小权限。日志、MLflow params 和普通 Artifact 只保存 ID、
hash 和聚合计数，不能保存原始秘密或测试答案。数据撤回后，受影响的快照和模型要能通过 lineage 找到。

## K. 建立一个可治理项目

### K.1 目录合同

```text
train-model/<project-name>/
├── README.md
├── conda.yaml
├── galatea.project.yaml
├── configs/
│   ├── baseline.yaml
│   ├── trial.yaml
│   ├── champion.yaml
│   └── evaluate.yaml
├── schemas/
├── src/<python_package>/
│   ├── data.py
│   ├── model.py
│   ├── objective.py
│   ├── training.py
│   ├── evaluation.py
│   └── artifacts.py
├── scripts/
│   ├── submit_train.py
│   └── build_release.py
└── tests/
```

项目专属实现和测试都放在项目目录；仓库级 `tests/` 只测试跨项目行为。数据、模型权重、checkpoint、
执行后的 Notebook 和秘密不进入源码树。

### K.2 五个不可混用的入口

| 入口 | 允许做什么 | 禁止做什么 |
| --- | --- | --- |
| `--check-config` | Schema、类型、枚举、路径和秘密键检查 | 模型参数更新、Run、test |
| `--plan` | digest、数据计数、资源、模型/环境身份 | optimizer、checkpoint、正式证据 |
| fixed Driver `--run` | 只接受 MCP binding 后训练 | 接受调用者自造 metadata |
| evaluator | 只接受 test-once binding | 训练或修改候选 |
| promotion | 显式审批后改 Registry alias | 因 Run 成功自动执行 |

项目代码必须在无 binding 时拒绝 `--run`。不要仅靠文档提醒，必须有自动测试。

### K.3 配置必须回答的问题

每份配置必须明确：任务、数据和 split 指纹、预处理版本、模型/tokenizer revision、dtype、上下文长度、
训练方法、可训练参数、batch/accumulation、optimizer、learning rate、seed、主指标和方向、评估协议、
角色、test 权限、CPU/GPU/内存、超时、Artifact allowlist 和 promotability。

LoRA chat SFT 还必须验证：只对目标 assistant token 计算 loss；system/user/history/padding 为 `-100`；
目标模块真实存在；adapter 能和同一个基座架构在新进程加载。完整通用骨架见第 4、5、6 节。

## L. 构建不可变 Release 和登记材料

### L.1 `[只读]` 先检查工作树和项目计划

```bash
cd /data/ai/chenzhangyue/code/galatea
git status --short
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
python train-model/wechat-persona/scripts/submit_train.py \
  --config train-model/wechat-persona/configs/formal-sft-v2-baseline.yaml \
  --check-config
python train-model/wechat-persona/scripts/submit_train.py \
  --config train-model/wechat-persona/configs/formal-sft-v2-baseline.yaml \
  --plan
```

这里的 `wechat-persona` 命令只演示现行入口。新项目替换为自己的项目和配置。`--plan` 必须显示
`will_create_mlflow_run=false`；若显示 `blocked`，按 errors 修复，不能删掉检查。

### L.2 `[本机写入，非训练]` 构建 Release

先通过 MLflow Tracking API 取得 Experiment ID；不读取 `mlflow.db`：

```bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312
export MLFLOW_TRACKING_URI=http://127.0.0.1:5000
MLFLOW_EXPERIMENT_ID=$(python - <<'PY'
from mlflow import MlflowClient

client = MlflowClient(tracking_uri="http://127.0.0.1:5000")
name = "wechat-persona"
experiment = client.get_experiment_by_name(name)
print(experiment.experiment_id if experiment else client.create_experiment(name))
PY
)
test -n "$MLFLOW_EXPERIMENT_ID"
```

当前现行 MCP V1 参考 builder 是：

```bash
cd /data/ai/chenzhangyue/code/galatea
source /data/conda/etc/profile.d/conda.sh
conda activate ray-llm-py312
export PYTHONPATH="$PWD/train-model/wechat-persona/src"
test -n "${MLFLOW_EXPERIMENT_ID:-}"

FORMAL_SNAPSHOT=/srv/galatea-private/wechat-persona/formal-snapshot/replace-with-snapshot-directory
REGISTRATION_ROOT=/srv/galatea-private/wechat-persona/registration-v1
CAMPAIGN_APPROVER_ID=replace-with-authorized-compute-approver-id
CAMPAIGN_EXPIRES_AT=$(date -u -d '+30 days' +%s)

python train-model/wechat-persona/scripts/build_release.py \
  --output-dir /srv/galatea-private/wechat-persona/releases \
  --registration-output "$REGISTRATION_ROOT" \
  --snapshot-manifest "$FORMAL_SNAPSHOT/manifest.json" \
  --campaign-id wechat-persona-formal-sft-v2 \
  --experiment-id "$MLFLOW_EXPERIMENT_ID" \
  --approved-by "$CAMPAIGN_APPROVER_ID" \
  --expires-at "$CAMPAIGN_EXPIRES_AT" \
  | tee /tmp/wechat-persona-release-build.json

jq -e '
  .status == "built" and .uploaded == false and .registered == false and
  .training_started == false and .mlflow_run_created == false and
  (.release_id | test("^[0-9a-f]{20}$"))
' /tmp/wechat-persona-release-build.json
```

它要求 clean Git commit，输出 content-addressed ZIP、`release.json`、`projects.json` 和 `campaign.json`，但
不会上传数据、注册 Campaign、创建 MLflow Run 或训练。`CAMPAIGN_APPROVER_ID` 必须来自独立的真实算力
审批，不能沿用无权限人员的字符串。此时对象 view 仍是 builder 故意留下的管理员占位符，将在 L.3 绑定。

新项目应实现等价 builder，而不是手工压缩当前工作树。Release 必须包含固定入口、规范化配置、源码、
环境定义和文件级 hash；排除 tests、notebooks、cache、`platform-data`、symlink 和秘密。

从这里到 O 节只使用一条参考实现路线，身份必须成组保持一致：

| 身份 | 参考值 |
| --- | --- |
| 项目和 MLflow Experiment | `wechat-persona` |
| Campaign | `wechat-persona-formal-sft-v2` |
| 登记材料目录 | `/srv/galatea-private/wechat-persona/registration-v1` |
| 部署 Registry 根目录 | `/etc/galatea-mcp/registry-v1` |

迁移到新项目时，把这四项、项目环境变量、项目配置和 builder 输出一起替换为 `<PROJECT_ID>` 对应值；
不能保留一半 `wechat-persona`、再混入一半新项目身份。

### L.3 `[本机写入，管理员]` 绑定对象 view 并完成 Registry

```bash
REGISTRATION_ROOT=/srv/galatea-private/wechat-persona/registration-v1
OBJECT_VIEWS=/srv/galatea-private/wechat-persona/control/object-views.json
python - "$REGISTRATION_ROOT/projects.json" "$OBJECT_VIEWS" <<'PY'
import json
import os
import sys
from pathlib import Path

registry_path, views_path = map(Path, sys.argv[1:])
registry = json.loads(registry_path.read_text())
views = json.loads(views_path.read_text())
if set(views) != {"train", "validation", "test"}:
    raise SystemExit("object views must contain train/validation/test")
project = registry["projects"][0]
if project["project_id"] != "wechat-persona":
    raise SystemExit("unexpected project identity")
project["dataset"]["views"] = views
temporary = registry_path.with_suffix(".json.tmp")
temporary.write_text(
    json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
)
temporary.chmod(0o600)
os.replace(temporary, registry_path)
PY
```

逐项复核：项目 root、Release 相对路径/hash/code revision/environment digest、嵌入 ZIP 的 config
path/hash/seed/resources、三个对象 view、task、objective、metric definition、evaluation protocol、quality
gates、Artifact allowlist。先递归拒绝任何管理员占位符、零摘要、空 VersionId 或非正 size，再用官方
Schema 验证：

```bash
/opt/galatea-mcp/bin/python - <<'PY'
import json
import jsonschema
from pathlib import Path

repo = Path("/data/ai/chenzhangyue/code/galatea")
registration = Path("/srv/galatea-private/wechat-persona/registration-v1")
projects = json.loads((registration / "projects.json").read_text())
campaign = json.loads((registration / "campaign.json").read_text())
project_schema = json.loads((repo / "services/galatea-mcp/contracts/project.schema.json").read_text())
campaign_schema = json.loads((repo / "services/galatea-mcp/contracts/campaign.schema.json").read_text())

def walk(value, path="$"):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, f"{path}[{index}]")
    else:
        yield path, value

problems = []
for name, document in (("projects", projects), ("campaign", campaign)):
    for path, value in walk(document, name):
        text = str(value)
        if "ADMIN_" in text or "PENDING_" in text or text == "0" * 64:
            problems.append(path)
for project in projects["projects"]:
    for split, view in project["dataset"]["views"].items():
        if not view["version_id"] or int(view["size_bytes"]) <= 0:
            problems.append(f"views.{split}")
if problems:
    raise SystemExit("unresolved registry fields: " + ", ".join(problems))
for project in projects["projects"]:
    jsonschema.Draft202012Validator(project_schema).validate(project)
jsonschema.Draft202012Validator(campaign_schema).validate(campaign)
print("registry-and-campaign-schema-ok")
PY
```

Schema 和占位符检查通过后，再由管理员把生成材料与 J.7/J.8 的审批、对象 metadata 和本地摘要逐项交叉
核对。Schema 通过只证明形状正确，不证明对象、hash、权限、预算或审批是真的。

## M. 配置并启动 Galatea MCP

### M.1 `[本机写入，管理员]` 安装服务账号和登记文件

```bash
getent group galatea-mcp >/dev/null || sudo groupadd --system galatea-mcp
id galatea-mcp >/dev/null 2>&1 || sudo useradd --system --gid galatea-mcp \
  --home-dir /var/lib/galatea-mcp --shell /usr/sbin/nologin galatea-mcp
sudo install -d -o galatea-mcp -g galatea-mcp -m 0700 /var/lib/galatea-mcp
sudo install -d -o root -g galatea-mcp -m 0750 /etc/galatea-mcp
sudo install -d -o root -g galatea-mcp -m 0750 /etc/galatea-mcp/registry-v1

REGISTRATION_ROOT=/srv/galatea-private/wechat-persona/registration-v1
DEPLOY_REGISTRY_ROOT=/etc/galatea-mcp/registry-v1

# 不猜 Release 文件名；从已通过 Schema 验证的 projects.json 读取所有相对路径。
mapfile -t REGISTRY_FILES < <(
  jq -r '[.projects[].releases[].path, .projects[].configs[].path] | unique[]' \
    "${REGISTRATION_ROOT}/projects.json"
)
for RELATIVE_PATH in "${REGISTRY_FILES[@]}"; do
  case "$RELATIVE_PATH" in
    ""|/*|*\\*|*:*|*".."*) echo "unsafe registry path: $RELATIVE_PATH" >&2; exit 1 ;;
  esac
  test -f "${REGISTRATION_ROOT}/${RELATIVE_PATH}"
  sudo install -d -o root -g galatea-mcp -m 0750 \
    "$(dirname "${DEPLOY_REGISTRY_ROOT}/${RELATIVE_PATH}")"
  sudo install -o root -g galatea-mcp -m 0640 \
    "${REGISTRATION_ROOT}/${RELATIVE_PATH}" \
    "${DEPLOY_REGISTRY_ROOT}/${RELATIVE_PATH}"
done

# builder 中的 root 指向登记草稿目录；部署副本必须指向实际只读 Registry 根目录。
jq --arg root "$DEPLOY_REGISTRY_ROOT" \
  '.projects |= map(.root = $root)' \
  "${REGISTRATION_ROOT}/projects.json" >/tmp/galatea-projects.deploy.json
sudo install -o root -g galatea-mcp -m 0640 \
  /tmp/galatea-projects.deploy.json "${DEPLOY_REGISTRY_ROOT}/projects.json"
sudo install -o root -g galatea-mcp -m 0640 \
  "${REGISTRATION_ROOT}/campaign.json" "${DEPLOY_REGISTRY_ROOT}/campaign.json"
```

`projects.json` 中的 `root`、每个 Release/config 相对路径和部署后的真实文件必须完全对应。上述命令从
Registry 读取文件名，并把 Campaign 复制到服务账号可读的位置，因此不依赖猜测 builder 输出名，也不
要求 `galatea-mcp` 读取项目所有者的私有草稿目录。

### M.2 `[本机写入，管理员]` 写部署配置和秘密环境

把 `<RAY_HEAD_NODE_ID>` 替换为 H.2 的输出，把 project/campaign ID 替换为批准文件中的值：

```bash
GALATEA_SERVICE_TOKEN=$(openssl rand -hex 32)
sudo tee /etc/galatea-mcp/service.env >/dev/null <<EOF
GALATEA_SERVICE_TOKEN=${GALATEA_SERVICE_TOKEN}
$(sudo cat /etc/minio/galatea-reader.env)
MLFLOW_TRACKING_URI=http://127.0.0.1:5000
WECHAT_PERSONA_MODEL_PATH=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
EOF
unset GALATEA_SERVICE_TOKEN
sudo chown root:galatea-mcp /etc/galatea-mcp/service.env
sudo chmod 0640 /etc/galatea-mcp/service.env

sudo tee /etc/galatea-mcp/config.json >/dev/null <<'JSON'
{
  "schema_version": "galatea.deployment/v1",
  "state_root": "/var/lib/galatea-mcp",
  "registry_path": "/etc/galatea-mcp/registry-v1/projects.json",
  "principal": {
    "principal_id": "training-operator",
    "project_ids": ["wechat-persona"],
    "campaign_ids": ["wechat-persona-formal-sft-v2"],
    "actions": ["*"]
  },
  "http_port": 8791,
  "token_env": "GALATEA_SERVICE_TOKEN",
  "platform": {
    "ray_topology": "shared_serial_endpoint",
    "credential_mode": "shared_read_only",
    "trainer": {
      "address": "http://127.0.0.1:8265",
      "expected_head_id": "<RAY_HEAD_NODE_ID>",
      "runtime_env": {},
      "env_refs": {
        "AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION": "AWS_DEFAULT_REGION",
        "S3_ENDPOINT_URL": "S3_ENDPOINT_URL",
        "WECHAT_PERSONA_MODEL_PATH": "WECHAT_PERSONA_MODEL_PATH"
      }
    },
    "evaluator": {
      "address": "http://127.0.0.1:8265",
      "expected_head_id": "<RAY_HEAD_NODE_ID>",
      "runtime_env": {},
      "env_refs": {
        "AWS_ACCESS_KEY_ID": "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY": "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION": "AWS_DEFAULT_REGION",
        "S3_ENDPOINT_URL": "S3_ENDPOINT_URL",
        "WECHAT_PERSONA_MODEL_PATH": "WECHAT_PERSONA_MODEL_PATH"
      }
    },
    "tracking_uri": "http://127.0.0.1:5000",
    "s3_endpoint": "http://127.0.0.1:9000",
    "s3_region": "us-east-1",
    "signing_key_path": null,
    "artifact_download_root": "/var/lib/galatea-mcp/downloads"
  }
}
JSON
sudo chown root:galatea-mcp /etc/galatea-mcp/config.json
sudo chmod 0640 /etc/galatea-mcp/config.json
```

V1 不要求 Ed25519 key；MCP 用固定提交、不可变输入、精确 metadata 和 Ray runtime Job ID 共同绑定来源。

### M.3 `[只读/管理员写入]` 验证、注册 Campaign、预检

注册会写 Galatea 本地状态，但不会提交 Ray Job：

```bash
sudo -u galatea-mcp env -i \
  PATH=/opt/galatea-mcp/bin:/usr/bin:/bin \
  /opt/galatea-mcp/bin/galatea-mcp \
  --config /etc/galatea-mcp/config.json validate

sudo -u galatea-mcp env -i \
  PATH=/opt/galatea-mcp/bin:/usr/bin:/bin \
  /opt/galatea-mcp/bin/galatea-mcp \
  --config /etc/galatea-mcp/config.json register \
  --spec /etc/galatea-mcp/registry-v1/campaign.json

sudo -u galatea-mcp bash -c \
  'set -a; source /etc/galatea-mcp/service.env; set +a; \
   /opt/galatea-mcp/bin/galatea-mcp \
   --config /etc/galatea-mcp/config.json preflight'
```

预期依次看到 `status=valid` 和 `status=platform-reachable`。preflight 只检查 Ray Head 与 MLflow
Experiment，不训练，也不能证明 test 隔离已在真实执行中通过。

### M.4 `[本机写入]` 启动 MCP systemd 服务

```bash
cd /data/ai/chenzhangyue/code/galatea
sudo systemd-analyze verify systemd/galatea-mcp.service
sudo install -m 0644 systemd/galatea-mcp.service \
  /etc/systemd/system/galatea-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now galatea-mcp.service
sudo systemctl is-active galatea-mcp.service
sudo journalctl -u galatea-mcp.service -n 50 --no-pager
```

离线 `register/amend` 与在线 service 共用 writer lock。要修改授权时先确认没有活动/unknown Job，再停止
service，以 `galatea-mcp` 用户修改，然后重启；不能直接编辑 `/var/lib/galatea-mcp/campaigns/*.json`。

## N. 调用 MCP：plan、submit、observe

### N.1 `[本机写入，工具脚本]` 建立一个通用 MCP 调用器

HTTP MCP 不是普通 REST JSON POST。下面使用官方 MCP SDK，避免手写 initialize/session 协议：

```bash
sudo tee /usr/local/bin/galatea-call >/dev/null <<'PY'
#!/opt/galatea-mcp/bin/python
import asyncio
import json
import os
import sys
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import httpx

async def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: galatea-call TOOL JSON_ARGUMENTS")
    token = os.environ["GALATEA_SERVICE_TOKEN"]
    headers = {"Authorization": "Bearer " + token}
    async with httpx.AsyncClient(headers=headers, trust_env=False, timeout=60) as client:
        async with streamable_http_client(
            "http://127.0.0.1:8791/mcp", http_client=client
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                result = await session.call_tool(sys.argv[1], json.loads(sys.argv[2]))
                payload = result.structuredContent
                if payload is None:
                    payload = json.loads(result.content[0].text)
                print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
                if result.isError:
                    raise SystemExit(2)

asyncio.run(main())
PY
sudo chmod 0755 /usr/local/bin/galatea-call
```

在受信管理员 shell 中加载 token；不要打印它：

```bash
set -a
source /etc/galatea-mcp/service.env
set +a
galatea-call galatea_get_capabilities '{}'
galatea-call galatea_inspect_project '{"project_id":"wechat-persona"}'
galatea-call galatea_get_campaign \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2"}'
```

继续条件：响应 envelope 的 `ok=true`，项目、Campaign、config IDs、Release IDs、objective 和预算正确。

### N.2 `[只读计划]` 生成 baseline Plan

```bash
galatea-call galatea_plan_run \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "step_id":"baseline",
    "attempt":1,
    "release_id":"<RELEASE_ID>",
    "config_id":"<BASELINE_CONFIG_ID>",
    "role":"baseline"
  }' | tee /tmp/galatea-baseline-plan.json
```

plan 会写 readiness 状态但不训练。检查 `plan_id`、`readiness_digest`、`input_digest`、资源和过期时间；
任何 ID 与 Campaign slot 不一致都应被拒绝。

### N.3 `[Training Run]` 提交一次

到这里才会占用 GPU、更新 LoRA 参数、创建 MLflow Run 和 Artifact。先确认用户明确授权算力费用与本次
Campaign，再从上一步响应复制真实 `plan_id`：

```bash
galatea-call galatea_submit_job \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "plan_id":"<PLAN_ID>",
    "idempotency_key":"wechat-persona-formal-sft-v2-baseline-attempt-1"
  }' | tee /tmp/galatea-baseline-operation.json
```

只提交一次。网络超时不代表没有启动；用同一 idempotency key 或 operation 查询，不能创建新 key 盲重试。

### N.4 `[只读观察]` 等待终态

```bash
galatea-call galatea_observe_job \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "operation_id":"<OPERATION_ID>"
  }'
```

按合理间隔重复观察，状态可能是 `queued/running/succeeded/failed/stopped/unknown`。`unknown` 时运行管理员
`reconcile` 并核对原 submission，不得当作失败重提：

```bash
sudo -u galatea-mcp bash -c \
  'set -a; source /etc/galatea-mcp/service.env; set +a; \
   /opt/galatea-mcp/bin/galatea-mcp \
   --config /etc/galatea-mcp/config.json reconcile'
```

## O. 验收一次 Run，再进入 Trial、Champion 和 test-once

### O.1 `[只读]` baseline 的六项成功条件

```bash
galatea-call galatea_query_runs \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","role":"baseline"}'
galatea-call galatea_get_artifact \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","run_id":"<RUN_ID>","artifact_ref":"reports/evidence.json"}'
```

必须同时满足：

1. Ray execution 为 `succeeded`；
2. MLflow Run 为 `FINISHED`；
3. `run.outcome=succeeded`；
4. `artifact.roundtrip_verified=true`；
5. Galatea integrity 为 `verified`，每个 Artifact 的 size/hash 可通过 MLflow API 回读；
6. baseline 的 `final_test_status=not-run`，test 仍 untouched。

缺一项都不能叫 governed success。训练进度 100%、出现 adapter 文件或 Ray 退出码 0 都不够。

### O.2 `[Training Run]` Trial 只使用 train/validation

每个 Trial 使用批准的 slot、config、Release、新 Plan、新 Operation 和新 Run，但复用完全相同的 data
loader、preprocessing、training、checkpoint 和 evaluator 代码。只能修改 Campaign 已批准的超参数和
资源。选择依据是 validation，不能读取 test。

用 L.2 输出的真实 Release ID 替换占位符；一次 `plan` 只配一次 `submit`：

```bash
galatea-call galatea_plan_run \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "step_id":"trial",
    "attempt":1,
    "release_id":"<RELEASE_ID>",
    "config_id":"formal-sft-v2-trial",
    "role":"trial"
  }' | tee /tmp/galatea-trial-plan.json

galatea-call galatea_submit_job \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "plan_id":"<TRIAL_PLAN_ID>",
    "idempotency_key":"wechat-persona-formal-sft-v2-trial-attempt-1"
  }' | tee /tmp/galatea-trial-operation.json

galatea-call galatea_observe_job \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","operation_id":"<TRIAL_OPERATION_ID>"}'
```

Trial 也必须满足 O.1 的前五项，并且 `final_test_status=not-run`。Campaign 预算只批准一个 Trial 时不能
临时增加搜索次数；需要更多 Trial 就建立新审批、预算、config 和 Campaign revision。

比较所有兼容 baseline/Trial：

```bash
galatea-call galatea_compare_runs \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","run_ids":["<BASELINE_RUN_ID>","<TRIAL_RUN_ID>"]}'
```

返回的是 `best-observed-compatible-validation-only`，不是“数学上全局最优”。数据、split、指标定义或评估
协议不同的 Run 不能硬排在一起。

### O.3 `[治理写入]` 冻结候选

从 compare 结果选定 Run 和它的 `evidence_digest`：

```bash
galatea-call galatea_freeze_candidate \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","run_id":"<SELECTED_RUN_ID>","evidence_digest":"<EVIDENCE_DIGEST>"}'
```

冻结以后不能偷偷改模型、prompt、阈值、split 或协议。要改就建立新候选和新的 test 资格。

### O.4 `[Training Run]` 干净 Champion

Champion 从同一个基座 revision 和冻结配置干净开始训练，不从 Trial 的 optimizer 状态偷偷续训。它仍然
只读取 train/validation。若冻结的是 baseline，使用 `formal-sft-v2-champion`；若冻结的是当前 Trial，
使用参数相同但 role 正确的 `formal-sft-v2-champion-trial`。Galatea 会验证除 role、evaluation 和 placement
外的规范化配置、seed 和 Release 与冻结候选一致：

```bash
CHAMPION_CONFIG_ID=formal-sft-v2-champion-trial
galatea-call galatea_plan_run \
  "{\"project_id\":\"wechat-persona\",\"campaign_id\":\"wechat-persona-formal-sft-v2\",\"step_id\":\"champion\",\"attempt\":1,\"release_id\":\"<RELEASE_ID>\",\"config_id\":\"${CHAMPION_CONFIG_ID}\",\"role\":\"champion\"}" \
  | tee /tmp/galatea-champion-plan.json

galatea-call galatea_submit_job \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "plan_id":"<CHAMPION_PLAN_ID>",
    "idempotency_key":"wechat-persona-formal-sft-v2-champion-attempt-1"
  }' | tee /tmp/galatea-champion-operation.json

galatea-call galatea_observe_job \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","operation_id":"<CHAMPION_OPERATION_ID>"}'

galatea-call galatea_verify_candidate \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","candidate_id":"<CANDIDATE_ID>"}'
```

Champion 必须完成 O.1 的 Artifact 验证，`clean_start=true` 且仍为 `final_test_status=not-run`。第一次
`verify_candidate` 会核验 Champion、记录其 Run/model digest，并把 Campaign 推进到 `champion_ready`；
此时交付最多是 `best-effort`，不能发布，因为 test 仍未运行。

### O.5 `[最终测试]` 一次 evaluate

只有 Campaign 到 `champion_ready`，才能对 evaluate slot plan/submit。Galatea 会先按 test 内容 SHA-256
原子 claim；同一冻结人口不能靠改文件名重复考试。evaluator 只收到 test view 和 Champion 的模型
digest，不收到 train/validation。

以下 `submit_job` 是不可逆的 test-once 边界。执行前由独立审批人核对 Candidate ID、Champion Run/model
digest、test 对象 VersionId/hash、协议、质量门和剩余预算；没有明确最终测试授权就停止：

```bash
galatea-call galatea_plan_run \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "step_id":"evaluate",
    "attempt":1,
    "release_id":"<RELEASE_ID>",
    "config_id":"formal-sft-v2-evaluate",
    "role":"evaluate"
  }' | tee /tmp/galatea-evaluate-plan.json

galatea-call galatea_submit_job \
  '{
    "project_id":"wechat-persona",
    "campaign_id":"wechat-persona-formal-sft-v2",
    "plan_id":"<EVALUATE_PLAN_ID>",
    "idempotency_key":"wechat-persona-formal-sft-v2-evaluate-attempt-1"
  }' | tee /tmp/galatea-evaluate-operation.json

galatea-call galatea_observe_job \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","operation_id":"<EVALUATE_OPERATION_ID>"}'

galatea-call galatea_get_artifact \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","run_id":"<EVALUATE_RUN_ID>","artifact_ref":"reports/final-test-evaluation.json"}'
galatea-call galatea_verify_candidate \
  '{"project_id":"wechat-persona","campaign_id":"wechat-persona-formal-sft-v2","candidate_id":"<CANDIDATE_ID>"}' \
  | tee /tmp/galatea-delivery-report.json
```

test 失败也不能重跑后挑好看的结果；修改候选或协议需要新授权和新的 holdout。质量门通过后，
最终报告必须同时为 `outcome=accepted`、`integrity=verified`、`final_test_status=passed`，并给出可回读的
`report_ref/evidence_refs`。`galatea_verify_candidate` 只生成交付报告，不会改变生产 alias。evaluate 的
Campaign slot 固定 `max_attempts=1`；失败、超时或不通过都不能原样重考。

### O.6 `[发布]` Promotion 永远是独立动作

当前 Galatea MCP V1 明确返回 `promotion=false`，不提供模型可调用的推广工具。生产 Model Registry alias
必须由独立管理员流程完成，并要求 Artifact 完整性、test-once、隐私/安全门和人工审查全部通过。

当前 `wechat-persona` Release 只发布 PEFT adapter 文件，没有 MLflow `MLmodel` 服务包、部署健康探针或
项目 `promote/rollback` 入口，所以在这个仓库状态下正确结论是
`delivery=accepted, promotion=blocked/not-implemented`，不能照搬一个 `set_registered_model_alias` 命令
假装上线。面向生产的新项目必须先实现并测试一个独立管理员入口，至少接受
`delivery_report_ref + champion_run_id + model_sha256 + target_alias + approval_id`，验证 fresh-load/服务
兼容性，使用原子 compare-and-set 更新，写审计记录，并能把 alias 回滚到上一已验证版本。该实现进入
新的 Release 和独立权限域后，才由人类审批者执行；训练 Driver、MCP 模型调用方和自动调参器都无权调用。
不要把“训练成功”写成“已上线”。

## P. 一致性备份和恢复演练

### P.1 为什么不能只复制 adapter

完整恢复需要同时保存：MinIO 的对象和 VersionId、MLflow 元数据、Galatea Campaign/Operation/test-once
状态、Registry/Release/config、部署配置和秘密、可取回当前提交的 Git bundle、不可变基座模型，以及依法
仍需保留的 Consent、审批、review 和 lineage。只备份 adapter 会丢失数据身份、Run、授权与最终测试使用
记录；只用 `mc cp` 复制当前对象会生成新 VersionId，也不能原样恢复旧 Registry。若组织的保留规则要求
删除原始数据，先完成批准的删除并保留删除回执，不得为了备份重新保留已到期或已撤回内容。

下面针对本文的单机部署做停写一致性备份。备份目标必须是另一台机器或独立存储，并启用访问控制、传输
加密、静态加密、保留策略和定期恢复测试。备份中含对象、私有配置和凭据，敏感级别与生产数据相同。

### P.2 `[管理员写入，短暂停服]` 创建一致性备份

先确认没有 `queued/running/unknown` Operation，也没有 Ray 活动作业。不要为备份中止一个正在训练的
Run；等待它终态或按已批准的 stop 流程结束：

```bash
/data/conda/envs/ray-llm-py312/bin/python - <<'PY'
from ray.job_submission import JobSubmissionClient

client = JobSubmissionClient("http://127.0.0.1:8265")
active = []
for job in client.list_jobs():
    status = getattr(job.status, "value", str(job.status))
    job_type = getattr(job.type, "value", str(job.type))
    if status in {"PENDING", "RUNNING"}:
        active.append((job.submission_id or "-", job_type, status))
for submission_id, job_type, status in active:
    print(submission_id, job_type, status, sep="\t")
if active:
    raise SystemExit(f"active Ray jobs remain: {len(active)}")
print("no-active-ray-jobs")
PY
```

这里故意不使用会显示完整 `runtime_env` 的 CLI 输出。脚本只打印 submission ID、类型和状态；存在活动
Job 时退出码非 0，必须等待或按已批准流程处理后重新检查。

配置受控备份 SSH 目标，然后停止三个会写持久状态的服务。MinIO 必须停止后再复制物理数据目录，这样
对象版本标识才能与 Registry 保持一致：

```bash
set -euo pipefail
BACKUP_HOST=backup-user@replace-with-backup-host
BACKUP_BASE=/srv/galatea-backups
BACKUP_STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_STAGE=$(mktemp -d /var/tmp/galatea-backup.XXXXXX)
BACKUP_ARCHIVE="${BACKUP_STAGE}/galatea-${BACKUP_STAMP}.tar.gz"
REPOSITORY_ROOT=/data/ai/chenzhangyue/code/galatea
MODEL_SNAPSHOT_ROOT=/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
PRIVATE_EVIDENCE_ROOT=/srv/galatea-private/wechat-persona

WRITER_SERVICES_STOPPED=false
restart_writer_services() {
  if "$WRITER_SERVICES_STOPPED"; then
    sudo systemctl start minio.service
    sudo systemctl start mlflow.service galatea-mcp.service
  fi
}
trap restart_writer_services EXIT

test -z "$(git -C "$REPOSITORY_ROOT" status --porcelain)"
test -f "$MODEL_SNAPSHOT_ROOT/config.json"
(cd "$MODEL_SNAPSHOT_ROOT" && sha256sum -c MODEL_SNAPSHOT.sha256)
test -d "$PRIVATE_EVIDENCE_ROOT/control"
test -d "$PRIVATE_EVIDENCE_ROOT/releases"
install -d -m 0700 "$BACKUP_STAGE/var/lib/galatea-backup"
git -C "$REPOSITORY_ROOT" rev-parse HEAD \
  >"$BACKUP_STAGE/var/lib/galatea-backup/code-revision.txt"
git -C "$REPOSITORY_ROOT" bundle create \
  "$BACKUP_STAGE/var/lib/galatea-backup/galatea-source.bundle" --all
grep -Fq "$(cat "$BACKUP_STAGE/var/lib/galatea-backup/code-revision.txt") " \
  <(git bundle list-heads \
    "$BACKUP_STAGE/var/lib/galatea-backup/galatea-source.bundle")
chmod 0600 "$BACKUP_STAGE/var/lib/galatea-backup/"*

WRITER_SERVICES_STOPPED=true
sudo systemctl stop galatea-mcp.service mlflow.service minio.service
sudo tar --acls --xattrs --numeric-owner -C / -czf "$BACKUP_ARCHIVE" \
  data/ai/chenzhangyue/code/galatea/platform-data/minio \
  data/ai/chenzhangyue/code/galatea/platform-data/mlflow \
  data/ai/chenzhangyue/code/model/Qwen3.5-0.8B \
  srv/galatea-private/wechat-persona \
  var/lib/galatea-mcp \
  etc/galatea-mcp \
  etc/minio \
  etc/systemd/system/minio.service \
  etc/systemd/system/mlflow.service \
  etc/systemd/system/ray-head.service \
  etc/systemd/system/galatea-mcp.service \
  -C "$BACKUP_STAGE" var/lib/galatea-backup
sudo chown "$(id -un):$(id -gn)" "$BACKUP_ARCHIVE"
(
  cd "$BACKUP_STAGE"
  sha256sum "$(basename "$BACKUP_ARCHIVE")" \
    >"$(basename "$BACKUP_ARCHIVE").sha256"
)

ssh "$BACKUP_HOST" \
  "install -d -m 0700 '${BACKUP_BASE}/${BACKUP_STAMP}'"
rsync -a --chmod=F600 \
  "$BACKUP_ARCHIVE" "${BACKUP_ARCHIVE}.sha256" \
  "${BACKUP_HOST}:${BACKUP_BASE}/${BACKUP_STAMP}/"
BACKUP_ARCHIVE_NAME=$(basename "$BACKUP_ARCHIVE")
ssh "$BACKUP_HOST" \
  "cd '${BACKUP_BASE}/${BACKUP_STAMP}' && sha256sum -c '${BACKUP_ARCHIVE_NAME}.sha256'"

sudo systemctl start minio.service
curl -fsS http://127.0.0.1:9000/minio/health/live
sudo systemctl start mlflow.service galatea-mcp.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
sudo systemctl is-active minio.service mlflow.service galatea-mcp.service
WRITER_SERVICES_STOPPED=false
trap - EXIT
unset -f restart_writer_services
```

只有远端文件大小和 SHA-256 与本地相同，并且服务按 MinIO → MLflow → Galatea 顺序恢复健康，备份才算
完成。确认远端备份可读后，删除本地临时目录；路径必须仍是 `mktemp` 返回的具体目录：

```bash
test -n "$BACKUP_STAGE" && test "$BACKUP_STAGE" != / && \
  rm -rf -- "$BACKUP_STAGE"
```

这里的物理目录访问只允许停服备份管理员使用。训练客户端和验收程序仍必须通过 MinIO、MLflow Artifact
和 Tracking API，不能把备份例外当成日常集成接口。

### P.3 `[只读/隔离写入]` 恢复演练

至少定期在隔离目录验证归档，而不是等故障后才第一次尝试。下载一个明确版本的备份并校验：

```bash
RESTORE_STAMP=replace-with-tested-backup-stamp
RESTORE_STAGE=$(mktemp -d /var/tmp/galatea-restore.XXXXXX)
rsync -a \
  "${BACKUP_HOST}:${BACKUP_BASE}/${RESTORE_STAMP}/" \
  "$RESTORE_STAGE/"
cd "$RESTORE_STAGE"
sha256sum -c galatea-*.tar.gz.sha256

DRILL_ROOT=$(mktemp -d /var/tmp/galatea-restore-root.XXXXXX)
sudo tar --acls --xattrs --numeric-owner \
  -C "$DRILL_ROOT" -xzf galatea-*.tar.gz
sudo sqlite3 \
  "$DRILL_ROOT/data/ai/chenzhangyue/code/galatea/platform-data/mlflow/mlflow.db" \
  'PRAGMA integrity_check;'
sudo test -d \
  "$DRILL_ROOT/data/ai/chenzhangyue/code/galatea/platform-data/minio/data"
sudo test -d "$DRILL_ROOT/var/lib/galatea-mcp/campaigns"
sudo test -d "$DRILL_ROOT/var/lib/galatea-mcp/evaluation-uses"
sudo test -f "$DRILL_ROOT/etc/galatea-mcp/registry-v1/projects.json"
sudo test -f \
  "$DRILL_ROOT/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B/config.json"
(
  cd "$DRILL_ROOT/data/ai/chenzhangyue/code/model/Qwen3.5-0.8B"
  sudo sha256sum -c MODEL_SNAPSHOT.sha256
)
sudo test -d "$DRILL_ROOT/srv/galatea-private/wechat-persona/control"
SOURCE_BUNDLE="$DRILL_ROOT/var/lib/galatea-backup/galatea-source.bundle"
BACKUP_CODE_REVISION=$(sudo cat \
  "$DRILL_ROOT/var/lib/galatea-backup/code-revision.txt")
sudo git bundle list-heads "$SOURCE_BUNDLE" \
  | grep -Fq "$BACKUP_CODE_REVISION "
```

预期 SHA 全部 `OK`、SQLite 输出 `ok`，Campaign、Operation、`evaluation-uses`、Release、config 和 MinIO
对象目录都存在，基座模型、私有证据和源码提交也能找到。演练目录仍含敏感数据；验证后按组织的数据
销毁流程处理，不能留在共享 `/tmp`。

### P.4 `[灾难恢复，破坏性管理员动作]` 恢复到替换机器

只有灾难恢复审批和目标路径复核后才能执行本节；它会覆盖目标机器同名状态。先按 C-E 安装相同架构和
Python 环境，再按 F-H 安装相同版本的二进制、创建用户并放置 unit，但跳过所有 `enable --now`/`start`
命令。源码目录可以是 D.1 得到的干净 clone。校验归档并确认目标确实是空的新机器或批准的恢复目标，
然后停止服务并恢复：

```bash
sudo systemctl stop galatea-mcp.service mlflow.service minio.service
sudo tar --acls --xattrs --numeric-owner -C / -xzf \
  /secure/restore/galatea-approved-backup.tar.gz

sudo chown -R minio:minio \
  /data/ai/chenzhangyue/code/galatea/platform-data/minio
sudo chown -R galatea-mlflow:galatea-mlflow \
  /data/ai/chenzhangyue/code/galatea/platform-data/mlflow
sudo chown -R root:galatea-ray \
  /data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
sudo chmod -R g+rX,a-w \
  /data/ai/chenzhangyue/code/model/Qwen3.5-0.8B
sudo chown -R galatea-mcp:galatea-mcp /var/lib/galatea-mcp
sudo chown -R root:galatea-mcp /etc/galatea-mcp
sudo chmod 0640 /etc/galatea-mcp/config.json /etc/galatea-mcp/service.env
sudo chmod 0600 \
  /etc/minio/minio.env \
  /etc/minio/mlflow-s3.env \
  /etc/minio/galatea-reader.env \
  /etc/minio/data-publisher.env

REPOSITORY_ROOT=/data/ai/chenzhangyue/code/galatea
SOURCE_BUNDLE=/var/lib/galatea-backup/galatea-source.bundle
BACKUP_CODE_REVISION=$(sudo cat /var/lib/galatea-backup/code-revision.txt)
sudo chown -R "$(id -un):$(id -gn)" /var/lib/galatea-backup
test -d "$REPOSITORY_ROOT/.git"
test -z "$(git -C "$REPOSITORY_ROOT" status --porcelain)"
git -C "$REPOSITORY_ROOT" fetch "$SOURCE_BUNDLE" \
  '+refs/heads/*:refs/remotes/recovery/*'
git -C "$REPOSITORY_ROOT" cat-file -e "${BACKUP_CODE_REVISION}^{commit}"
git -C "$REPOSITORY_ROOT" checkout --detach "$BACKUP_CODE_REVISION"

sudo systemctl daemon-reload
sudo systemctl start minio.service
curl -fsS http://127.0.0.1:9000/minio/health/live
sudo systemctl start mlflow.service ray-head.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health

NEW_RAY_HEAD_ID=$(
  /data/conda/envs/ray-llm-py312/bin/python - <<'PY'
from ray.util.state import list_nodes

nodes = list_nodes(
    address="http://127.0.0.1:8265",
    filters=[("is_head_node", "=", True), ("state", "=", "ALIVE")],
    limit=2,
)
assert len(nodes) == 1, nodes
print(nodes[0].node_id)
PY
)
test -n "$NEW_RAY_HEAD_ID"
sudo jq --arg head_id "$NEW_RAY_HEAD_ID" '
  .platform.trainer.expected_head_id = $head_id |
  .platform.evaluator.expected_head_id = $head_id
' /etc/galatea-mcp/config.json >/tmp/galatea-config.restored.json
sudo install -o root -g galatea-mcp -m 0640 \
  /tmp/galatea-config.restored.json /etc/galatea-mcp/config.json

sudo -u galatea-mcp env -i \
  PATH=/opt/galatea-mcp/bin:/usr/bin:/bin \
  /opt/galatea-mcp/bin/galatea-mcp \
  --config /etc/galatea-mcp/config.json validate
sudo -u galatea-mcp bash -c \
  'set -a; source /etc/galatea-mcp/service.env; set +a; \
   /opt/galatea-mcp/bin/galatea-mcp \
   --config /etc/galatea-mcp/config.json preflight'
sudo -u galatea-mcp bash -c \
  'set -a; source /etc/galatea-mcp/service.env; set +a; \
   /opt/galatea-mcp/bin/galatea-mcp \
   --config /etc/galatea-mcp/config.json reconcile'
sudo systemctl start galatea-mcp.service
sudo systemctl is-active \
  minio.service mlflow.service ray-head.service galatea-mcp.service
```

恢复后 Ray Head ID 通常会变化。上面的顺序先核对唯一的新 Head，再更新 trainer/evaluator 两个绑定并
完成 `validate/preflight/reconcile`，最后才启动 MCP，不能自动接受陌生集群。若故障或备份介质可能泄露
凭据，还要轮换 MCP Token 和三个最小权限对象存储账号并同步对应环境文件。最后通过官方 API 回读一个
已知 MLflow Run/Artifact，确认 Registry 中每个对象 VersionId、Artifact hash、Campaign、Operation 和
test-once 标记都存在。任何一项缺失都表示恢复未完成，不能继续训练或重新使用 test。

## Q. 新机器最终验收表

完成下面所有项目，才可以说“一台新机器已按本文实现完整受治理训练闭环”：

| 阶段 | 必须可回读的结果 |
| --- | --- |
| 系统 | OS/架构、CPU/RAM/磁盘、驱动和 GPU 清单 |
| 环境 | 三个隔离环境、精确包版本、`pip check`、CUDA 可用性 |
| MinIO | loopback health、私有 Bucket、versioning、三个最小权限身份 |
| MLflow | health、测试 Run、Artifact 上传下载内容一致 |
| Ray | Head ID、版本、资源、Jobs API 可达 |
| 项目 | check/plan 只读、无 binding 的 run 被拒绝、项目测试通过 |
| 数据 | 授权、Schema、计数、manifest/split digest、三 view VersionId、无交叉 |
| 模型 | model/tokenizer immutable revision、架构、许可、fresh load |
| Release | clean commit、ZIP/config/environment hash、固定入口、无 secret |
| Campaign | 审批人、expiry、slot、attempt、预算和 final reserve |
| 执行 | Plan、Operation、Submission、Attempt、Ray Job、MLflow Run 可互相追溯 |
| 训练 | train/validation 指标、checkpoint、adapter/model、资源和时间证据 |
| 完整性 | Artifact API round-trip、每文件 size/hash、fresh-process load |
| 选择 | 只用兼容 validation evidence 比较并冻结候选 |
| 最终评价 | Champion 干净训练、test-once、质量/隐私/安全门 |
| 发布 | 独立人工审批和可审计 alias 更新，或明确记录“未发布” |
| 恢复 | 失败/中断保留，新 Attempt 不覆盖旧 Run；备份恢复演练通过 |

如果只需要验证平台而没有训练授权，验收到 Ray、MCP preflight 和项目只读测试后停止，状态写
`platform_ready/training_not_authorized`。这不是失败，而是正确执行权限边界。

---

# 第二篇：原理、真实复盘和跨模型合同

第一篇解决“从零怎么做”；第二篇解释“为什么这样做、如何判断证据、怎样迁移到别的任务”。

## 0. 先看结论：这份目录能否迁移到其他微调任务

结论是：**本文已经覆盖新机器平台部署、受治理执行顺序和跨模型合同；任意新模型仍必须在自己的项目
目录中实现并测试模型/任务专属组件。** “完整”表示没有省略生命周期步骤，不表示一套训练代码能无条件
支持所有模型族。第一篇给出可操作流程，本篇把两层内容明确分开：

| 层次 | 本指南提供的内容 | 新项目仍需提供的内容 |
| --- | --- | --- |
| 通用控制面 | 数据快照、授权、Release、计划、执行绑定、Run、Artifact、评估、候选冻结、test-once、promotion | 项目注册、审批人、资源和后端适配 |
| 通用证据 | 身份、血缘、配置、环境、资源、指标、哈希、恢复、治理状态 | 项目指标的具体计算和阈值 |
| LLM 共性 | tokenizer/processor 版本、输入输出协议、seed、截断、checkpoint round-trip、隐私/安全门 | 模型族的 label、loss、生成或打分实现 |
| 当前实例 | Qwen3.5、聊天 SFT、LoRA、单 GPU Ray、MLflow | 换成其他模型、数据、PEFT/全参方法时的配置和测试 |

因此“对应其他微调训练”的判定条件不是复制命令，而是满足以下不变量：

1. 新项目有自己的 `galatea.project.yaml`、固定参数化入口、环境定义、Schema 和项目文档。
2. 后端由项目契约声明；训练、恢复和持久评测只能走该后端的固定入口。
3. 训练方法只替换可插拔的 `data -> model -> objective -> optimizer -> checkpoint -> evaluator` 组件，
   不改变授权、身份、证据、失败重试和晋级边界。
4. 新任务有明确的主指标、优化方向、验证集选参规则和最终测试规则。
5. 端到端测试证明 check/plan 不训练、Driver 拒绝无授权调用、Artifact 可回读、新进程可加载，
   且 test 不会被实验阶段读取。

若缺少其中任意一项，项目状态应为 `blocked` 或 `contract-incomplete`，不能用本地结果补齐治理证据。

### 0.1 控制面与工作负载面

迁移时把系统拆成两个平面，可以避免把本次 Qwen/LoRA 的偶然细节误当成平台规则：

```text
控制面（所有任务共用）
授权 -> 数据/代码/环境身份 -> Release -> readiness/plan -> 执行绑定
     -> Run/Artifact 证据 -> 比较/冻结 -> test-once -> review -> promotion

工作负载面（按模型和任务替换）
数据解析/预处理 -> 模型与 tokenizer/processor -> objective/loss
     -> 优化器与并行策略 -> checkpoint 格式 -> 任务评估器 -> 服务适配器
```

控制面决定“谁可以以什么身份运行、证据是否可信、结果能否晋级”；工作负载面决定“模型如何学习和
如何测量”。工作负载实现可以变，控制面状态不能被变通。

---

## 1. 最小可治理闭环

任何正式微调都必须经过下面这条链路：

~~~text
授权的数据快照
  -> 固定、可复算的 train/validation/test split
  -> 项目契约和只读 check/plan
  -> clean commit 构建 immutable Release
  -> 管理员登记 Release、Campaign 和资源预算
  -> Galatea plan_run 生成 readiness digest
  -> 以相同 plan 一次性 submit_job
  -> 通过声明后端的固定 Driver 创建唯一 MLflow Run
  -> train + validation-only checkpoint 选择
  -> MLflow Artifact API 上传、下载、哈希和新进程加载
  -> 执行后端、MLflow、Galatea 三方终态一致
  -> 仅用 train/validation 比较和冻结候选
  -> Champion 的 test-once
  -> 人工/安全审查
  -> 单独的显式 promotion
~~~

当前 Ray 项目中，以下动作不能替代这条链路；其他后端对应的通用提交命令也同样不能绕过项目 Driver：

- 直接运行 python train.py 或 python scripts/submit_train.py --run；
- 通用 ray job submit；
- Notebook 中跑“快速 baseline”；
- 把本地生成的 adapter、loss 或截图补写成 governed evidence；
- 用 experimental_only、promotable=false、小数据、一个 epoch 或一张 GPU 作为后端例外；
- 直接读取 mlflow.db 或服务端 MinIO 文件系统验证 Artifact。

这些字段只决定授权、证据用途和 promotion 权限，不改变执行后端。

---

## 2. 本次实际运行的范围和最终状态

本次任务只做一个 wechat-persona baseline：

- 不访问 test；
- 不启动 Trial；
- 不冻结 candidate；
- 不注册模型版本，不修改 Registry alias；
- 使用 1 GPU、batch_size=4、eval_batch_size=4、1 epoch；
- 使用 LoRA adapter，不复制完整 base model 作为 Artifact。

本节记录一个已经结束的历史 Run 及其点时证据，不代表当前数据快照自动获得后续训练授权。任何新的
baseline、Trial 或恢复运行仍需重新满足当前项目的 consent、review、formal snapshot、Release 和
execution binding；当前项目文档中的 `formal_evidence_blocked` 状态不因这份复盘而改变。

### 2.1 不可变身份

| 对象 | 值 |
| --- | --- |
| 项目 | wechat-persona |
| task | causal-language-model-sft-lora |
| Release | 9cb590e7effb3e2117dd |
| Release SHA-256 | 9cb590e7effb3e2117dd84c315f10a7489f10cfe0e2a3a0ea05dedd79c8ab1ae |
| Release code revision | 9cf1959519c52b43ff46f87e48fdd769626907c0 |
| Campaign | wechat-persona-baseline-recovery-20260909-9cf1959 |
| Plan | plan-8ed6e1baae4a76c0a26232894ab5c3f8 |
| readiness digest | 8ed6e1baae4a76c0a26232894ab5c3f8f75e2be169e68bfb31483870eb48fa65 |
| Operation | op-4debbe64150015f66cbc584755d93cf2 |
| Ray submission | galatea-py-4debbe64150015f66cbc584755d93cf2 |
| MLflow Run | 567da82aae694f4fac7835cb44802ac4 |
| attempt | 1 |
| role | baseline |
| objective | val_loss，direction=min |

### 2.2 终态证据

| 项目 | 结果 |
| --- | --- |
| Ray | SUCCEEDED，driver exit code 0 |
| MLflow | FINISHED |
| run.outcome | succeeded |
| artifact.roundtrip_verified | true |
| Galatea execution | succeeded |
| Galatea integrity | verified |
| test access | untouched |
| evidence final_test_status | not-run |
| compare_runs | 有 ranking，rejected=[] |
| Trial/Champion/evaluate Runs | 0 |
| Registry model versions for this Run | 0 |

训练指标：

| metric | value |
| --- | ---: |
| train_loss | 4.121612131394869 |
| val_loss | 3.976290464401245 |
| val_perplexity | 53.318878643709716 |

这些数值是本次固定数据、模型 revision、配置和评估协议下的 baseline 事实，不能直接当成其他模型或其他数据集的预期值。

---

## 3. 失败历史和修复经验

### 3.1 训练完成但 Driver 尾部超时

早期 baseline 使用 3600 秒授权时限。训练本体约 2798 秒完成，随后 Driver 还要做逐样本 validation、checkpoint 上传、Artifact 下载回读和新进程加载，最终撞到 watchdog deadline。

关键结论：

- GPU 没有 OOM；
- 训练参数已经更新；
- Ray Job 仍必须标记 FAILED；
- 失败 Run 必须保留，不能事后改成成功。

正式预算必须覆盖：

~~~text
总预算 >= data + model load + tokenize + train + validation
       + checkpoint + artifact upload + artifact download/verify
       + cleanup + safety margin
~~~

训练时间不等于总 wall time。

### 3.2 Artifact 远端文件名不一致

旧实现依赖目录行为上传 best-adapter.safetensors，导致远端实际文件名可能不符合契约。修复后强制：

- 本地源文件和远端文件名完全一致；
- 远端路径必须是相对路径；
- 禁止 .. 穿越；
- 上传前检查 source basename 与 remote basename；
- 上传后立刻通过 Artifact API 下载并复算 SHA-256。

### 3.3 重复 validation 造成 metric 漂移

Trainer 已在 eval_strategy=epoch 下产生权威 eval_loss。训练结束再跑一套逐样本 validation 会增加耗时，并可能因 padding、batch、精度或 reduction 差异产生不同数值。

修复后直接复用 Trainer log_history 中最后一个 eval_loss，使 MLflow、validation-quality.json 和 evidence.json 使用同一数值。

如果项目确实需要逐样本指标，必须批量计算、明确归一化方式并把时间计入 deadline。

### 3.4 MLflow 逐条写 history 太慢

约 9353 个 step，每步含 loss、learning rate 和 gradient norm。逐条调用 log_metric 会制造大量网络往返。

修复后：

- 使用 log_batch；
- 单批最多 1000 个 Metric；
- 同步提交；
- 将 aggregate train_loss、val_loss、val_perplexity 写在最高 history step 之后；
- evaluation role 没有 Trainer history，才单独写最终指标。

这样既保留可诊断 history，也避免 summary 被最后一个 step loss 覆盖。

### 3.5 Campaign 并发覆盖导致 lineage 失效

曾经有一个 Ray/MLflow 成功的 Run，但同名 Campaign 被并发流程覆盖。Operation 绑定旧 Release，当前 Campaign 指向新 Release，Galatea 因 lineage 不一致拒绝了证据。

规则：

- 不手工改写历史 Operation；
- 每次 retry 使用唯一 Campaign 名；
- project、campaign、operation、release、readiness 必须互相一致；
- writer lock 被拦截时按单写者流程处理；
- 用官方 API 重新读取状态，不依赖本地猜测。

### 3.6 离线登记文件权限

离线 register 曾生成服务账号无法读取的 state 文件，MCP 返回 state-corrupt。这是部署边界问题，不是训练代码问题。

通用处理：

1. 备份部署配置；
2. 确认没有活动 Ray Job；
3. 短暂停 MCP service 释放 writer lock；
4. 以服务账号离线 register/amend；
5. 恢复服务；
6. 通过官方 MCP API 读取 Campaign 和 operations；
7. 检查 owner/group/mode 最小且服务账号可读。

### 3.7 Dirty-worktree 测试必须真实可重复

最后审计发现一个测试错误地依赖主工作树恰好 dirty。修复为在临时 Git 仓库创建最小项目和未跟踪文件，真实触发 clean Git commit gate。

测试不能依赖当前开发者环境的偶然状态。

---

## 4. 项目契约：把模型训练变成可治理对象

每个新模型项目至少有：

~~~text
train-model/<project>/
├── README.md
├── galatea.project.yaml
├── configs/
├── src/<package>/
├── scripts/
├── tests/
└── conda.yaml
~~~

galatea.project.yaml 至少明确：

~~~yaml
apiVersion: galatea/v1
kind: TrainingProject
metadata:
  name: <project-id>
spec:
  task: <task-name>
  executionBackend: ray
  objective:
    metric: val_loss
    direction: min
  entrypoints:
    checkConfig: [python, scripts/submit_train.py, --check-config]
    plan: [python, scripts/submit_train.py, --plan]
    train: [python, scripts/submit_train.py, --run]
  mlflow:
    experimentName: <experiment-name>
    trackingUriEnv: MLFLOW_TRACKING_URI
~~~

代码必须实现：

- check-config 只读，不能创建 Run；
- plan 只读，不能更新参数或生成正式 checkpoint；
- run 必须要求 immutable Release、readiness digest、execution identity、submission identity 和 role；
- 缺少 binding 时 fail closed；
- 训练、验证、checkpoint、上传、Artifact round-trip 由同一个固定 Driver 负责；
- check/plan、Notebook、普通后端提交命令不能绕过 Driver；
- retry 创建新 Run/attempt，不覆盖旧 Run 或 Artifact；
- objective metric 和优化方向显式声明。

### 4.1 哪些配置可以变化

| 层 | 例子 | 规则 |
| --- | --- | --- |
| 数据身份 | dataset/split digest、对象 VersionId | 可变，但必须新 evidence |
| 角色 | baseline、trial、champion、evaluate | 必须有授权 slot |
| 资源 | accelerator、CPU、memory、timeout、batch | 改变后重新 plan |
| 超参数 | learning rate、rank、epochs、max length | 新 config digest |
| test 访问 | baseline/Trial 不访问，evaluate 单独授权 | 不能隐式改变 |
| 执行架构 | 固定项目 Driver、声明的后端、Artifact/MLflow 代码 | 不按实验分叉 |
| 数据加载/loss | 项目共享实现 | 不复制 ad-hoc 版本 |

### 4.2 通用配置骨架（方法和框架无关）

下面的骨架是迁移新模型时应先设计的 schema。字段名可以按项目规范调整，但不能删掉其语义。
`method`、`task` 和 `backend` 是可扩展枚举；`identity`、`governance`、`resources`、`evaluation` 和
`artifacts` 是每次训练都必须有的控制字段。

```yaml
apiVersion: galatea/v1
kind: TrainingProject
metadata: {name: <project-id>}
spec:
  task: <causal-lm-sft|seq2seq-sft|dpo|reward-model|multimodal|...>
  executionBackend: <ray|batch|kubernetes|other-governed>
  entrypoints: {checkConfig: [...], plan: [...], train: [...]}
  objective: {metric: <metric-name>, direction: <min|max>}
  model:
    id: <immutable-model-id>
    revision: <commit-or-snapshot>
    tokenizerOrProcessorRevision: <immutable-revision>
    architecture: <declared-architecture>
  method:
    name: <full-finetune|lora|qlora|ia3|prefix|dpo|...>
    parameters: <complete-method-parameters>
  data:
    datasetId: <immutable-id>
    manifestSha256: <digest>
    splitSha256: <digest>
    preprocessingVersion: <version>
  evaluation:
    protocolVersion: <frozen-version>
    testPolicy: <untouched|once-after-candidate-freeze>
  artifacts:
    allowlist: [<manifest>, <checkpoint-or-adapter>, <reports>, <recovery-metadata>]
  governance:
    role: <smoke|baseline|trial|champion|evaluate>
    promotable: false
    approvalRef: <authorization-id>
  resources: {cpus: 4, gpus: 1, memoryBytes: <n>, timeoutSeconds: <n>}
```

运行时再将该配置与 Release、Plan、Operation、Submission 和 Attempt 绑定。`other-governed` 只有在项目
契约明确声明可恢复、可审计且有固定入口时才可使用；普通本地进程不满足此条件。不要把凭据、原始文本、
可变 `latest` 模型标签或未验证的路径写入配置。

### 4.3 状态、责任和不可跳过的转换

| 状态 | 责任主体 | 必须存在的证据 | 允许的下一步 |
| --- | --- | --- | --- |
| `discovered` | 数据负责人 | 来源、用途、授权引用 | `authorized` 或 `blocked` |
| `snapshot_ready` | 数据工程 | manifest、内容 digest、split、质量/隐私报告 | `release_ready` |
| `release_ready` | 训练负责人 | clean Release、环境 digest、配置 digest | `planned` |
| `planned` | 平台/治理服务 | readiness、资源、role、expiry、idempotency key | `submitted` |
| `submitted/running` | 执行后端 | Job/attempt metadata、运行日志 | `succeeded` 或 `failed` |
| `succeeded` | Driver/MLflow | Run、Artifact 哈希、fresh-load、终态指标 | `trial_evidence` |
| `trial_evidence` | 评估负责人 | 兼容比较、validation ranking | `candidate_frozen` 或 `rejected` |
| `candidate_frozen` | 审批流程 | freeze digest、选择规则、活动作业已终态 | `test_once` |
| `test_once` | Champion/evaluator | 原子 test claim、final report | `review` |
| `review` | 人工/安全负责人 | 质量、安全、隐私、人工审查 | `promoted` 或 `rejected` |

任何失败、撤回、证据不一致或权限过期都转为 `blocked/failed/withdrawn`，而不是跳到下一状态。

---

## 5. 数据、隐私和 split

### 5.1 数据是第一份证据

每次 Run 至少记录：

~~~json
{
  "dataset_id": "<immutable-dataset-id>",
  "manifest_sha256": "<64-hex>",
  "split_sha256": "<64-hex>",
  "preprocessing_version": "<version>",
  "source_or_manifest_digest": "<64-hex>",
  "train_view_version_id": "<object-version>",
  "validation_view_version_id": "<object-version>",
  "test_view_version_id": "<object-version>",
  "test_access": "untouched"
}
~~~

train、validation、test view 应是独立不可变对象版本，并记录内容 SHA-256 和大小。不要读取会变化的 latest 文件名。

### 5.2 隐私治理

真实聊天或私有数据要有：

- consent ledger 和用途校验；
- PII、secret、canary 扫描；
- role/speaker 映射；
- 第三方内容和媒体策略；
- review event，uncertain=0；
- source、normalized、session、candidate、reviewed、formal snapshot 的 digest；
- 删除和撤回路径。

扫描输出只保存对象 ID、hash 和计数，不把秘密、手机号、聊天原文或生成正文写入普通日志、Git 或 MLflow params。

### 5.3 Split 原则

- 优先按 session、对话组、scenario、模板族或近重复族分组；
- 有可靠时间字段时才使用 deterministic chronological split；
- 冻结后的 validation/test population 不得静默重排；
- test 不用于超参搜索、早停、checkpoint 选择或阈值选择；
- test 只能在 candidate freeze 后，通过 Champion/evaluate authorization 使用一次；
- split、预处理、评估协议或 prompt 改变时，旧 test evidence 失效。

---

## 6. 模型、tokenizer 和 loss mask

### 6.1 Immutable model identity

不要只记录可变仓库名。至少记录：

~~~text
model_id
model_revision
tokenizer_id
tokenizer_revision
environment_digest
~~~

没有网络时可以由受保护 runtime environment 提供本地 immutable snapshot，但 binding 仍记录原始 ID 和 revision。训练代码不能让用户任意替换本地模型路径。

### 6.2 Assistant-only SFT

对于 chat SFT，loss 只在目标 assistant token 上计算：

- system、user、历史 assistant、padding 和截断部分使用 -100；
- 最后一条必须是非空 assistant response；
- 无法识别 assistant span 直接失败；
- 不要把整个 prompt+answer 当作等价的 full-sequence loss；
- chat template、thinking 开关和 truncation 规则必须版本化。

至少写 forward-only 测试检查：

1. assistant label 数大于 0；
2. system/user label 全是 -100；
3. padding 不参与 loss；
4. 超长输入不会静默截掉全部目标；
5. batch padding 前后单样本 loss 在容许误差内一致。

### 6.3 LoRA/QLoRA

至少记录：

- rank、alpha、dropout；
- target modules 是否真实存在；
- base dtype；
- quantization method 和 bitsandbytes/torch/transformers/CUDA revision；
- adapter-only checkpoint；
- fresh-process base+adapter load；
- trainable parameter count；
- peak GPU memory。

QLoRA 仍需独立完成量化加载、forward/backward、保存、Artifact round-trip 和新进程加载 preflight。

### 6.4 微调方法选择：共享治理，不共享假设

LoRA 只是本次运行的参数更新方式。换方法时，以下治理字段和生命周期不变，但监督信号、可训练
参数、资源估算、checkpoint 和评估器必须重新定义并测试：

| 方法 | 更新对象 | 必须记录/验证 | 常见错误 |
| --- | --- | --- | --- |
| 全参数 SFT | base model 全部或声明的参数集合 | trainable 参数清单、冻结策略、优化器状态、完整模型 checkpoint、参数量和显存 | 把 adapter-only 文件当完整模型；遗漏冻结层 |
| LoRA/其他 PEFT | adapter 参数 | rank/alpha/dropout、target module、可训练参数量、adapter config、base revision、fresh-load | target module 不存在却静默跳过；adapter 与服务架构不一致 |
| QLoRA | 量化 base + adapter | bits、量化类型、compute dtype、库/CUDA revision、反量化/合并策略、显存 | 只验证加载不验证 backward 或新进程加载 |
| Prefix/Prompt/IA³ | 虚拟 token 或缩放参数 | prompt/virtual-token 版本、初始化、注入层、导出协议 | 推理端未加载同一 prompt/adapter 配置 |
| DPO/IPO/偏好优化 | chosen/rejected 相对偏好 | reference model revision、pair identity、偏好 split、beta/loss 定义、KL/拒答门 | 把 chosen 文本泄漏到输入；用 test 调 beta 或早停 |
| Reward model | 标量奖励/排序器参数 | pair/group split、标签来源、校准、ranking 指标、偏差/安全门 | 以 reward 分数代替最终生成质量 |
| Seq2Seq SFT | encoder-decoder 参数 | encoder/decoder 输入、decoder start、label shift、长度和 generation config | 沿用 causal LM 的 assistant span mask |
| 多模态微调 | 文本与图像/音视频编码器或 adapter | 对象 VersionId、processor、分辨率/帧预算、模态缺失策略、跨模态泄漏扫描 | 只哈希文本，遗漏图片对象或预处理状态 |

迁移判定可以写成一句话：**换的是可训练参数和任务损失，不换的是数据授权、不可变身份、固定入口、
Artifact 完整性、验证/测试隔离和 promotion 状态机。**

### 6.5 模型任务适配的最小测试集

新任务在申请真实 GPU 预算前，应有不依赖真实数据的 forward-only 或模拟 fixture 测试：

1. 输入 schema、角色/模态边界和 tokenizer/processor 输出可复现。
2. 监督标签或 pair/reward 目标非空，padding、ignore index、截断和长度策略正确。
3. forward、backward（可用 tiny fake model）和 optimizer step 的张量形状/梯度存在性正确。
4. 目标模块、冻结参数和 trainable 参数量与配置一致。
5. checkpoint 能在新进程中按声明的 base/model revision 加载，并拒绝错误 revision。
6. 评估器只读取获准 split，能区分 train、validation、test 和 challenge set。
7. 失败路径不会标记成功、覆盖已有 Artifact 或泄漏敏感样本。

---

## 7. 只读 preflight

preflight 分为三类，不能把其中一类的通过误当成训练授权：

| 检查 | 目的 | 可产生训练 Run/参数更新？ |
| --- | --- | --- |
| `check-config` | schema、路径、枚举、秘密键、资源声明 | 否 |
| `plan`/readiness | 数据/模型/代码/环境 digest、切分、预算、权限和后端可达性 | 否 |
| governed preflight | tiny fixture 的 forward、必要时受控 2-step backward、checkpoint/load 兼容 | 只有通过固定授权的 Training Run 才可；本地 forward-only 不能冒充正式证据 |

若 preflight 需要真正的 optimizer step、真实数据遍历、checkpoint 或持久 MLflow 证据，它本身就是
Training Run，必须走正式后端和授权。所谓“只是预热”不构成例外。

先确认平台：

~~~bash
source /data/conda/etc/profile.d/conda.sh
conda activate attend-ray-py312

systemctl is-active minio.service mlflow.service jupyterlab.service
curl -fsS -H 'Host: localhost' http://127.0.0.1:5000/health
curl -fsS http://127.0.0.1:9000/minio/health/live
ray status
nvidia-smi -L
~~~

再确认项目：

~~~bash
export PYTHONPATH="$PWD/train-model/<project>/src"

python train-model/<project>/scripts/submit_train.py \
  --config train-model/<project>/configs/<config>.yaml \
  --check-config

python train-model/<project>/scripts/submit_train.py \
  --config train-model/<project>/configs/<config>.yaml \
  --plan
~~~

只读结果必须能回答：

- project/task/backend 是否匹配；
- dataset/split/preprocessing digest 是否存在；
- model/tokenizer revision 是否固定；
- objective metric/direction 是否明确；
- role、promotable、test access 是否正确；
- 资源和 timeout 是否足够；
- Artifact 路径和 MLflow experiment 是否可用；
- 是否有阻断理由。

plan 成功不等于已授权训练，只是 readiness 的输入。

---

## 8. Immutable Release

### 8.1 Release 内容

应包括：

- 固定源码和 fixed entrypoint；
- formal configs 的 canonical JSON；
- galatea.project.yaml；
- conda.yaml 或等价 environment manifest；
- 数据预处理、训练、评估和 Artifact 代码；
- release manifest、文件级 SHA-256、code revision、environment digest。

应排除 tests、notebooks、cache、platform-data、symlink 和运行时数据库。若测试是运行时依赖，必须
将其作为显式、可哈希的 release 输入，而不是假定工作树中的 tests 会随时存在。

Release builder 必须：

- 默认拒绝 dirty Git worktree；
- 输出目录位于项目外；
- 同一 release ID 内容不同则拒绝覆盖；
- 生成 content-addressed archive；
- 不上传、不注册、不创建 MLflow Run、不启动声明的执行后端。

示例：

~~~bash
python train-model/<project>/scripts/build_release.py \
  --output-dir /srv/galatea-private/<project>/releases \
  --registration-output /srv/galatea-private/<project>/registration \
  --snapshot-manifest <controlled-snapshot>/manifest.json
~~~

管理员登记前绑定：

- immutable object VersionId；
- train/validation/test view digest；
- MLflow experiment ID；
- 声明后端的 topology/cluster identity 和 role credentials；
- quality gates；
- Campaign budget 和审批人。

### 8.2 Release 与当前 HEAD

训练使用 Release 记录的 code_revision，不是提交 Job 时工作树的 HEAD。后续只修改 tests 或文档时，可以让 HEAD 前进，但不能声称旧 Release 包含这些变化。运行代码、配置或环境改变，必须构建新 Release。

---

## 9. Campaign、Plan 和 Submit

### 9.1 Campaign

每个 slot 至少绑定：

~~~json
{
  "step_id": "baseline",
  "role": "baseline",
  "config_ids": ["<config-id>"],
  "release_ids": ["<release-id>"],
  "max_attempts": 1
}
~~~

Campaign 还要有 unique ID、request revision、expiry、approved_by、计算/存储预算、max_trials、stage 和 cancellation 状态。

### 9.2 Writer lock

Galatea state 是单写者状态。遇到 writer-locked：

1. 不绕过 MCP 手工改历史 state；
2. 确认没有活动或正在提交的后端 Job；
3. 按部署规范短暂停 service；
4. 以服务账号 register/amend；
5. 立即恢复 service；
6. 通过官方 MCP API 读取 Campaign 和 operation。

### 9.3 Plan

plan_run 必须绑定完全相同的：

~~~text
project_id
campaign_id
step_id
attempt
role
config_id
release_id
~~~

Plan 返回 plan_id、readiness_digest、input_digest、资源预算、deadline 和 candidate/champion 绑定。baseline 的 candidate/champion 应为 null。

提交前再次检查活动 Job 数量，避免共享 endpoint 并发训练。

### 9.4 Submit

只用 Plan 返回的 plan_id，并使用稳定且唯一的 idempotency key：

~~~text
<campaign-id>-<step-id>-attempt-<n>
~~~

只提交一次。网络超时不要盲目重提；先用原 idempotency key 查询 operation。平台返回 unknown 时先 reconcile。

---

## 10. 固定 Driver 的 execution binding

### 10.0 后端无关的执行接口

项目声明的 `executionBackend` 可以是 Ray，也可以是另一个具备恢复和审计能力的后端。平台应把后端
差异收敛在一个固定适配器接口中：

```text
validate_binding(binding) -> admission result
submit(plan, idempotency_key) -> execution_id
observe(execution_id) -> state/log cursor
cancel(execution_id) -> terminal state
reconcile(execution_id) -> authoritative state
```

后端适配器必须保证：

- 同一 `plan_id + idempotency_key` 至多启动一个逻辑 Attempt；
- Job/worker 日志、资源和退出码能关联到 `operation_id`、`attempt_id` 和 MLflow Run；
- 只有固定 Driver/worker 组合能更新模型参数或创建父 Run；
- 取消、超时、节点失败和重试都有明确终态；
- 适配器不把“提交成功”当成“训练成功”，也不在客户端伪造运行时身份。

Ray 项目把该接口映射为 Galatea plan → Ray Job → Driver；Batch/Kubernetes 项目可以映射为
Galatea plan → 受控 Job/Workflow → Driver。若某个后端不能提供 immutable binding、幂等、终态查询或
Artifact/Run 关联，就不能作为正式 Training Run 后端，只能停在 contract validation。

固定 Driver 应拒绝缺少以下字段的调用：

~~~json
{
  "schema_version": "galatea.execution/v1",
  "project_id": "<project>",
  "campaign_id": "<campaign>",
  "operation_id": "<operation>",
  "submission_id": "<submission>",
  "release_id": "<release>",
  "release_digest": "<sha256>",
  "readiness_digest": "<sha256>",
  "config_id": "<config>",
  "config_digest": "<sha256>",
  "role": "baseline",
  "attempt": 1,
  "seed": 42,
  "objective": {"metric": "val_loss", "direction": "min"},
  "resources": {
    "cpus": 4,
    "gpus": 1,
    "memory_bytes": 17179869184,
    "workers": 1,
    "seconds": 10800,
    "cleanup_seconds": 60
  },
  "views": {
    "train": {"bucket": "...", "key": "...", "version_id": "...", "sha256": "..."},
    "validation": {"bucket": "...", "key": "...", "version_id": "...", "sha256": "..."}
  }
}
~~~

baseline/Trial binding 不应有 test view。Champion/evaluate 的 test view 必须单独授权，并绑定 candidate freeze 与 test-once claim。

Driver 顺序：

1. 验证 admission、role 和 config；
2. 创建 Driver-owned MLflow Run；
3. 写 lineage params/tags；
4. 通过 Artifact service 下载 train/validation；
5. 校验对象版本、大小和 SHA-256；
6. 加载 immutable base model/tokenizer；
7. tokenize、mask、训练；
8. 采用 Trainer 权威 validation metric；
9. 保存 adapter、best checkpoint、trainer state 和 validation report；
10. 上传 Artifact 并通过 MLflow API round-trip；
11. 新进程加载 adapter；
12. 写 reports/evidence.json 并再次 round-trip；
13. 设置成功 tag，终止 MLflow Run；
14. 全部完成才返回成功。

任一步失败都应设置 Run failed、保留失败证据、不发布半成品，并用新 attempt/new Run 重试。

---

## 11. 训练时间和资源预算

### 11.1 总时间模型

~~~text
T_total =
  T_data_download
  + T_model_load
  + T_tokenize
  + T_train
  + T_validation
  + T_checkpoint
  + T_artifact_upload
  + T_artifact_download_verify
  + T_cleanup
~~~

deadline_seconds 必须覆盖估算值和 safety margin。cleanup_seconds 要显式记录。

### 11.2 不要只看显存

高显存占用不等于高吞吐。batch size、sequence length、padding、attention kernel、数据长度分布和 evaluation 都会影响 GPU utilization。

安全调优顺序：

1. 小 batch forward-only 兼容检查；
2. 固定配置测 samples/s、steps/s、validation tokens/s；
3. 记录 peak allocated/reserved memory；
4. 逐步增加 batch 或 gradient accumulation；
5. 留出 OOM 和尾部 Artifact 余量；
6. 运行中不改 config，不重提同一个 Operation。

本次 batch_size=4、eval_batch_size=4 在约 48 GiB GPU 上显存约 46.9 GiB，已接近边界。没有为了“吃满显存”继续冒险，是正确决策。

### 11.3 Validation 策略

如果 Trainer 已产生 epoch-level eval_loss，优先复用它。只有项目明确需要逐样本指标时，才额外执行逐样本或批量 pass，并把时间计入 deadline。

必须额外计算时：

- 采用 padding batch；
- 按每个样本有效 assistant token 数归一化；
- 对 -100 使用 ignore index；
- 检查每个样本至少有一个 supervised token；
- 记录 batch size 和 metric definition。

### 11.4 预算估算与容量实验记录

在正式 Trial 前先用受控小样本测量，而不是凭显存猜预算。至少记录：

```text
samples/s、tokens/s、optimizer_steps/s、validation_tokens/s
峰值 allocated/reserved GPU memory、进程 RSS、模型加载时间
tokenize/数据下载时间、checkpoint 大小与写入时间、Artifact 往返时间
```

每项都要附带硬件、精度/量化、batch、gradient accumulation、平均/分位序列长度、warm-up 是否排除、
worker 数和测量样本数。改变这些口径就不能直接比较吞吐或成本。

容量或方法实验建议遵循“先兼容、后规模、再质量”的顺序：

1. tiny fixture 验证架构、processor、loss/偏好目标和 checkpoint。
2. 在冻结协议上测单步速度、显存和尾部时间，确定合法资源预算。
3. 先比较较小模型/低成本方法，再决定扩大模型、序列长度、GPU 或试 QLoRA。
4. 以主质量指标和资源成本的联合规则做采用/停止决定；不要以参数量或单一辅助分数替代。

---

## 12. MLflow 记录设计

### 12.1 History 与 summary 分离

逐 step：

~~~text
train_loss
learning_rate
gradient_norm
~~~

epoch/summary：

~~~text
val_loss
val_perplexity
train_loss  # authoritative aggregate，写在最高 history step 之后
~~~

成功 Run 的 history 结构：

- train_loss：9354 条，step 1–9354；
- learning_rate：9353 条，step 1–9353；
- gradient_norm：9353 条，step 1–9353；
- val_loss：1 条，step 9354；
- val_perplexity：1 条，step 9354。

把 aggregate 写在最后，避免 summary 查询把最后一个 optimizer batch loss 误认为最终 train_loss。

### 12.2 批量写入

用 log_batch，每批最多 1000 条 Metric，并同步提交。测试应验证：

- batch 大小上限；
- 所有 metric key 都保留；
- summary step 大于 history 最大 step；
- 写入失败时 Run 不标成成功。

### 12.3 Lineage

建议至少记录：

~~~text
task
role / run.role
attempt
code_revision
config_digest
dataset_digest
split_digest
preprocessing
metric_definition
evaluation_protocol
model_id / model_revision
tokenizer_revision
environment_digest
seed
release_id / release_digest
readiness_digest
galatea.campaign
galatea.operation
galatea.submission
test.access
run.outcome
run.promotable
artifact.roundtrip_verified
model.uri
~~~

不要把聊天原文、秘密、真实姓名或生成正文放入 params/tags。

---

## 13. Artifact contract

### 13.1 推荐 Artifact

~~~text
model/adapter_model.safetensors
model/adapter_config.json
reports/validation-quality.json
reports/evidence.json
checkpoints/best-adapter.safetensors
checkpoints/trainer_state.json
~~~

Champion/evaluate 才额外产生 reports/final-test-evaluation.json。

### 13.2 每个文件都要验证

1. 计算本地 SHA-256 和大小；
2. 用精确远端文件名上传；
3. 通过 MLflow Artifact API 下载；
4. 复算下载文件 SHA-256 和大小；
5. 对 adapter/config 做新进程加载；
6. 写入 evidence manifest；
7. 任一失败则 Run failed。

禁止直接读取 MinIO server filesystem、mlflow.db 或让客户端下载 server-side credentials。

### 13.3 evidence.json 最小结构

~~~json
{
  "schema_version": "galatea.evidence/v1",
  "lineage": {
    "project_id": "<project>",
    "campaign_id": "<campaign>",
    "operation_id": "<operation>",
    "submission_id": "<submission>",
    "release_id": "<release>",
    "release_digest": "<sha256>",
    "config_id": "<config>",
    "config_digest": "<sha256>",
    "dataset_digest": "<sha256>",
    "split_digest": "<sha256>",
    "readiness_digest": "<sha256>",
    "role": "baseline",
    "seed": 42,
    "clean_start": true,
    "candidate_id": null,
    "champion_run_id": null
  },
  "metrics": {
    "train_loss": 0.0,
    "val_loss": 0.0,
    "val_perplexity": 0.0
  },
  "artifacts": [
    {
      "path": "model/adapter_model.safetensors",
      "sha256": "<sha256>",
      "size_bytes": 0
    }
  ],
  "integrity": {
    "roundtrip": true,
    "load_verified": true
  },
  "final_test_status": "not-run"
}
~~~

evidence.json 的 allowlist 必须同时存在于 galatea.project.yaml 和 registry project。

---

## 14. 运行中监听和终态验收

### 14.1 运行中只观察

可以读：

- galatea_observe_job；
- 声明后端的 Job API（当前项目为 Ray Jobs API）；
- Driver log；
- CPU、内存和适用的 accelerator utilization/memory；
- MLflow Run status/history。

不能：

- 修改同一个 config；
- 改 batch size、eval batch 或 timeout；
- 重新提交同一个 Operation；
- 为了利用率杀掉其他任务或重置共享 accelerator；
- 访问 test；
- 以“看起来卡住”为理由绕过治理边界。

### 14.2 Trainer 100% 不是 Job 成功

还要等待（Ray 项目示例；其他后端等待等价的终态和 reconciliation）：

1. validation；
2. checkpoint；
3. Artifact upload；
4. Artifact round-trip；
5. fresh-process load；
6. MLflow FINISHED；
7. 声明的执行后端进入成功终态（Ray 项目为 Ray SUCCEEDED）；
8. Galatea reconciliation 标记 succeeded/verified。

### 14.3 必须同时满足的成功条件

| 层 | 必须观察到 |
| --- | --- |
| 执行后端 | 成功终态、driver/worker exit code 0、metadata 与 binding 一致（Ray 项目即 Ray SUCCEEDED） |
| Galatea | execution=succeeded、integrity=verified |
| MLflow | FINISHED、run.outcome=succeeded |
| Artifact | required files、SHA-256、大小、round-trip、fresh load |
| Metrics | objective、direction、history、aggregate 一致 |
| Data | train/validation identity 固定，baseline test untouched |
| Comparison | validation-only ranking，无不兼容 rejection |
| Governance | candidate/test/promotion 状态未越权 |

任何一层缺失都只能报告部分完成或失败。

---

## 15. 失败、重试和恢复

### 15.1 失败不覆盖

失败 Run、Operation、checkpoint 和 log 都是审计证据。修复后走：

~~~text
旧失败 Run 保留
-> 新 immutable Release（若代码/配置改变）
-> 新 Campaign 或新 revision
-> 新 plan/readiness
-> 新 submission/operation
-> 新 MLflow Run
~~~

本项目声明 pauseResume=false，不能伪造跨 Job resume。

### 15.2 重试矩阵

| 失败类型 | 新 Release | 新 Campaign | 新 attempt | 处理 |
| --- | --- | --- | --- | --- |
| 代码修复 | 是 | 推荐唯一名称 | 是 | 旧证据保留 |
| 配置/资源改变 | 是或重新登记配置 | 是 | 是 | 重新 plan |
| 网络 submit 不确定 | 否 | 否 | 否 | 用原 key 查询 |
| 执行后端/环境失败 | 视 binding | 推荐 | 是 | 保留失败 Run |
| Artifact hash mismatch | 通常是 | 推荐 | 是 | 查路径/服务 |
| deadline | 通常是 | 推荐 | 是 | 缩短尾部或增加合法 timeout |
| test 提前访问 | 是 | 是 | 是 | 废弃 test evidence |
| Campaign 被覆盖 | 不改历史 | 是 | 是 | 新唯一 Campaign |

### 15.3 不要盲目重试

以下情况应先修复外部阻塞或请求管理员：

- object VersionId 无法取得；
- MLflow/Artifact 服务不可用；
- 声明后端的 cluster/namespace identity 与 registry 不一致；
- Release registry 权限不足；
- Campaign budget/approval 已耗尽；
- consent 撤回或 formal dataset 不再 eligible。

---

## 16. 从 baseline 到 Trial、Champion 和 promotion

本次任务在 baseline 成功后停止。成功不会自动升级为下一状态。

### 16.1 Trial

- 使用同一 Driver、数据加载、预处理、loss、checkpoint 和 Artifact 代码；
- 只改变允许的 config/data/role/resource；
- 只用 train/validation 选参；
- 使用新 slot、新 Plan、新 Operation、新 Run；
- 不读取 test；
- 保持 task、dataset、split、preprocessing、metric、protocol、role 兼容。

### 16.2 Candidate freeze

至少绑定：

- selected Run ID；
- validation evidence digest；
- config/release/split/protocol digest；
- checkpoint/model digest；
- comparison ranking；
- selection rule；
- 所有活动 operation 已终态。

### 16.3 Champion/test-once

1. candidate 已冻结；
2. 生成 test-once claim；
3. binding 明确 test view 和 test evaluation ID；
4. Driver 原子 claim test；
5. test 只读一次；
6. 输出 final-test-evaluation.json；
7. Artifact round-trip 和质量门通过。

模型、prompt、阈值、split、协议或 candidate 改变时，旧 test evidence 失效。

### 16.4 Promotion

promotion 是独立显式动作，需要：

- compatible validation evidence；
- test-once；
- Artifact round-trip；
- PII/canary/safety gates；
- 人工 review；
- 明确审批和 audit record；
- 原子更新生产 alias。

baseline 的 run.promotable=false 是正确状态，不是失败。

---

## 17. 迁移到其他模型

只替换项目特定值，不能删除治理字段：

| 可替换项 | 必须保留 |
| --- | --- |
| model ID/revision | immutable identity |
| tokenizer/chat template | versioned preprocessing |
| 参数更新方法和可训练范围 | checkpoint/model output + fresh-load |
| 方法专属模块或冻结策略 | config digest + existence/frozen-state check |
| dataset schema | immutable manifest/split/object versions |
| objective metric | direction and compatibility |
| resource budget | plan-bound compute/accelerator/memory/deadline |
| evaluation protocol | frozen protocol across variants |
| framework/backend | fixed project Driver and declared backend boundary |

模型族额外检查：

| 类型 | 额外检查 |
| --- | --- |
| Causal LM SFT | assistant-only labels、chat template、stop rules |
| Seq2Seq | encoder/decoder labels、decoder start token、length |
| QLoRA | quantization、bitsandbytes/CUDA、merge 规则 |
| DPO/Preference | chosen/rejected identity、preference split、reference digest |
| Reward model | pair/group split、label calibration、ranking metric |
| Multimodal | image/object VersionId、processor revision、pixel budget |
| Long-context | packing、position encoding、truncation、target token count |
| Multi-GPU | topology、rank/seed、authoritative parent Run、checkpoint owner |

### 17.1 新模型/新任务的迁移步骤

不要从旧项目复制一个“能跑”的训练脚本开始；按下面顺序建立新项目：

1. **定义任务和数据对象**：写清输入、目标、标签/pair、模态、group、敏感字段、撤回语义和 split。
2. **冻结基座与处理器**：登记 model/processor revision、架构、精度、上下文长度和特殊 token 规则。
3. **选择方法并列出变量**：明确全参或 PEFT、可训练参数、loss、优化器、checkpoint 和推理加载方式。
4. **声明主指标和门禁**：定义 train/validation/final-test 指标、方向、最小样本量、硬失败条件和人工审查。
5. **实现项目骨架**：`README.md`、`galatea.project.yaml`、`configs/`、`schemas/`、`src/`、`scripts/`、
   `tests/`、`docs/` 和环境文件。
6. **先做只读契约检查**：确认不创建 Run、不读 test、不更新参数，且所有 digest 可重算。
7. **做 tiny fixture/preflight**：覆盖 forward、必要的 backward、目标/标签、冻结边界和 fresh-load。
8. **构建 clean immutable Release**：把代码、配置、环境和 schema 固化；建立 Campaign/资源预算。
9. **通过声明后端执行 smoke**：smoke、baseline、Trial、Champion 都走同一个固定 Driver/后端适配器。
10. **只用 train/validation 选参并冻结候选**：记录兼容比较和 selection rule，不提前读取 test。
11. **test-once、人工/安全审查和 promotion 分离**：每一步都有独立授权和可回读证据。
12. **做恢复演练并记录 STOP 条件**：模拟超时、节点失败、hash mismatch、撤回和权限过期，证明不会覆盖成功证据。

推荐目录只表达所有权，不要求不同框架使用同一训练器：

```text
train-model/<project>/
├── README.md                 # 使用入口和当前状态
├── galatea.project.yaml      # 后端、入口、兼容性和证据契约
├── conda.yaml                # 或等价的锁定环境
├── configs/                  # smoke/baseline/trial/champion/evaluate 变体
├── schemas/                  # 数据、Run、Job 和 Artifact 的活动 Schema
├── src/                      # loader/model/objective/trainer/evaluator
├── scripts/                  # check/plan/release/受控提交/评估
├── tests/                    # 任务语义和治理边界
└── docs/
    └── README.md             # 项目唯一主文档；被合并的旧文档由 Git 追溯
```

跨项目不变量和协议留在 `doc/train-llm/`；模型 ID、数据命令、项目状态和运行手册不得复制回通用层。

### 17.2 迁移完成的“最小充分证据包”

新项目至少应能交付下列不含敏感正文的证据包：

```text
project-contract.yaml
release.json + release archive digest
dataset-manifest.json + split-manifest.json + preprocessing report
plan/readiness + execution binding + attempt/job metadata
MLflow Run ID + parameter/metric history + terminal status
checkpoint/model manifest + per-file SHA-256 + Artifact round-trip receipt
validation report + compatible comparison/ranking
candidate freeze record（若有）
final-test-once report（仅 Champion/evaluate）
human/safety/privacy review + promotion audit（若上线）
```

如果只能提供 loss 曲线、checkpoint 路径或一张“训练完成”截图，证据包是不充分的。

---

## 18. 端到端验收清单

使用方法：每一项都要有可回读证据或明确 `blocked` 原因；“代码存在”“命令返回 0”或“后端
显示成功”不能代替对应证据。对不适用的项目（例如纯文本任务没有图像）记录 `not_applicable`，
不能默默省略身份、评估或安全字段。

### A. 数据和授权

- [ ] 原始输入只读，来源、内容身份和使用权/授权依据已登记。
- [ ] 适用时，consent purpose、数据主体、期限、第三方和撤回策略已验证。
- [ ] 按数据风险执行 PII/secret/canary、恶意输入或标签泄漏检查，并记录结果。
- [ ] 需要人工或标签审核时，所有 review/candidate 状态已结案，未决项为 0。
- [ ] formal snapshot 有 manifest、lineage、split 和 object VersionId。
- [ ] train/validation/test digest 和计数可复算。

### B. 代码和配置

- [ ] project contract 声明 execution backend、fixed entrypoint、task 和 objective。
- [ ] check/plan 不产生 Run、checkpoint 或 test access。
- [ ] Driver 缺少 binding 时 fail closed。
- [ ] model 与 tokenizer/processor revision 固定。
- [ ] 输入/标签或偏好构造、loss/objective、截断、batch、seed 和参数更新方法有对应测试。
- [ ] Artifact allowlist 包含 evidence、validation，以及适用的 checkpoint/model/adapter。
- [ ] 代码、配置、环境和测试通过。

### C. Release 和平台

- [ ] worktree clean，Release archive content-addressed。
- [ ] archive、manifest、registry SHA-256 一致。
- [ ] registry project 与 Campaign slot 绑定同一 Release。
- [ ] Campaign 有唯一 ID、审批、expiry 和预算。
- [ ] writer lock 和文件权限正确。
- [ ] 声明的执行后端、MLflow、Artifact service health 正常（Ray 项目另确认 Ray cluster）。

### D. Plan 和 Submit

- [ ] list_operations 确认目标 Campaign 状态。
- [ ] plan_run 参数与 slot 完全一致。
- [ ] readiness digest 已保存。
- [ ] submit 前活动后端 Job 符合并发策略。
- [ ] 只提交一次，idempotency key 稳定。
- [ ] 保存 plan、operation、submission、run 关联。

### E. 运行中

- [ ] 只观察，不修改运行中 config。
- [ ] train、validation、checkpoint/model output 和 Artifact 的适用阶段都有日志。
- [ ] CPU、内存和适用的 accelerator 无 OOM、泄漏或非预期竞争。
- [ ] deadline 覆盖尾部 validation 和 Artifact round-trip。
- [ ] 未出现 test view 或 final-test API 调用。

### F. 终态

- [ ] 声明的执行后端进入成功终态（Ray 项目为 SUCCEEDED）。
- [ ] MLflow FINISHED。
- [ ] run.outcome=succeeded。
- [ ] artifact.roundtrip_verified=true。
- [ ] Galatea integrity=verified。
- [ ] evidence artifact 可由官方 API 回读。
- [ ] 每个 Artifact 下载后 SHA-256/size 一致。
- [ ] 新进程加载 adapter 成功。
- [ ] objective、history、aggregate 指标一致。
- [ ] test access 仍为 untouched。
- [ ] compare ranking 有结果且无不兼容 rejection。
- [ ] 没有误启动 Trial、Champion、evaluate 或 promotion。

### G. 迁移与抽象性

- [ ] 项目明确声明任务类型、执行后端、固定入口和后端适配器，不依赖本指南中的 Qwen/Ray/LoRA 默认值。
- [ ] 模型族专属的输入、标签/loss、checkpoint、评估和服务加载测试已存在。
- [ ] 若使用全参、QLoRA、DPO、Reward、Seq2Seq 或多模态方法，方法参数和额外身份均已进入 config/Run/Artifact。
- [ ] 训练、恢复和持久评估没有第二条本地或通用调度路径；所有角色复用同一工作负载代码。
- [ ] 主指标、方向、质量门、资源成本和停止/采用规则已版本化。
- [ ] test-once 和 promotion 是独立状态，不会因方法或后端变化而自动放宽。

### H. 文档充分性审计

- [ ] 项目 README 能从数据交付、check/plan、Release、授权、运行、查询证据到恢复完整导航。
- [ ] runbook 的每条命令都标注只读/训练/评估性质、所需权限和预期产物；示例 placeholder 不会被误当成授权。
- [ ] design/implementation 文档解释组件边界、状态机、失败模式和测试策略，而非只列超参数。
- [ ] schema、配置、Artifact allowlist、MLflow 字段和 quality gates 互相一致。
- [ ] 文档链接、路径、环境名、端口和服务状态与实际仓库文件/部署单元一致。
- [ ] 文档明确当前状态（例如 `implementation_complete/formal_evidence_blocked`）和下一步放行条件。

---

## 19. 证据查询顺序

使用仓库提供的官方 MCP/MLflow API 封装，按如下顺序只读查询；`observe_job` 是 Ray 项目示例，
其他后端使用等价的 observe/reconcile API：

~~~text
get_capabilities
get_campaign
list_operations
get_operation / observe_job
query_runs
get_metric_history
get_artifact reports/evidence.json
get_artifact reports/validation-quality.json
compare_runs
~~~

MLflow 侧使用 Tracking/Artifact API：

~~~python
run = client.get_run(run_id)
assert run.info.status == "FINISHED"
evidence = client.download_artifacts(run_id, "reports/evidence.json", temp_dir)
adapter = client.download_artifacts(run_id, "model", temp_dir)
~~~

不要把 placeholder 当成授权，不要把模板命令直接用于触发训练。

### 19.1 目录级充分性结论（2026-09-10）

对通用指南、跨项目协议以及两个项目内文档的联合审计结论如下：

| 审计项 | 结论 | 依据/剩余动作 |
| --- | --- | --- |
| 治理原则 | 充分 | governed-training-workflow、execution classification、evidence contract 已定义统一边界 |
| Toy SFT/LoRA/Ray | 充分 | `llm-lora-playground/docs/README.md` 和项目实现/测试互相对应，旧阶段文档已合并 |
| 私有聊天数据治理 | 充分但默认阻断 | `wechat-persona/docs/` 明确 consent、审核、脱敏、撤回和 formal snapshot 门 |
| 开放式聊天评估 | 充分 | 四层指标、Base/Prompt-only/LoRA 矩阵、validation-only、test-once 和人工盲测已版本化 |
| RAG/记忆边界 | 充分 | memory-grounded 协议把记忆留在模型外，并覆盖 owner isolation、删除和无证据拒答 |
| 其他 PEFT/全参/任务 | 本指南已抽象；项目实现需补 | 必须按第 6.4、6.5、17 节建立专属 config、Driver 组件和测试 |
| 非 Ray 后端 | 本指南已抽象；平台适配需补 | 必须实现第 10.0 的 binding、幂等、observe/reconcile 和终态关联接口 |
| 当前真实私有数据正式训练 | 不足/阻断 | consent、人工审核、formal snapshot 或服务绑定未齐时不得训练；历史本地结果不能升级 |
| 生产晋级 | 不自动完成 | 仍需 candidate freeze、test-once、人工/安全审查和独立 promotion |

**最终判断：** 本文是从新机器开始实施受治理 LLM 微调的唯一执行入口，已经覆盖平台安装、项目合同、
数据与模型身份、Release、授权执行、MLflow/Artifact 验收、选择、test-once、发布和恢复的完整生命周期。
其余 `doc/train-llm/` 与项目文档只用于专题深入或维护，不是执行本文的阅读前提。本文也足够清楚地说明
哪些治理内容必须保持、哪些工作负载组件可以替换、何时必须阻断。

“完整”不表示静态文档会自动生成任意模型的任务代码。当前可直接核对的 live MCP V1 控制面示例是
`wechat-persona`；新模型仍必须在自己的 `train-model/<project>/` 中提供 loader、label/loss、训练器、
checkpoint、评估器、环境、Schema 和后端适配，并通过第 17、18 节的迁移清单与最小充分证据包验收。

---

## 20. 最重要的十条经验

1. 训练本体完成不等于 Run 成功；尾部 validation、Artifact 和 fresh-process load 也是训练的一部分。
2. 资源预算必须覆盖尾部，不能只测 optimizer loop。
3. Artifact 路径是契约；basename、allowlist、hash 和 round-trip 缺一不可。
4. 同一指标只能有一个权威定义；Trainer、MLflow、report 和 evidence 必须一致。
5. MLflow history 要批量、同步、有序写入，aggregate 放在最高 history step 之后。
6. 失败证据必须保留；修复后新建 Release/Plan/Attempt/Run，不重写历史。
7. 唯一 Campaign 名称和 writer lock 能防止并发污染。
8. 资源利用率不是目标；稳定、可复现并留有 OOM/容量余量更重要。
9. baseline 不需要 test；baseline 的职责是建立可比较、可追溯的 train/validation 基线。
10. promotion 是独立状态；执行后端、MLflow 和 Artifact 成功都不等于可以上线。

---

## 21. 深入阅读（不是执行前提）

下面的材料提供项目维护细节或专题协议。第一次从零实施只按本文顺序执行即可；遇到要修改项目代码、
Schema 或协议时，再进入相应资料：

- [LLM 训练文档中心](README.md)
- [项目 0–4 合并实施指南](../../train-model/llm-lora-playground/docs/README.md)
- [项目 5–9 合并实施指南](../../train-model/wechat-persona/docs/README.md)
- [wechat-persona governed execution plan](../../train-model/wechat-persona/docs/governed-execution-plan.md)
- [fine-tuning evaluation protocol](fine-tuning-evaluation-protocol.md)
- [memory-grounded evaluation protocol](memory-grounded-evaluation-protocol.md)
- [wechat-persona project README](../../train-model/wechat-persona/README.md)
- [wechat-persona project contract](../../train-model/wechat-persona/galatea.project.yaml)
- [governed-training-workflow skill](../../.codex/skills/governed-training-workflow/SKILL.md)

---

## 22. 维护规则

这份文档描述流程和证据契约，不是某个模型的永久超参数表。以下变化必须更新项目 config、protocol 或 Release：

- model/tokenizer revision；
- chat template、thinking 或 loss mask；
- dataset manifest、split 或 preprocessing；
- Artifact 路径、报告 schema 或 metric definition；
- 执行后端 topology/namespace、资源、timeout 或执行环境；
- MLflow experiment、质量门或 promotion policy。

维护流程：

1. 修改项目代码、Schema 和测试；
2. 运行只读 check/plan；
3. 构建新的 immutable Release；
4. 重新 register/plan；
5. 用新 Operation 产生新证据；
6. 保留旧 Release、旧 Run 和失败/成功历史；
7. 不能只修改文档或 Registry 指针来改变历史 Run 的含义。

项目专属变更只更新项目 `docs/`；只有控制面、证据或跨项目协议发生变化时才更新本指南。
