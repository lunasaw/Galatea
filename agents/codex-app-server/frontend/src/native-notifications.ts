import type { Obj } from "./native";
export const nativeNoticeTitles: Record<string, string> = {
  configWarning: "配置警告", deprecationNotice: "弃用提示", guardianWarning: "自动审查警告",
  "autoApprovalReview/strictReviewRequired": "需要严格审查", "model/rerouted": "模型已切换",
  "model/verification": "模型验证状态", "modelProvider/authRecoveryStarted": "正在恢复服务商认证",
  "modelProvider/authRecoveryCompleted": "服务商认证恢复结果",
  "model/safetyBuffering/updated": "模型安全缓冲状态", "windows/worldWritableWarning": "Windows 目录权限警告",
  "account/login/completed": "登录结果", "account/gatewayOAuth/changed": "Gateway 登录状态",
  "thread/prediction/updated": "后续输入预测", "thread/environment/disconnected": "执行环境已断开",
  "externalAgentConfig/import/progress": "配置导入进度", "externalAgentConfig/import/completed": "配置导入结果",
};
export type NativeNotice = { id: string; method: string; title: string; params: Obj };
export function nativeNotice(event: Obj): NativeNotice | undefined {
  // Moderation metadata is diagnostic-only, matching the CLI's notification handling.
  if (event.direction !== "runtime_to_host" || event.kind !== "notification" || !nativeNoticeTitles[event.method]) return;
  return { id: event.event_id, method: event.method, title: nativeNoticeTitles[event.method], params: event.payload?.params || {} };
}
export const invalidatesNativeResources = (event: Obj) => event.direction === "runtime_to_host" && event.kind === "notification"
  && ["account/updated", "account/login/completed", "account/gatewayOAuth/changed", "account/rateLimits/updated",
    "project/changed", "thread/project/updated", "thread/name/updated", "skills/changed", "thread/attachment/updated",
    "thread/readState/changed", "thread/settings/updated", "thread/goal/updated", "thread/goal/cleared",
    "thread/environment/connected", "thread/environment/disconnected", "remoteControl/status/changed",
    "mcpServer/oauthLogin/completed", "mcpServer/startupStatus/updated",
    "externalAgentConfig/import/progress", "externalAgentConfig/import/completed"].includes(event.method);
