import {
  useAuiState,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { ToolFallback } from "./components/assistant-ui/elements/tool-fallback.aui";
import { toolState } from "./messages";
import type { Step } from "./types";

// The host can observe a call without its receipt. Preserve that distinction
// while delegating disclosure, animation, arguments and results to assistant-ui.
export function CodexToolFallback(props: ToolCallMessagePartProps) {
  const step = useAuiState((s) => s.message.metadata.custom?.step) as
    | Step
    | undefined;
  if (!step) return <ToolFallback {...props} />;
  const state = toolState(step);
  const status: ToolCallMessagePartProps["status"] =
    state === "running"
      ? { type: "running" }
      : state === "failed"
        ? { type: "incomplete", reason: "error", error: "工具执行失败" }
        : { type: "complete" };
  return (
    <ToolFallback.Root>
      <ToolFallback.Trigger
        toolName={props.toolName}
        status={status}
        label={state === "unknown" ? "已观察到调用（结果未知）" : undefined}
      />
      <ToolFallback.Content>
        <ToolFallback.Error status={status} />
        <ToolFallback.Args argsText={props.argsText} />
        {step.text && <pre className="native-terminal">{step.text}</pre>}
        <ToolFallback.Result result={props.result} />
        {step.job && (
          <p className="text-xs text-muted-foreground">
            作业 {step.job.job_id} · {step.job.status || "状态未知"}
            （最近一次观察）
          </p>
        )}
        {step.observed_at && (
          <p className="text-xs text-muted-foreground">
            最近观察：{step.observed_at}
          </p>
        )}
      </ToolFallback.Content>
    </ToolFallback.Root>
  );
}
