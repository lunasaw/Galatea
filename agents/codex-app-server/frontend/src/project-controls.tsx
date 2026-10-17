import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type DragEvent,
} from "react";
import { useAuiState, ThreadListPrimitive } from "@assistant-ui/react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  Folder,
  FolderPlus,
  MoreHorizontal,
  Plus,
  X,
} from "lucide-react";
import { consoleStore as store } from "./store";
import { native } from "./native";
import {
  moveProject,
  moveThread,
  normalizeProjectPath,
  projectName,
} from "./project-state";
import { Button } from "./components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "./components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./components/ui/dropdown-menu";

export function ProjectDialog({
  open,
  onOpenChange,
  section,
  select = false,
  initialPath,
}: {
  open: boolean;
  onOpenChange: (value: boolean) => void;
  section?: string;
  select?: boolean;
  initialPath?: string;
}) {
  const [path, setPath] = useState("");
  const [browsed, setBrowsed] = useState("");
  const [folders, setFolders] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const addedProject = useRef<{
    id: string;
    path: string;
    name: string;
    section: string;
  } | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(0);
  const browse = async (value: string) => {
    const version = ++request.current;
    setBusy(true);
    setError("");
    setFolders([]);
    setBrowsed("");
    try {
      const resolved = normalizeProjectPath(value);
      const result = await native("fs/readDirectory", { path: resolved });
      if (version !== request.current) return;
      setPath(resolved);
      setBrowsed(resolved);
      setFolders(
        (result.entries || [])
          .filter((e: { isDirectory: boolean }) => e.isDirectory)
          .map((e: { fileName: string }) => e.fileName)
          .sort(),
      );
    } catch (e) {
      if (version === request.current) setError(String(e));
    } finally {
      if (version === request.current) setBusy(false);
    }
  };
  useEffect(() => {
    if (open) {
      setCreating(false);
      setName("");
      setLabel("");
      addedProject.current = null;
      setPath(initialPath || store.cwd);
      void browse(initialPath || store.cwd);
    }
    return () => {
      request.current++;
    };
  }, [open]);
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      let resolved = normalizeProjectPath(path);
      if (creating) {
        if (
          !name.trim() ||
          /[\/\0]/.test(name) ||
          [".", ".."].includes(name.trim())
        )
          throw new Error("请输入不含斜杠的文件夹名称");
        resolved = normalizeProjectPath(resolved + "/" + name.trim());
        await native("fs/createDirectory", {
          path: resolved,
          recursive: false,
        });
        setPath(resolved);
        setCreating(false);
        setName("");
      } else await native("fs/readDirectory", { path: resolved });
      const project =
        addedProject.current?.path === resolved &&
        addedProject.current.name ===
          (label.trim() || addedProject.current.name)
          ? addedProject.current
          : await store.addProjectPersistent(resolved, label, section);
      addedProject.current = project;
      if (select && !(await store.chooseProject(project.id))) {
        setError(store.state.error || "未能切换项目，请重试");
        return;
      }
      onOpenChange(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(value) => !busy && onOpenChange(value)}>
      <DialogContent
        className="project-dialog"
        aria-describedby="project-dialog-description"
      >
        <DialogTitle>{creating ? "创建项目" : "添加项目"}</DialogTitle>
        <DialogDescription id="project-dialog-description">
          为项目选择工作文件夹。多个项目可以使用同一个文件夹。
        </DialogDescription>
        <label className="project-name-field">
          项目名称
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="留空使用文件夹名称"
            disabled={busy}
          />
        </label>
        <form
          className="project-path-form"
          onSubmit={(e) => {
            e.preventDefault();
            e.stopPropagation();
            void browse(path);
          }}
        >
          <input
            aria-label="项目目录"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            disabled={busy}
          />
          <Button type="submit" variant="outline" disabled={busy}>
            前往
          </Button>
        </form>
        <div
          className="project-folder-list"
          aria-label="项目文件夹"
          aria-busy={busy}
        >
          <button
            type="button"
            disabled={busy || !browsed || browsed === "/"}
            onClick={() =>
              void browse(browsed.split("/").slice(0, -1).join("/") || "/")
            }
          >
            <ArrowUp size={15} />
            上一级
          </button>
          {busy ? (
            <p role="status">正在读取目录…</p>
          ) : (
            folders.map((folder) => (
              <button
                type="button"
                key={folder}
                onClick={() => void browse(browsed + "/" + folder)}
              >
                <Folder size={15} />
                <span>{folder}</span>
                <ChevronRight size={14} />
              </button>
            ))
          )}
          {!busy && browsed && !folders.length && <p>此目录没有子文件夹</p>}
        </div>
        {creating && (
          <label className="project-name-field">
            文件夹名称
            <input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-project"
              disabled={busy}
            />
          </label>
        )}
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
        <div className="project-dialog-actions">
          <Button
            type="button"
            variant="ghost"
            disabled={busy}
            onClick={() => setCreating(!creating)}
          >
            <FolderPlus size={16} />
            {creating ? "选择已有目录" : "新建文件夹"}
          </Button>
          <Button
            type="button"
            disabled={busy || !path.trim() || (creating && !name.trim())}
            onClick={() => void save()}
          >
            {creating ? "创建并添加" : "添加此项目"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ProjectPicker({ disabled }: { disabled: boolean }) {
  useSyncExternalStore(store.subscribe, store.getSnapshot);
  const [adding, setAdding] = useState(false);
  const cwd = store.cwd;
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            disabled={disabled}
            title={cwd}
            aria-label="选择项目"
          >
            <Folder />
            <span>
              {store.currentProject?.name || projectName(cwd) || "选择工作目录"}
            </span>
            <ChevronDown />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side="top"
          align="start"
          className="project-picker-menu"
        >
          <DropdownMenuLabel>项目</DropdownMenuLabel>
          {store.projects.map((p) => (
            <DropdownMenuItem
              key={p.id}
              onSelect={() => void store.chooseProject(p.id)}
            >
              <Folder />
              <div>
                <span>{p.name}</span>
                <small>{p.path}</small>
              </div>
              {p.id === store.currentProject?.id && (
                <Check className="ml-auto" />
              )}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setAdding(true)}>
            <Plus />
            添加或创建项目…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <ProjectDialog open={adding} onOpenChange={setAdding} select />
    </>
  );
}

type DragItem = { kind: "project" | "thread" | "section"; id: string };
const dragType = "application/x-codex-sidebar";
let dragging: DragItem | null = null;
function startDrag(event: DragEvent, item: DragItem) {
  dragging = item;
  event.dataTransfer.setData(dragType, JSON.stringify(item));
  event.dataTransfer.effectAllowed = "move";
}
function dragOver(event: DragEvent) {
  if (!event.dataTransfer.types.includes(dragType)) return;
  event.preventDefault();
  event.stopPropagation();
  event.dataTransfer.dropEffect = "move";
  event.currentTarget.setAttribute("data-drop-target", "true");
}
function clearTarget(event: DragEvent) {
  event.currentTarget.removeAttribute("data-drop-target");
}
function readDrop(event: DragEvent): DragItem | null {
  event.preventDefault();
  event.stopPropagation();
  clearTarget(event);
  try {
    const item = JSON.parse(event.dataTransfer.getData(dragType));
    dragging = null;
    return item;
  } catch {
    return null;
  }
}
const endDrag = () => {
  dragging = null;
  document
    .querySelectorAll("[data-drop-target]")
    .forEach((e) => e.removeAttribute("data-drop-target"));
};

export function ProjectSidebar({
  ThreadItem,
  search,
}: {
  ThreadItem: React.ComponentType;
  search: string;
}) {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot);
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const threadItems = useAuiState((s) => s.threads.threadItems);
  const projects = store.projects;
  const layout = state.projectLayout;
  const sectionForThread = (id: string) => {
    const sectionId = state.sessions.find((s) => s.id === id)?.section_id;
    return state.threadSections.some((section) => section.id === sectionId) ? sectionId : "";
  };
  const [adding, setAdding] = useState<string | null>(null);
  const [edit, setEdit] = useState<{
    kind: "section" | "project" | "threadSection";
    id: string;
    name: string;
    roots?: Array<{ path: string }>;
  } | null>(null);
  const [notice, setNotice] = useState("");
  const [removing, setRemoving] = useState<{ id: string; name: string } | null>(
    null,
  );
  const query = search.trim().toLowerCase();
  const toggle = (key: string) =>
    store.saveProjectLayout({
      collapsed: { ...layout.collapsed, [key]: !layout.collapsed[key] },
    });
  const placeProject = async (
    projectId: string,
    section: string,
    before?: string,
  ) => {
    const accepted = await store.saveProjectLayout({
      projects: moveProject(projects, projectId, section, before),
      collapsed: { ...layout.collapsed, ["section:" + section]: false },
    });
    setNotice(accepted ? "项目位置已保存" : "项目移动失败，请查看错误信息");
  };
  const placeThread = async (
    id: string,
    projectId: string,
    before?: string,
  ) => {
    if (!(await store.moveSessionToProject(id, projectId))) return;
    try {
      if (await store.orderThread(id, before)) {
        setNotice("会话位置已保存"); return;
      }
    } catch (error) {
      store.fail(error); setNotice("会话排序失败，请重试"); return;
    }
    store.saveProjectLayout({
      threadOrder: moveThread(
        [...new Set([...store.state.projectLayout.threadOrder, ...threadIds])],
        id,
        before,
      ),
      collapsed: { ...store.state.projectLayout.collapsed, [projectId]: false },
    });
    setNotice("会话位置已保存");
  };
  const sectionOrder = (id: string, before?: string) => {
    const section = layout.sections.find((s) => s.id === id);
    if (!section || id === before) return;
    const sections = layout.sections.filter((s) => s.id !== id);
    const index = before ? sections.findIndex((s) => s.id === before) : -1;
    sections.splice(index < 0 ? sections.length : index, 0, section);
    store.saveProjectLayout({ sections });
  };
  const sections = [...layout.sections, { id: "", name: "项目" }];
  const items = new Map(threadItems.map((item) => [item.id, item]));
  const indexById = new Map(threadIds.map((id, index) => [id, index]));
  const renderThread = (id: string, projectId: string) => (
    <div
      key={id}
      className="project-thread-row"
      draggable
      onDragStart={(event) => startDrag(event, { kind: "thread", id })}
      onDragEnd={endDrag}
      onDragOver={(event) => {
        if (dragging?.kind === "thread") dragOver(event);
      }}
      onDragLeave={clearTarget}
      onDrop={(event) => {
        const item = readDrop(event);
        if (item?.kind === "thread") {
          if (!projectId) void store.moveToThreadSection(item.id, state.sessions.find((s) => s.id === id)?.section_id || null, id).catch(store.fail);
          else void placeThread(item.id, projectId, id);
        }
      }}
    >
      <ThreadListPrimitive.ItemByIndex
        index={indexById.get(id)!}
        components={{ ThreadListItem: ThreadItem }}
      />
    </div>
  );
  const renderProject = (p: (typeof projects)[number]) => {
    const ids = [...new Set([...layout.threadOrder, ...threadIds])].filter(
      (id) =>
        indexById.has(id) &&
        items.get(id)?.custom?.projectId === p.id &&
        (!query ||
          p.name.toLowerCase().includes(query) ||
          p.path.toLowerCase().includes(query) ||
          (items.get(id)?.title || "新会话").toLowerCase().includes(query)),
    );
    if (
      query &&
      !ids.length &&
      !p.name.toLowerCase().includes(query) &&
      !p.path.toLowerCase().includes(query)
    )
      return null;
    const collapsed = !query && layout.collapsed[p.id];
    const siblings = projects.filter((item) => item.section === p.section);
    const position = siblings.findIndex((item) => item.id === p.id);
    return (
      <div
        key={p.id}
        className="sidebar-project-group"
        data-project-path={p.path}
        data-project-id={p.id}
      >
        <div
          className="sidebar-project-row"
          draggable
          onDragStart={(event) =>
            startDrag(event, { kind: "project", id: p.id })
          }
          onDragEnd={endDrag}
          onDragOver={(event) => {
            if (dragging?.kind !== "section") dragOver(event);
          }}
          onDragLeave={clearTarget}
          onDrop={(event) => {
            const item = readDrop(event);
            if (item?.kind === "project")
              placeProject(item.id, p.section, p.id);
            else if (item?.kind === "thread") void placeThread(item.id, p.id);
          }}
        >
          <button
            type="button"
            className="sidebar-project-toggle"
            data-slot="aui_thread-list-group-label"
            aria-expanded={!collapsed}
            onClick={() => toggle(p.id)}
            title={p.path}
          >
            <Folder size={15} />
            <span>{p.name}</span>
            <ChevronRight className="sidebar-project-chevron" size={13} />
          </button>
          <button
            type="button"
            className="sidebar-project-action"
            aria-label={`在 ${p.name} 中新建会话`}
            onClick={() => void store.startProject(p.id)}
          >
            <Plus size={14} />
          </button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="sidebar-project-action"
                aria-label={`${p.name} 项目菜单`}
              >
                <MoreHorizontal size={15} />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuItem onSelect={() => void store.startProject(p.id)}>
                <Plus />
                新建会话
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() =>
                  setEdit({ kind: "project", id: p.id, name: p.name, roots: p.roots?.length ? p.roots.map((r) => ({ ...r })) : [{ path: p.path }] })
                }
              >
                编辑项目
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={position === 0}
                onSelect={() =>
                  placeProject(p.id, p.section, siblings[position - 1]?.id)
                }
              >
                <ArrowUp />
                上移
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={position === siblings.length - 1}
                onSelect={() =>
                  placeProject(p.id, p.section, siblings[position + 2]?.id)
                }
              >
                <ArrowDown />
                下移
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setRemoving(p)}>
                移除项目
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>移动到分组</DropdownMenuLabel>
              {sections.map((s) => (
                <DropdownMenuItem
                  key={s.id}
                  onSelect={() => placeProject(p.id, s.id)}
                >
                  {s.name}
                  {p.section === s.id && <Check className="ml-auto" />}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {!collapsed && (
          <div className="project-thread-list">
            {ids.map((id) => renderThread(id, p.id))}
            {!ids.length && (
              <button
                type="button"
                className="project-empty"
                aria-label={`开始 ${p.name} 的第一个会话`}
                onClick={() => void store.startProject(p.id)}
              >
                新建会话
              </button>
            )}
          </div>
        )}
      </div>
    );
  };
  return (
    <div className="project-sidebar" inert={state.projectBusy || undefined}>
      {query &&
        !projects.some(
          (p) =>
            p.name.toLowerCase().includes(query) ||
            p.path.toLowerCase().includes(query),
        ) &&
        !threadItems.some((item) =>
          (item.title || "新会话").toLowerCase().includes(query),
        ) && (
          <p
            className="text-muted-foreground px-2.5 py-4 text-sm"
            data-slot="aui_thread-list-empty"
          >
            没有匹配的会话
          </p>
        )}
      <p className="sr-only" role="status">
        {notice}
      </p>
      {sections.map((section, index) => (
        <section key={section.id} data-project-section={section.id}>
          <div
            className="sidebar-section-heading"
            draggable={!!section.id}
            onDragStart={(event) =>
              startDrag(event, { kind: "section", id: section.id })
            }
            onDragEnd={endDrag}
            onDragOver={dragOver}
            onDragLeave={clearTarget}
            onDrop={(event) => {
              const item = readDrop(event);
              if (item?.kind === "section") sectionOrder(item.id, section.id);
              if (item?.kind === "project") placeProject(item.id, section.id);
            }}
          >
            <button
              type="button"
              aria-expanded={!layout.collapsed["section:" + section.id]}
              onClick={() => toggle("section:" + section.id)}
            >
              {section.name}
              <ChevronDown size={12} />
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button type="button" aria-label={`${section.name}分组菜单`}>
                  <MoreHorizontal size={15} />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem
                  onSelect={() =>
                    setEdit({ kind: "section", id: "", name: "" })
                  }
                >
                  创建分组
                </DropdownMenuItem>
                {section.id && (
                  <>
                    <DropdownMenuItem
                      onSelect={() => setEdit({ kind: "section", ...section })}
                    >
                      重命名分组
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={index === 0}
                      onSelect={() =>
                        sectionOrder(section.id, sections[index - 1]?.id)
                      }
                    >
                      上移分组
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      disabled={index >= layout.sections.length - 1}
                      onSelect={() =>
                        sectionOrder(section.id, sections[index + 2]?.id)
                      }
                    >
                      下移分组
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onSelect={() =>
                        store.saveProjectLayout({
                          sections: layout.sections.filter(
                            (s) => s.id !== section.id,
                          ),
                          projects: projects.map((p) =>
                            p.section === section.id
                              ? { ...p, section: "" }
                              : p,
                          ),
                        })
                      }
                    >
                      移除分组，保留项目
                    </DropdownMenuItem>
                  </>
                )}
                <DropdownMenuItem
                  onSelect={() =>
                    store.saveProjectLayout({
                      collapsed: Object.fromEntries(
                        projects.map((p) => [p.id, false]),
                      ),
                    })
                  }
                >
                  展开全部项目
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() =>
                    store.saveProjectLayout({
                      collapsed: Object.fromEntries(
                        projects.map((p) => [p.id, true]),
                      ),
                    })
                  }
                >
                  收起全部项目
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <button
              type="button"
              aria-label={`添加项目到${section.name}`}
              onClick={() => setAdding(section.id)}
            >
              <Plus size={16} />
            </button>
          </div>
          {(query || !layout.collapsed["section:" + section.id]) &&
            projects
              .filter(
                (p) =>
                  p.section === section.id ||
                  (!section.id &&
                    !layout.sections.some((s) => s.id === p.section)),
              )
              .map(renderProject)}
        </section>
      ))}
      <div className="sidebar-section-heading"><span>会话分组</span><button aria-label="创建会话分组" onClick={() => setEdit({ kind: "threadSection", id: "", name: "" })}><Plus size={14} /></button></div>
      {[...state.threadSections, { id: "", name: "其他会话" }].map((group) => <section key={group.id}>
        <div className="sidebar-section-heading"><span>{group.name}</span>{group.id && <DropdownMenu><DropdownMenuTrigger asChild><button aria-label={`${group.name}会话分组菜单`}><MoreHorizontal size={14} /></button></DropdownMenuTrigger>
          <DropdownMenuContent><DropdownMenuItem onSelect={() => setEdit({ kind: "threadSection", ...group })}>重命名会话分组</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void native("threadSection/delete", { sectionId: group.id }).then(() => store.refreshProjects()).catch(store.fail)}>删除分组，保留会话</DropdownMenuItem></DropdownMenuContent></DropdownMenu>}
        </div>
        {[...new Set([...layout.threadOrder, ...threadIds])].filter((id) => indexById.has(id) && !items.get(id)?.custom?.projectId && sectionForThread(id) === group.id && (!query || (items.get(id)?.title || "").toLowerCase().includes(query)))
          .map((id) => <div className="unassigned-thread" key={id}>{renderThread(id, "")}<DropdownMenu><DropdownMenuTrigger asChild><button aria-label={`${store.title(id)}移动到会话分组`}><MoreHorizontal size={12} /></button></DropdownMenuTrigger><DropdownMenuContent>
            {[...state.threadSections, { id: "", name: "其他会话" }].map((target) => <DropdownMenuItem key={target.id} onSelect={() => void store.moveToThreadSection(id, target.id || null).catch(store.fail)}>{target.name}</DropdownMenuItem>)}
          </DropdownMenuContent></DropdownMenu></div>)}
      </section>)}
      <ProjectDialog
        open={adding !== null}
        onOpenChange={(open) => !open && setAdding(null)}
        section={adding || ""}
      />
      <Dialog
        open={!!removing}
        onOpenChange={(open) => !open && setRemoving(null)}
      >
        <DialogContent>
          <DialogTitle>移除项目</DialogTitle>
          <DialogDescription>
            将“{removing?.name}
            ”从项目列表移除。文件夹和文件保留，已有会话移至“其他会话”。使用同一文件夹的其他项目不受影响。
          </DialogDescription>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setRemoving(null)}>
              取消
            </Button>
            <Button
              disabled={state.projectBusy}
              onClick={async () => {
                if (removing && await store.removeProject(removing.id)) setRemoving(null);
              }}
            >
              确认移除项目
            </Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={!!edit} onOpenChange={(open) => !open && setEdit(null)}>
        <DialogContent aria-describedby={undefined}>
          <DialogTitle>
            {edit?.kind === "project"
              ? "编辑项目"
              : edit?.id
                ? "重命名分组"
                : "创建分组"}
          </DialogTitle>
          <form
            className="project-edit-form"
            onSubmit={async (event) => {
              event.preventDefault();
              event.stopPropagation();
              if (!edit?.name.trim()) return;
              if (edit.kind === "threadSection") {
                try { await native(edit.id ? "threadSection/update" : "threadSection/create", { ...(edit.id ? { sectionId: edit.id } : {}), name: edit.name.trim() }); await store.refreshProjects(); setEdit(null); }
                catch (error) { store.fail(error); }
                return;
              }
              let accepted;
              if (edit.kind === "project") {
                let roots: Array<{ path: string }>;
                try {
                  roots = (edit.roots || []).map((r) => ({ path: normalizeProjectPath(r.path) }));
                  if (!roots.length || new Set(roots.map((r) => r.path)).size !== roots.length) throw new Error("项目至少需要一个目录，目录不能重复");
                } catch (error) { store.fail(error); return; }
                accepted = await store.saveProjectLayout({
                  projects: projects.map((p) =>
                    p.id === edit.id ? { ...p, name: edit.name.trim(), path: roots[0].path, roots } : p,
                  ),
                });
              } else
                accepted = await store.saveProjectLayout({
                  sections: edit.id
                    ? layout.sections.map((s) =>
                        s.id === edit.id ? { ...s, name: edit.name.trim() } : s,
                      )
                    : [
                        ...layout.sections,
                        { id: crypto.randomUUID(), name: edit.name.trim() },
                      ],
                });
              if (accepted) setEdit(null);
            }}
          >
            <input
              autoFocus
              aria-label="名称"
              value={edit?.name || ""}
              onChange={(event) =>
                setEdit(
                  (value) => value && { ...value, name: event.target.value },
                )
              }
            />
            {edit?.kind === "project" && <fieldset className="project-roots"><legend>工作目录</legend><p>第一个目录用于新聊天。</p>
              {edit.roots?.map((root, index) => <div key={index}><input aria-label={`工作目录 ${index + 1}`} value={root.path} required onChange={(e) => setEdit({ ...edit, roots: edit.roots!.map((r, i) => i === index ? { path: e.target.value } : r) })} />
                <Button type="button" size="icon" variant="ghost" aria-label={`上移目录 ${index + 1}`} disabled={index === 0 || state.projectBusy} onClick={() => { const roots = [...edit.roots!]; [roots[index - 1], roots[index]] = [roots[index], roots[index - 1]]; setEdit({ ...edit, roots }); }}><ArrowUp size={14} /></Button>
                <Button type="button" size="icon" variant="ghost" aria-label={`删除目录 ${index + 1}`} disabled={edit.roots!.length < 2 || state.projectBusy} onClick={() => setEdit({ ...edit, roots: edit.roots!.filter((_, i) => i !== index) })}><X size={14} /></Button></div>)}
              <Button type="button" variant="outline" disabled={state.projectBusy} onClick={() => setEdit({ ...edit, roots: [...edit.roots!, { path: "" }] })}>添加工作目录</Button>
            </fieldset>}
            <Button type="submit" disabled={state.projectBusy || !edit?.name.trim()}>
              保存
            </Button>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
