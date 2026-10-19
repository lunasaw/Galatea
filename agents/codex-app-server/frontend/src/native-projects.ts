import { api, requestKey } from "./store";
import type { Project, ProjectLayout, ProjectSection } from "./project-state";
import type { Session } from "./types";
type Obj = Record<string, any>;
type Call = (method: string, params: Obj) => Promise<any>;
export async function projectPages(call: Call, method: string, params: Obj = {}) {
  const data: Obj[] = []; let cursor: string | null = null; const seen = new Set<string>();
  do {
    const page = await call(method, { ...params, limit: 100, ...(cursor ? { cursor } : {}) });
    if (!Array.isArray(page.data)) throw new Error("项目服务未返回有效列表");
    data.push(...page.data); cursor = page.nextCursor || null;
    if (cursor && seen.has(cursor)) throw new Error("项目服务返回重复游标");
    if (cursor) seen.add(cursor);
  } while (cursor);
  return data;
}
export function nativeProject(row: Obj): Project {
  return { id: row.id, name: row.name, path: row.roots[0]?.path || "", section: row.metadata?.webSectionId || "",
    roots: row.roots, metadata: row.metadata };
}
export class NativeProjects {
  groups: ProjectSection[] = []; revision = 0;
  constructor(private call: Call) {}
  async load() {
    const [projects, groups, sections] = await Promise.all([
      projectPages(this.call, "project/list", { sortKey: "position", sortDirection: "asc" }),
      api<{ sections: ProjectSection[]; revision: number }>("/api/sidebar-groups"),
      projectPages(this.call, "threadSection/list"),
    ]);
    this.groups = groups.sections; this.revision = groups.revision;
    return { projects: projects.map(nativeProject), sections: groups.sections, threadSections: sections as ProjectSection[] };
  }
  async groupsUpdate(sections: ProjectSection[]) {
    const result = await api<{ sections: ProjectSection[]; revision: number }>("/api/sidebar-groups", { sections, revision: this.revision }, requestKey());
    this.groups = result.sections; this.revision = result.revision;
  }
  async create(path: string, name: string, section: string) {
    const result = await this.call("project/create", { idempotencyKey: requestKey(), roots: [{ path }], name, metadata: { webSectionId: section } });
    return nativeProject(result.project);
  }
  async migrate(layout: ProjectLayout, sessions: Session[], native: Project[]) {
    const mapping = new Map<string, string>();
    for (const old of layout.projects) {
      const existing = native.find((p) => p.id === old.id || p.metadata?.webLegacyId === old.id);
      if (existing) { mapping.set(old.id, existing.id); continue; }
      const threads = sessions.filter((s) => layout.threadProjects[s.id] === old.id && !s.settings?.projectId).map((s) => s.thread_id).filter(Boolean);
      const result = await this.call("project/import", { idempotencyKey: "web-import:" + old.id,
        name: old.name, roots: [{ path: old.path }], threads, metadata: { webLegacyId: old.id, webSectionId: old.section } });
      mapping.set(old.id, result.project.id);
    }
    if (layout.sections.length) await this.groupsUpdate([...this.groups, ...layout.sections.filter((s) => !this.groups.some((g) => g.id === s.id))]);
    return mapping;
  }
  async update(previous: ProjectLayout, update: Partial<ProjectLayout>, sessions: Session[]) {
    if (update.sections) await this.groupsUpdate(update.sections);
    if (update.projects) {
      for (const project of update.projects) {
        const old = previous.projects.find((p) => p.id === project.id);
        if (!old) continue; // New projects are created through project/create.
        const rootsChanged = JSON.stringify(old.roots) !== JSON.stringify(project.roots);
        if (old.name !== project.name || old.path !== project.path || old.section !== project.section || rootsChanged) {
          const { project: current } = await this.call("project/read", { projectId: project.id });
          await this.call("project/update", { projectId: project.id,
            ...(old.name !== project.name ? { name: project.name } : {}),
            ...(rootsChanged ? { roots: project.roots } : old.path !== project.path ? {
              roots: current.roots?.map((r: Obj, i: number) => i ? r : { ...r, path: project.path }) || [{ path: project.path }],
            } : {}),
            ...(old.section !== project.section ? { metadata: { ...current.metadata, webSectionId: project.section } } : {}) });
        }
      }
      for (const old of previous.projects.filter((p) => !update.projects!.some((n) => n.id === p.id)))
        await this.call("project/delete", { projectId: old.id });
      if (previous.projects.map((p) => p.id).join() !== update.projects.map((p) => p.id).join())
        for (let i = update.projects.length - 1; i >= 0; i--)
          await this.call("project/move", { projectId: update.projects[i].id, beforeProjectId: update.projects[i + 1]?.id || null });
    }
    for (const [id, projectId] of Object.entries(update.threadProjects || {})) {
      const session = sessions.find((s) => s.id === id);
      if (session?.thread_id) await this.call("thread/metadata/update", { threadId: session.thread_id, projectId });
    }
  }
}
