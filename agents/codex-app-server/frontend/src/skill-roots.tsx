import { useState } from "react";
import { Button } from "./components/ui/button";
import { Field, Feedback, useAction } from "./capability-controls";
import { native } from "./native";
import { consoleStore as store, type ConsoleState } from "./store";
import { integrationsChanged } from "./integrations";

export function SkillRoots({ state }: { state: ConsoleState }) {
  const [roots, setRoots] = useState((state.nativeProtocol?.skill_roots || []).join("\n")); const action = useAction();
  return <details><summary>扩展 Skills 目录</summary><p>目录保存在当前 Host，重启后继续使用。</p>
    <Field label="Skills 根目录（每行一个绝对路径）"><textarea value={roots} onChange={(e) => setRoots(e.target.value)} /></Field>
    <Button variant="outline" disabled={action.busy || !state.nativeProtocol?.configuration_editable} onClick={() => void action.run(async () => {
      const extraRoots = [...new Set(roots.split("\n").map((s) => s.trim()).filter(Boolean))];
      if (extraRoots.some((p) => !p.startsWith("/"))) throw new Error("请填写绝对目录路径");
      for (const path of extraRoots) if (!(await native("fs/getMetadata", { path })).isDirectory) throw new Error(`${path} 不是目录`);
      await native("skills/extraRoots/set", { extraRoots });
      if (store.state.nativeProtocol) store.patch({ nativeProtocol: { ...store.state.nativeProtocol, skill_roots: extraRoots } });
      integrationsChanged();
    }, "Skills 目录已保存")}>保存扩展目录</Button><Feedback error={action.error} status={action.status} />
  </details>;
}
