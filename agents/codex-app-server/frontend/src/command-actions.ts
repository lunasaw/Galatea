export type CommandAction = {
  type: "read" | "search" | "listFiles";
  path?: string;
  name?: string;
  query?: string;
};

/** Only use the runtime's structured classifications; shell text is not authoritative. */
export function commandActions(
  detail: Record<string, unknown>,
): CommandAction[] {
  if (!Array.isArray(detail.commandActions)) return [];
  if (
    !detail.commandActions.length ||
    detail.commandActions.some(
      (action) =>
        !action || !["read", "search", "listFiles"].includes(action.type),
    )
  )
    return [];
  return detail.commandActions.map((action) => ({
    type: action.type,
    ...(typeof action.path === "string" ? { path: action.path } : {}),
    ...(typeof action.name === "string" ? { name: action.name } : {}),
    ...(typeof action.query === "string" ? { query: action.query } : {}),
  }));
}
