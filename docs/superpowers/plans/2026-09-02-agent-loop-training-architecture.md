# Agent Loop 训评优化架构图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用 Draw.io MCP 新建一张中文单页架构图，清楚展示 DeepSeek Harness、`dsh-galatea` 插件，以及数据检查、训练、评估和配置优化闭环。

**Architecture:** 图采用三层结构：用户、DeepSeek Harness、Galatea 训推平台。DeepSeek Harness 内包含 Agent Loop 编排区和 `dsh-galatea` 插件，Loop 通过简短流程节点表达两次审批及评估失败后的优化回路；插件位于 Loop 与训练项目、Ray、MLflow、MinIO 之间。

**Tech Stack:** Draw.io MCP、mxGraph XML、PNG/SVG 导出

## Global Constraints

- 图中节点与说明统一使用中文。
- 节点使用短标签，不放说明性长文。
- `dsh-galatea` 必须位于 DeepSeek Harness 边界内部。
- Agent Loop 必须呈现 `数据检查 → 训练 → 评估 → 配置优化 → 训练` 的闭环。
- 数据就绪和评估通过后各设置一个人工审批节点。
- 所有元素保持在 `x=0..800`、`y=0..600` 的单页视口内。

---

### Task 1: 创建架构图

**Files:**
- Create: `docs/agent-loop-training-architecture.drawio`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-09-02-agent-loop-training-architecture-design.md`
- Produces: Draw.io MCP 当前会话中的单页 `Agent Loop 训评优化` 图，以及可编辑的 `.drawio` 文件

- [ ] **Step 1: 启动 Draw.io MCP 会话**

调用 `mcp__drawio__start_session({})`，确认浏览器中的 Draw.io 编辑器已连接。

- [ ] **Step 2: 构造单页 mxGraph XML**

调用 `mcp__drawio__create_new_diagram`，使用完整 `<mxfile>`，页面名为
`Agent Loop 训评优化`。根节点固定为 `0` 和 `1`，业务单元使用下列 ID、标签与几何位置：

| ID | 标签 | 父节点 | x | y | w | h | 形状 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| `user` | 用户 | `1` | 10 | 170 | 60 | 50 | 圆角框 |
| `harness` | DeepSeek Harness | `1` | 90 | 30 | 670 | 360 | 容器 |
| `loop` | Agent Loop | `harness` | 30 | 40 | 610 | 220 | swimlane |
| `data` | 数据检查 | `loop` | 20 | 50 | 80 | 44 | 圆角框 |
| `ready` | 就绪审批 | `loop` | 125 | 42 | 80 | 60 | 菱形 |
| `train` | 训练 | `loop` | 230 | 50 | 80 | 44 | 圆角框 |
| `evaluate` | 评估 | `loop` | 335 | 50 | 80 | 44 | 圆角框 |
| `gate` | 质量达标？ | `loop` | 440 | 42 | 80 | 60 | 菱形 |
| `result_approval` | 结果审批 | `loop` | 535 | 42 | 70 | 60 | 菱形 |
| `optimize` | 配置优化 | `loop` | 335 | 135 | 80 | 44 | 圆角框 |
| `done` | 完成 | `loop` | 535 | 135 | 70 | 44 | 圆角框 |
| `plugin` | dsh-galatea 插件 | `harness` | 205 | 285 | 260 | 50 | 强调圆角框 |
| `platform` | Galatea 训推平台 | `1` | 90 | 420 | 670 | 150 | 容器 |
| `project` | 训练项目 | `platform` | 20 | 50 | 130 | 55 | 圆角框 |
| `ray` | Ray | `platform` | 190 | 50 | 100 | 55 | 圆角框 |
| `mlflow` | MLflow | `platform` | 350 | 50 | 100 | 55 | 圆角框 |
| `minio` | MinIO | `platform` | 510 | 50 | 100 | 55 | 圆角框 |

颜色只承担边界区分：Harness 使用蓝色系，插件使用较深蓝色，平台使用绿色系，审批与判断使用浅黄色。所有文本设置 `html=1;whiteSpace=wrap`，字号不小于 12。

- [ ] **Step 3: 添加主流程和优化回路**

按以下 source/target 创建带 `<mxGeometry relative="1" as="geometry"/>` 的正交箭头：

| source | target | 标签 |
| --- | --- | --- |
| `user` | `loop` | 请求 |
| `data` | `ready` |  |
| `ready` | `train` | 通过 |
| `train` | `evaluate` |  |
| `evaluate` | `gate` | 指标 |
| `gate` | `result_approval` | 是 |
| `result_approval` | `done` | 通过 |
| `gate` | `optimize` | 否 |
| `optimize` | `train` | 重试 |
| `loop` | `plugin` | Tool 调用 |

`gate → optimize → train` 使用橙色强调，并用显式 entry/exit 坐标与 waypoint 避开其他节点。

- [ ] **Step 4: 添加平台访问关系**

从 `plugin` 底边分散三个出口，创建下列正交箭头；再连接平台内部的数据流：

| source | target | 标签 |
| --- | --- | --- |
| `plugin` | `project` | 检查/配置 |
| `plugin` | `ray` | 提交/监控 |
| `plugin` | `mlflow` | 评估/比较 |
| `ray` | `mlflow` | 记录 Run |
| `mlflow` | `minio` | Artifact |

- [ ] **Step 5: 导出可编辑源文件并验证 XML**

调用：

```text
mcp__drawio__export_diagram({
  "path": "/Users/weidian/project/luna/Galatea/docs/agent-loop-training-architecture.drawio",
  "format": "drawio",
  "page_name": "Agent Loop 训评优化"
})
```

运行：

```bash
python3 -c "import xml.etree.ElementTree as ET; ET.parse('docs/agent-loop-training-architecture.drawio')"
```

预期：命令退出码为 0，XML 可解析。

### Task 2: 导出预览并完成视觉校验

**Files:**
- Create: `docs/agent-loop-training-architecture.png`
- Create after approval: `docs/agent-loop-training-architecture.svg`

**Interfaces:**
- Consumes: Draw.io MCP 当前会话中的 `Agent Loop 训评优化` 页面
- Produces: 供用户评审的 PNG；批准后的 SVG

- [ ] **Step 1: 导出 PNG 预览**

调用：

```text
mcp__drawio__export_diagram({
  "path": "/Users/weidian/project/luna/Galatea/docs/agent-loop-training-architecture.png",
  "format": "png",
  "page_name": "Agent Loop 训评优化"
})
```

- [ ] **Step 2: 检查预览**

使用本地图片查看能力检查：节点重叠、标签截断、缺失箭头、连线穿过无关节点、回路不清晰、插件是否位于 Harness 容器内。发现问题时只通过 `mcp__drawio__edit_diagram` 更新相关 cell，并重新导出同一路径 PNG；最多自修复两轮。

- [ ] **Step 3: 展示预览并收集反馈**

向用户展示 `docs/agent-loop-training-architecture.png`。用户提出修改时，按 cell ID 定向更新，不重建无关部分。

- [ ] **Step 4: 用户批准后导出 SVG**

调用：

```text
mcp__drawio__export_diagram({
  "path": "/Users/weidian/project/luna/Galatea/docs/agent-loop-training-architecture.svg",
  "format": "svg",
  "page_name": "Agent Loop 训评优化"
})
```

预期：`.drawio`、`.png` 和 `.svg` 均存在且非空，PNG/SVG 与已批准页面一致。

- [ ] **Step 5: 验证交付文件**

运行：

```bash
test -s docs/agent-loop-training-architecture.drawio
test -s docs/agent-loop-training-architecture.png
test -s docs/agent-loop-training-architecture.svg
```

预期：三个命令退出码均为 0。
