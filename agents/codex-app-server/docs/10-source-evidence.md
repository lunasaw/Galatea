# 运行时来源与协议证据

原生协议 bundle、历史 wire surface 和来源记录位于 src/codex_agent/protocol 与 tests/fixtures。生产接纳要求实际二进制、包目录树、experimental schema 和来源验证一致，不能仅比较版本字符串。

`scripts/verify_runtime_provenance.py` 校验固定 NPM 包的签名与 Sigstore attestations，并输出 `codex-agent.runtime-provenance/v1`。历史记录不会自动转为本次重构的 release 证据。

源码开发模式测量本地二进制、配置和扩展，但不宣称正式发布。Python 核心依赖仅 jsonschema 与 uvicorn，测试额外使用 httpx；业务平台依赖不进入 Host distribution。
