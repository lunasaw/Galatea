import type { ConsoleState } from "./store";
import { consoleStore as store } from "./store";
import { Button } from "./components/ui/button";
import { Facts } from "./capability-controls";
export function NativeNoticeCenter({ state }: { state: ConsoleState }) {
  return <div className="native-notice-center" aria-live="polite">{state.nativeNotices.slice(-5).map((notice) => <details key={notice.id} className="p-2 bg-muted"><summary>{notice.title}</summary><Facts value={notice.params} /><Button variant="ghost" size="sm" onClick={() => store.patch({ nativeNotices: state.nativeNotices.filter((n) => n.id !== notice.id) })}>关闭提示</Button></details>)}</div>;
}
