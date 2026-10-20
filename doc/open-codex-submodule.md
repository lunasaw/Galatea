# Open Codex 子模块维护

通用 Host 独立维护于 [lunasaw/open-codex](https://github.com/lunasaw/open-codex)。
`agents/codex-app-server` 使用 Git 的 `160000` gitlink 条目固定到具体提交；
`.gitmodules` 记录公开 HTTPS 仓库地址。子模块不是符号链接，也不会自动跟随上游 main。

## 克隆与拉取

```sh
git clone --recurse-submodules https://github.com/lunasaw/Galatea.git
cd Galatea
git submodule status
```

已有克隆在切换到包含此次迁移的分支后执行：

```sh
git submodule update --init --recursive
```

之后每次拉取主仓库也执行该命令。CI 使用递归 checkout；部署使用递归克隆。
GitHub 源码 ZIP 和普通 `git archive` 不包含子模块内容。

## 保留已有运行状态

本次拆分仅改变源码管理方式，不自动迁移或重启已有服务。现有工作区切换分支前，
确认组件没有未提交源码改动，并备份该目录内所有被忽略的状态，尤其是 `.local/`。
更稳妥的方式是先在新目录递归克隆并验收，再按既有部署流程迁移本机状态。
不要用 `git clean -fdx` 清理运行目录，也不要将 `.local/` 中的认证或数据库提交到任一仓库。

包名 `codex-agent-host`、导入名 `codex_agent`、命令 `codex-agent`、配置版本
`codex-agent/v2` 和子模块路径保持兼容。open-codex 首次启动默认使用自身根目录；
要将 Galatea 作为工作区，在 Galatea 根目录运行：

```sh
CODEX_AGENT_WORKSPACE="$PWD" sh agents/codex-app-server/start.sh
```

已有 `.local/agent.json` 的工作目录会保留，环境变量只影响首次生成。

## 更新版本

先在 open-codex 提交、评审并合并通用 Host 改动，再选取明确的已验证提交：

```sh
git -C agents/codex-app-server fetch origin
git -C agents/codex-app-server checkout --detach <verified-commit>
python -m pip install -e 'agents/codex-app-server[test]' -e agents/galatea-app
python -m unittest discover -s agents/codex-app-server/tests -p 'test_*.py'
python -m unittest discover -s agents/galatea-app/tests -p 'test_*.py'
python -m unittest discover -s tests -p 'test_repository_contract.py'
git add agents/codex-app-server
git commit -m "chore(agent): update open-codex revision"
```

在 Galatea 的 PR 中记录上游提交、兼容性变化和测试结果。
常规部署不要执行 `git submodule update --remote`，以免绕过主仓库固定版本。
回退时将指针切回上一个已验证提交，经验证后提交回退；运行状态兼容性仍需按对应版本检查。

首次提取来源为 Galatea `16a4d2918c27ab3df7b55803732af0aeb1abb5b5`，保留该目录的
22 个历史提交及作者信息；路径重写后上游提交 SHA 与原仓库不同。
