# 发布接纳门禁

正式启动通过 release.verify_release 检查管理员保护的 manifest、stage0_record 和证据文件；缺失、失败或摘要变化均拒绝启动。`--dev` 为显式源码开发入口，不产生生产接纳结论。

证据覆盖 runtime 来源/目录树、协议导出、catalog、experimental 正反例、工具面、调用成功/失败、跨进程恢复与部署绑定。`scripts/runtime_probe.py` 只执行合成协议子集，报告中未执行项仍保持 not_run，不能将协议通过等同生产接纳。

自定义 Dynamic Tools 的正式发布必须提供与当前 catalog、运行时和真实模型 wire surface 绑定的 compatibility_profile。MCP 与 Skills 使用原生接口，不依赖业务 Tool catalog。

Provider 在发布验证后加载，扩展源码目录和显式 Skill 根纳入测量。更换业务扩展只修改配置与接纳制品，不修改 Host Agent Loop。
