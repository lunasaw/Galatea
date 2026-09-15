# 验证与交付记录

## 范围与结论

初次梳理：2026-09-07；最终复核：2026-09-15。两张图均使用 archify 2.17 生成。只新增本项目 `doc/` 下的文档与图示，
未修改业务实现，未读取真实聊天或最终测试数据，未进行模型训练或 Registry alias 更新。

| 图 | 类型 | 结构校验 | 自动浏览器证据 | 图像复核 | 视觉修正轮数 |
| --- | --- | --- | --- | --- | --- |
| [system-architecture](system-architecture.html) | architecture | 9/9 showcase，0 错误 / 0 警告 | passed | passed | 1 |
| [governed-workflow](governed-workflow.html) | workflow | 9/9 showcase，0 错误 / 0 警告 | passed | passed | 0 |

三类证据独立记录：`deliver` 证明规格与 HTML 的字节身份及确定性校验；`visual-check`
证明浏览器中的尺寸、可读性与截图覆盖；图像复核由本次图像能力实际检查截图后记录。
自动浏览器回执内的 `visualReview: pending` 是工具固定语义，不会被手动改成通过；
人工性质的图像复核另记于 [handoff.json](evidence/handoff.json)。

## 图规格和 HTML 摘要

### system-architecture

- 规格：[system-architecture.json](diagrams/system-architecture.json)，5315 字节。
- 规格 SHA-256：`412226a2d08cdcf46375ee5b6d268d687955893b35cc65698cbc83a434d7860e`。
- HTML：[system-architecture.html](system-architecture.html)，716660 字节。
- HTML SHA-256：`1b03350c582329676617d7ebdc2934b305d2464f64b0725fc85014a143e3c2d4`。
- 确定性交付回执：[system-architecture.delivery.json](evidence/system-architecture.delivery.json)。
- 浏览器回执：[system-architecture.visual-check.json](system-architecture.visual-check.json)。
- 截图索引：[system-architecture.visual-check.html](system-architecture.visual-check.html)。

### governed-workflow

- 规格：[governed-workflow.json](diagrams/governed-workflow.json)，6415 字节。
- 规格 SHA-256：`52e9131baddfe2996c4bb2309cb5ed750e307b2048095707000ee9175382c0e3`。
- HTML：[governed-workflow.html](governed-workflow.html)，718677 字节。
- HTML SHA-256：`829653bd5679dd041b14f0d66433bb53778d92292051b4b159a488ade6f6eba5`。
- 确定性交付回执：[governed-workflow.delivery.json](evidence/governed-workflow.delivery.json)。
- 浏览器回执：[governed-workflow.visual-check.json](governed-workflow.visual-check.json)。
- 截图索引：[governed-workflow.visual-check.html](governed-workflow.visual-check.html)。

## 浏览器与视觉检查

原系统 Chrome 的 DevTools 管道检查超时；通过工具支持的 `ARCHIFY_CHROME` 指定本机已有
Chromium headless shell 后重新执行，两个最终 HTML 均通过。未修改 archify 的渲染器或检查器。

| 视口 | 架构图 scrollWidth × scrollHeight | 流程图 scrollWidth × scrollHeight |
| --- | --- | --- |
| 1440 × 900 | 1440 × 900 | 1440 × 900 |
| 1600 × 1000 | 1600 × 1000 | 1600 × 1000 |
| 1920 × 1080 | 1920 × 1080 | 1920 × 1080 |
| 2048 × 1320 | 2048 × 1320 | 2048 × 1320 |

上述四种视口均满足 `scrollWidth <= innerWidth` 且 `scrollHeight <= innerHeight`。
1440×900 和 2048×1320 各有深浅两种主题截图；八张最终截图均已查看。
复核内容包括节点和连线文字、遮挡/交叉、图例与导航区、卡片及大屏纵向比例。
补充交互检查已验证两张图的节点搜索、聚焦、Escape 关闭，以及通过真实导出菜单下载 SVG。
SVG 结构检查确认包含图节点且不包含页面按钮，无页面运行错误；记录见
[viewer-interactions.json](evidence/viewer-interactions.json)。
静态截图和这些抽查不等同于所有交互功能或全部导出格式的完整测试。

## 项目检查

检查使用本机已存在的 Python 3.11.13 / PyYAML 6.0.2；仓库文档中的 Linux Conda 路径在本机
不存在。本次未安装依赖，也未把本地检查等同于项目 `conda.yaml` 中训练环境的验证。

| 检查 | 退出码 | 观察结果 |
| --- | --- | --- |
| 现有单元测试 | 0 | 2026-09-15 再次执行：64 tests，OK；合成数据/组件/契约测试 |
| 导入配置 `--check` | 0 | ok，不写数据或创建 MLflow Run |
| 数据集 `--plan` | 0 | planned，不导出正式数据 |
| BM25 索引 `--plan` | 0 | planned，`will_write_index: false` |
| 训练配置 `--check-config` | 0 | 配置结构有效 |
| Smoke 训练 `--plan` | 2 | 按预期 blocked，正式训练资格/人工审核/PII/canary 门尚未通过 |

完整命令和输出见 [project-checks.json](evidence/project-checks.json)。
配置结构通过不代表数据身份真实或训练已授权；blocked 是现状验证结果。

## 重现命令

在仓库根目录执行以下只读检查：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s train-model/wechat-persona/tests -p 'test_*.py'
python3 train-model/wechat-persona/scripts/submit_train.py \
  --config train-model/wechat-persona/configs/persona-lora-smoke.yaml --plan
```

图示维护使用本机 archify：

```bash
node /Users/weidian/.agents/skills/archify/bin/archify.mjs validate architecture \
  train-model/wechat-persona/doc/diagrams/system-architecture.json --quality showcase --json
node /Users/weidian/.agents/skills/archify/bin/archify.mjs validate workflow \
  train-model/wechat-persona/doc/diagrams/governed-workflow.json --quality showcase --json
```

修改后需重新 `deliver`、`visual-check`、查看截图并更新本记录的摘要；
原始 JSON 是图的编辑入口，不应直接编辑生成的 HTML。
