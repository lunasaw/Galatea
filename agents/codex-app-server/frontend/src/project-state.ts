export type Project = {
  id: string;
  path: string;
  name: string;
  section: string;
};
export type ProjectSection = { id: string; name: string };
export type ProjectLayout = {
  projects: Project[];
  sections: ProjectSection[];
  collapsed: Record<string, boolean>;
  threadOrder: string[];
  threadProjects: Record<string, string>;
  hiddenPaths: string[];
};
export const emptyLayout = (): ProjectLayout => ({
  projects: [],
  sections: [],
  collapsed: {},
  threadOrder: [],
  threadProjects: {},
  hiddenPaths: [],
});
export const projectName = (path: string) =>
  path.split("/").filter(Boolean).at(-1) || path;
// A stable identity for the old path-based projects, including unsaved discoveries.
export const legacyProjectId = (path: string) => "folder:" + path;
export function normalizeProjectPath(path: string) {
  const value = path.trim();
  if (!value.startsWith("/") || value.includes("\0"))
    throw new Error("请输入服务器上的绝对目录路径");
  const parts: string[] = [];
  for (const part of value.split("/")) {
    if (part === "..") parts.pop();
    else if (part && part !== ".") parts.push(part);
  }
  return "/" + parts.join("/");
}
export function parseProjectLayout(raw: string | null): ProjectLayout {
  try {
    const data = JSON.parse(raw || "null");
    if (!data || !Array.isArray(data.projects) || !Array.isArray(data.sections))
      return emptyLayout();
    const projects: Project[] = data.projects
      .filter(
        (p: Project) =>
          p &&
          typeof p.path === "string" &&
          p.path.startsWith("/") &&
          typeof p.name === "string" &&
          typeof p.section === "string",
      )
      .map((p: Project) => ({
        ...p,
        id: typeof p.id === "string" && p.id ? p.id : legacyProjectId(p.path),
      }));
    const collapsed =
      data.collapsed && typeof data.collapsed === "object"
        ? { ...data.collapsed }
        : {};
    for (const p of projects) {
      if (
        p.id === legacyProjectId(p.path) &&
        collapsed[p.id] === undefined &&
        typeof collapsed[p.path] === "boolean"
      )
        collapsed[p.id] = collapsed[p.path];
    }
    return {
      projects,
      hiddenPaths: Array.isArray(data.hiddenPaths)
        ? data.hiddenPaths.filter((path: unknown) => typeof path === "string") : [],
      sections: data.sections.filter(
        (s: ProjectSection) =>
          s && typeof s.id === "string" && typeof s.name === "string",
      ),
      collapsed,
      threadOrder: Array.isArray(data.threadOrder)
        ? data.threadOrder.filter((id: unknown) => typeof id === "string")
        : [],
      threadProjects:
        data.threadProjects && typeof data.threadProjects === "object"
          ? (Object.fromEntries(
              Object.entries(data.threadProjects).filter(
                ([, id]) => typeof id === "string",
              ),
            ) as Record<string, string>)
          : {},
    };
  } catch {
    return emptyLayout();
  }
}
export function mergeProjects(layout: ProjectLayout, paths: string[]) {
  const projects = [...layout.projects];
  for (const path of paths.filter(Boolean)) {
    if (!layout.hiddenPaths.includes(path) && !projects.some((p) => p.path === path))
      projects.push({
        id: legacyProjectId(path),
        path,
        name: projectName(path),
        section: "",
      });
  }
  return projects;
}
export function projectForPath(projects: Project[], path: string) {
  const matches = projects.filter((p) => p.path === path);
  // Fallback must be independent of sidebar ordering for older/unassigned sessions.
  return (
    matches.find((p) => p.id === legacyProjectId(path)) ||
    matches.sort((a, b) => a.id.localeCompare(b.id))[0]
  );
}
export function moveProject(
  projects: Project[],
  id: string,
  section: string,
  before?: string,
) {
  const project = projects.find((p) => p.id === id);
  if (!project || id === before) return projects;
  const next = projects.filter((p) => p.id !== id);
  const index = before ? next.findIndex((p) => p.id === before) : -1;
  next.splice(index < 0 ? next.length : index, 0, { ...project, section });
  return next;
}
export function moveThread(order: string[], id: string, before?: string) {
  if (id === before) return order;
  const next = order.filter((item) => item !== id);
  const index = before ? next.indexOf(before) : -1;
  next.splice(index < 0 ? next.length : index, 0, id);
  return next;
}
