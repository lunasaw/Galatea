import * as React from "react";
import { SidebarProvider, SidebarRail, useSidebar } from "./components/ui/sidebar";

const STORAGE_KEY = "codex-agent.sidebar-width";
const DEFAULT_WIDTH = 320;
const MIN_WIDTH = 240;
const MAX_WIDTH = 640;

const ResizeContext = React.createContext<{
  width: number;
  maxWidth: number;
  setWidth: (width: number) => void;
  setResizing: (resizing: boolean) => void;
  setPanelWidth: (width: number) => void;
} | null>(null);

export function ResizableSidebarProvider({ children }: { children: React.ReactNode }) {
  const [preferredWidth, setPreferredWidth] = React.useState(() => {
    try {
      const saved = Number(localStorage.getItem(STORAGE_KEY));
      if (Number.isFinite(saved) && saved >= MIN_WIDTH) return Math.min(MAX_WIDTH, saved);
    } catch { /* Browser storage may be unavailable. */ }
    return DEFAULT_WIDTH;
  });
  const [viewportWidth, setViewportWidth] = React.useState(() => window.innerWidth);
  const [panelWidth, setPanelWidth] = React.useState(0);
  const [resizing, setResizing] = React.useState(false);
  // Reserve space for the conversation, navigation rail and any open task panel.
  const maxWidth = Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, viewportWidth - 450 - panelWidth));
  const width = Math.min(preferredWidth, maxWidth);
  const setWidth = React.useCallback((value: number) => {
    setPreferredWidth(Math.round(Math.max(MIN_WIDTH, Math.min(maxWidth, value))));
  }, [maxWidth]);

  React.useEffect(() => {
    const fit = () => setViewportWidth(window.innerWidth);
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, []);

  React.useEffect(() => {
    if (resizing) return;
    try { localStorage.setItem(STORAGE_KEY, String(preferredWidth)); }
    catch { /* Resizing remains usable without persistence. */ }
  }, [preferredWidth, resizing]);

  React.useEffect(() => {
    if (!resizing) return;
    document.body.setAttribute("data-sidebar-resizing", "true");
    return () => document.body.removeAttribute("data-sidebar-resizing");
  }, [resizing]);

  return (
    <ResizeContext.Provider value={{ width, maxWidth, setWidth, setResizing, setPanelWidth }}>
      <SidebarProvider
        className="h-dvh overflow-hidden"
        data-sidebar-resizing={resizing || undefined}
        style={{ "--sidebar-width": `${width}px` } as React.CSSProperties}
      >
        {children}
      </SidebarProvider>
    </ResizeContext.Provider>
  );
}

export function useSidebarPanelRef() {
  const context = React.useContext(ResizeContext);
  if (!context) throw new Error("useSidebarPanelRef requires ResizableSidebarProvider");
  const { setPanelWidth } = context;
  return React.useCallback((node: HTMLElement | null) => {
    if (!node) return;
    const measure = () => setPanelWidth(Math.round(node.getBoundingClientRect().width));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => {
      observer.disconnect();
      setPanelWidth(0);
    };
  }, [setPanelWidth]);
}

export function SidebarResizeHandle() {
  const { isMobile, state } = useSidebar();
  const context = React.useContext(ResizeContext);
  if (!context) throw new Error("SidebarResizeHandle requires ResizableSidebarProvider");
  const { width, maxWidth, setWidth, setResizing } = context;
  const drag = React.useRef<{ pointerId: number; x: number; width: number } | null>(null);

  React.useEffect(() => () => {
    drag.current = null;
    setResizing(false);
  }, [isMobile, state, setResizing]);

  if (isMobile) return null;
  if (state === "collapsed") return <SidebarRail />;

  const stop = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return;
    drag.current = null;
    setResizing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div
      className="sidebar-resize-handle"
      role="separator"
      aria-label="调整会话列表宽度"
      aria-orientation="vertical"
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={maxWidth}
      aria-valuenow={width}
      aria-valuetext={`${width} 像素`}
      tabIndex={0}
      title="拖动调整会话列表宽度，双击恢复默认"
      onDoubleClick={() => setWidth(DEFAULT_WIDTH)}
      onKeyDown={(event) => {
        const next = {
          ArrowLeft: width - 16,
          ArrowRight: width + 16,
          Home: MIN_WIDTH,
          End: maxWidth,
        }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        setWidth(next);
      }}
      onPointerDown={(event) => {
        if (event.button !== 0 || !event.isPrimary) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { pointerId: event.pointerId, x: event.clientX, width };
        setResizing(true);
      }}
      onPointerMove={(event) => {
        if (drag.current?.pointerId !== event.pointerId) return;
        setWidth(drag.current.width + event.clientX - drag.current.x);
      }}
      onPointerUp={stop}
      onPointerCancel={stop}
      onLostPointerCapture={stop}
    />
  );
}
