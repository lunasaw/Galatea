import type { Snapshot } from "./types";

export const effortNames: Record<string, string> = {
  none: "无", minimal: "最低", low: "低", medium: "中", high: "高", xhigh: "极高", max: "最高", ultra: "Ultra",
};
export type SlashCommand = {
  name: string; label: string; description: string;
  submenu?: boolean; thread?: boolean; idle?: boolean; unavailable?: string;
};
export const slashCommands: SlashCommand[] = [
  { name: "mcp", label: "MCP", description: "显示 MCP 服务器状态", submenu: true },
  { name: "usage", label: "使用情况和计费", description: "打开用量和账单设置" },
  { name: "aside", label: "侧边", description: "发起临时侧边聊天", thread: true, unavailable: "当前 Web 控制台尚未接入临时侧边聊天" },
  { name: "share", label: "分享聊天", description: "创建此聊天快照的链接", thread: true, unavailable: "当前连接未提供聊天快照分享服务" },
  { name: "fork", label: "创建聊天分支", description: "为此聊天创建分支", submenu: true, thread: true, idle: true },
  { name: "init", label: "初始化", description: "创建包含 Codex 指令的 AGENTS.md 文件", idle: true },
  { name: "compact", label: "压缩", description: "压缩此聊天的上下文", thread: true, idle: true },
  { name: "feedback", label: "反馈", description: "发送有关此聊天的反馈" },
  { name: "archive", label: "归档", description: "归档当前聊天", thread: true, idle: true },
  { name: "reasoning", label: "推理", description: "选择推理强度", submenu: true },
  { name: "model", label: "模型", description: "选择模型", submenu: true },
  { name: "new", label: "新聊天", description: "开始新的聊天" },
  { name: "plan", label: "计划模式", description: "切换计划模式", idle: true },
  { name: "goal", label: "目标", description: "设置要持续追求的目标" },
  { name: "queue", label: "输入队列", description: "查看待发送消息", thread: true },
  { name: "search", label: "搜索", description: "搜索文件和聊天" },
  { name: "permissions", label: "权限", description: "设置文件与命令权限", idle: true },
  { name: "skills", label: "Skills", description: "浏览可用 Skills" },
  { name: "apps", label: "Apps", description: "浏览 Apps" },
  { name: "plugins", label: "插件", description: "管理插件" },
  { name: "resume", label: "恢复聊天", description: "查看历史聊天" },
  { name: "review", label: "审查", description: "审查未提交的修改", thread: true, idle: true },
  { name: "undo", label: "回退", description: "回退上一轮上下文", thread: true, idle: true },
  { name: "status", label: "状态", description: "查看聊天状态" },
  { name: "help", label: "帮助", description: "快捷命令与帮助" },
];
export function filterSlashCommands(text: string) {
  if (!/^\/[^\s/]*$/u.test(text)) return [];
  const query = text.slice(1).toLocaleLowerCase();
  return slashCommands.filter((row) => `${row.name} ${row.label} ${row.description}`.toLocaleLowerCase().includes(query));
}
export function exactSlashCommand(text: string) {
  return slashCommands.find((row) => text.trim().toLowerCase() === `/${row.name}`);
}
export function contextUsedPercent(snapshot: Snapshot | null): number | null {
  const usage = snapshot?.turns?.slice().reverse().find((turn) => turn.usage)?.usage as {
    last?: { totalTokens?: number }; modelContextWindow?: number;
  } | undefined;
  const used = usage?.last?.totalTokens, limit = usage?.modelContextWindow;
  if (typeof used !== "number" || typeof limit !== "number" || !Number.isFinite(used) || !Number.isFinite(limit) || used < 0 || limit <= 0) return null;
  return Math.min(100, Math.round(used / limit * 100));
}
// Like the native CLI's /init, this starts an ordinary agent turn. Filesystem
// access continues through the thread's native permission and approval policy.
export const initPrompt = `请为当前仓库创建 AGENTS.md，作为 Codex 和贡献者的项目指南。
先检查当前工作目录中是否已有 AGENTS.md；若已存在，不覆盖、不修改该文件，并告知用户。
阅读仓库结构、README、构建配置、测试和提交惯例后，编写简明、具体、可执行的说明。
使用标题 Repository Guidelines，按项目情况涵盖：项目结构、构建与测试命令、代码风格、测试规范、提交与 Pull Request 要求，以及适用的安全和配置注意事项。
提供经过仓库验证的路径和命令示例；不要虚构命令、依赖或规范。`;
