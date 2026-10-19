import { Component, lazy, Suspense, type ComponentType, type ComponentProps, type ReactNode } from "react";

class LoadError extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <p role="alert">内容加载失败。<button type="button" onClick={() => location.reload()}>刷新重试</button></p> : this.props.children;
  }
}

/** Keep optional views off the initial download, without suspending the chat. */
export function deferred<T extends ComponentType<any>>(load: () => Promise<{ default: T }>) {
  const View = lazy(load);
  return function DeferredView(props: ComponentProps<T>) {
    return <LoadError><Suspense fallback={<span role="status" className="text-sm text-muted-foreground">正在加载…</span>}><View {...props} /></Suspense></LoadError>;
  };
}
