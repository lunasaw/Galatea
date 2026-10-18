import { useCallback } from "react";
import { Button } from "./components/ui/button";
import { Feedback, useResource } from "./capability-controls";
import { type Obj } from "./native";
import type { ConsoleState } from "./store";
import { readAccountUsage, rateLimitBuckets } from "./account-usage-data";

export function AccountUsage({ state }: { state: ConsoleState }) {
  const resource = useResource(useCallback(readAccountUsage, []));
  const data = resource.data;
  const buckets = rateLimitBuckets(data?.limits);
  return <section className="capability-page account-usage"><h3>使用情况和计费</h3>
    <p className="muted">查看账户额度、重置时间和账单。</p>
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
    {!resource.loading && !buckets.length && <p className="muted">{data?.account?.type === "apiKey" ? "API 密钥账户的额度和账单请在服务商控制台查看。" : "当前连接未提供账户额度。"}</p>}
    <h4>账单</h4><p className="muted">账单和充值请在当前账户所用服务商的控制台管理。</p>
    <Feedback error={resource.error || data?.errors.join("\n")} />
  </section>;
}
