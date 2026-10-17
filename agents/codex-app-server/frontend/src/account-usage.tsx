import { useCallback } from "react";
import { Button } from "./components/ui/button";
import { Feedback, useResource } from "./capability-controls";
import { native, type Obj } from "./native";
import type { ConsoleState } from "./store";
import { contextUsedPercent } from "./slash-commands";

export function AccountUsage({ state }: { state: ConsoleState }) {
  const resource = useResource(useCallback(async () => {
    const [account, limits] = await Promise.allSettled([native("account/read", { refreshToken: false }), native("account/rateLimits/read", {})]);
    return { account: account.status === "fulfilled" ? account.value.account : null,
      limits: limits.status === "fulfilled" ? limits.value : null,
      errors: [account, limits].flatMap((result) => result.status === "rejected" ? [String(result.reason)] : []) };
  }, []));
  const data = resource.data;
  const buckets: Obj[] = data?.limits?.rateLimitsByLimitId ? Object.values(data.limits.rateLimitsByLimitId)
    : data?.limits?.rateLimits ? [data.limits.rateLimits] : [];
  const usage = state.snapshot?.turns?.slice().reverse().find((turn) => turn.usage)?.usage as Obj | undefined;
  const used = contextUsedPercent(state.snapshot);
  return <section className="capability-page account-usage"><h3>使用情况和计费</h3>
    <Button variant="outline" disabled={resource.loading} onClick={() => void resource.refresh()}>{resource.loading ? "正在读取…" : "刷新使用情况"}</Button>
    <h4>账户</h4><p>{data?.account?.email || (data?.account?.type === "apiKey" ? "API 密钥登录" : data?.account ? "已登录" : "未提供账户信息")}{data?.account?.planType && ` · ${data.account.planType}`}</p>
    {buckets.map((bucket, index) => <div className="desktop-settings-group" key={bucket.limitId || index}>
      <h4>{bucket.limitName || bucket.limitId || "账户额度"}</h4>
      {[bucket.primary, bucket.secondary].filter(Boolean).map((window: Obj, i) => <div className="desktop-setting-row" key={i}>
        <div><label>{window.windowDurationMins ? `${window.windowDurationMins} 分钟额度` : i ? "长期额度" : "短期额度"}</label>
          {window.resetsAt != null && <p>重置时间：{new Date(window.resetsAt * 1000).toLocaleString()}</p>}</div>
        <div className="desktop-setting-control"><span>已使用 {window.usedPercent}%</span></div>
      </div>)}
      {bucket.credits && <div className="desktop-setting-row"><label>余额</label><span>{bucket.credits.unlimited ? "不限额度" : bucket.credits.balance ?? "未提供"}</span></div>}
    </div>)}
    {!resource.loading && !buckets.length && <p className="muted">当前连接未提供账户额度。</p>}
    {usage && <><h4>当前聊天</h4><dl className="capability-facts">
      {used !== null && <div><dt>上下文占用</dt><dd>{used}%</dd></div>}
      {typeof usage.total?.totalTokens === "number" && <div><dt>累计 Token</dt><dd>{usage.total.totalTokens.toLocaleString()}</dd></div>}
    </dl></>}
    <h4>账单</h4><p className="muted">账单和充值请在当前账户所用服务商的控制台管理。</p>
    <Feedback error={resource.error || data?.errors.join("\n")} />
  </section>;
}
