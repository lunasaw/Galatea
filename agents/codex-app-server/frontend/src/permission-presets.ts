export const permissionPresets = {
  ask: {
    label: "请求批准",
    description: "编辑外部文件和使用互联网时始终询问",
    sandbox: "workspace-write",
    approvalPolicy: "on-request",
    approvalsReviewer: "user",
  },
  auto: {
    label: "帮我批准",
    description: "自动审查操作，仅在需要时请求批准",
    sandbox: "workspace-write",
    approvalPolicy: "on-request",
    approvalsReviewer: "auto_review",
  },
  full: {
    label: "完全访问权限",
    description: "可不受限制地访问互联网和此电脑上的文件",
    sandbox: "danger-full-access",
    approvalPolicy: "never",
    approvalsReviewer: "user",
  },
} as const;
export type PermissionPreset = keyof typeof permissionPresets;
export function permissionSettings(preset: PermissionPreset, start = false) {
  const { sandbox, approvalPolicy, approvalsReviewer } =
    permissionPresets[preset];
  return {
    approvalPolicy,
    approvalsReviewer,
    ...(start
      ? { sandbox }
      : {
          sandboxPolicy:
            sandbox === "workspace-write"
              ? {
                  type: "workspaceWrite",
                  writableRoots: [],
                  networkAccess: false,
                  excludeTmpdirEnvVar: false,
                  excludeSlashTmp: false,
                }
              : { type: "dangerFullAccess" },
        }),
  };
}
export function identifyPermission(
  settings: Record<string, any>,
): PermissionPreset | "custom" {
  if (settings.permissions) return "custom";
  const sandbox =
    settings.sandboxPolicy?.type || settings.sandbox || settings.sandbox_mode;
  const approval = settings.approvalPolicy || settings.approval_policy;
  const reviewer =
    settings.approvalsReviewer || settings.approvals_reviewer || "user";
  if (
    ["dangerFullAccess", "danger-full-access"].includes(sandbox) &&
    approval === "never"
  )
    return "full";
  if (
    ["workspaceWrite", "workspace-write"].includes(sandbox) &&
    approval === "on-request"
  ) {
    if (
      settings.sandboxPolicy?.networkAccess ||
      settings.sandboxPolicy?.writableRoots?.length
    )
      return "custom";
    return ["auto_review", "guardian_subagent"].includes(reviewer)
      ? "auto"
      : "ask";
  }
  return "custom";
}
