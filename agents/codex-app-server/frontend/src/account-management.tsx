import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "./components/ui/button";
import { Facts, Feedback, useAction, useResource } from "./capability-controls";
import { native, type Obj } from "./native";
import { observeNative } from "./native-events";
import { NativeOperation } from "./native-operation";
import { SettingRow } from "./model-settings";
import { api, consoleStore as store, requestKey, type ConsoleState } from "./store";

export function AccountManagement({ state }: { state: ConsoleState }) {
  const account = useResource(useCallback(() => native("account/read", { refreshToken: false }), []));
  const gateway = useResource(useCallback(() => native("account/gatewayOAuth/read", null), []));
  const handoffs = useResource(useCallback(() => api<Obj[]>("/api/native/auth-handoffs"), []), 5000);
  const [login, setLogin] = useState<Obj>(); const [event, setEvent] = useState<Obj>();
  const [logout, setLogout] = useState(false); const [credit, setCredit] = useState(false); const [creditKey, setCreditKey] = useState(requestKey);
  const action = useAction(); const cleanup = useRef<(() => void) | undefined>(undefined);
  const refresh = async () => { await account.refresh(); await gateway.refresh(); await handoffs.refresh(); await store.refreshRuntimeDefaults(store.cwd); };
  useEffect(() => {
    let alive = true;
    void observeNative("account/login/completed", (data) => { setEvent(data); setLogin(undefined); void refresh(); }, (e) => setEvent({ error: String(e) })).then((stop) => { if (alive) cleanup.current = stop; else stop(); });
    return () => { alive = false; cleanup.current?.(); };
  }, []);
  const handleLogin = async (result: Obj) => { setLogin(result.loginId ? result : undefined); setEvent(undefined); await refresh(); };
  const link = login?.authUrl || login?.authorizationUrl || login?.verificationUrl;
  const gatewayLink = handoffs.data?.find((row) => row.providerId === gateway.data?.providerId)?.authUrl;
  const current = account.data?.account;
  return <section className="capability-page"><h3>账户与登录</h3><p className="muted">管理当前运行实例使用的账户、登录方式和授权连接。</p>
    <div className="desktop-settings-group"><SettingRow label="当前账户" description={current?.email || (current ? "此实例已登录" : "登录后可使用账户对应的模型与服务")}><span>{account.loading ? "读取中…" : current?.type === "apiKey" ? "API Key" : current?.type === "chatgpt" ? "ChatGPT" : current?.type || "未登录"}</span></SettingRow>
      {current?.planType && <SettingRow label="账户方案"><span>{current.planType}</span></SettingRow>}</div>
    <NativeOperation state={state} method="account/login/start" title="登录账户" description="凭据交给原生认证存储。支持 ChatGPT 浏览器/设备码、API Key 和 Bedrock；登录条件由运行实例决定。"
      transform={(s) => ({ ...s, oneOf: s.oneOf?.filter((branch: Obj) => branch.properties?.type?.enum?.[0] !== "chatgptAuthTokens") })} initial={{ type: "chatgpt" }} onResult={handleLogin} />
    {login && <div role="status">{link && /^https?:\/\//.test(link) && <p><a href={link} target="_blank" rel="noreferrer">打开登录页面 ↗</a></p>}{login.userCode && <p>设备码：<strong>{login.userCode}</strong></p>}
      <Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => { await native("account/login/cancel", { loginId: login.loginId }); setLogin(undefined); }, "登录已取消")}>取消登录</Button></div>}
    {event && <Facts value={event} />}
    {logout ? <div className="native-options"><Button variant="destructive" disabled={action.busy} onClick={() => void action.run(async () => { await native("account/logout", null); setLogout(false); setLogin(undefined); await refresh(); }, "账户已退出")}>确认退出运行实例账户</Button><Button variant="ghost" onClick={() => setLogout(false)}>取消</Button></div> : <Button variant="outline" onClick={() => setLogout(true)}>退出账户…</Button>}
    <details><summary>Bedrock 认证</summary><NativeOperation state={state} method="account/bedrock/discover" title="发现 AWS 配置" /><NativeOperation state={state} method="account/bedrock/setup" title="使用 AWS 配置登录" onResult={refresh} /><NativeOperation state={state} method="account/bedrock/checkGovCloudRequirements" title="检查 GovCloud 条件" /></details>
    <details><summary>Gateway OAuth</summary><Facts value={gateway.data} /><NativeOperation state={state} method="account/gatewayOAuth/login" title="登录 Gateway" onResult={async () => { await gateway.refresh(); await handoffs.refresh(); }} />
      {gatewayLink && /^https?:\/\//.test(gatewayLink) && <a href={gatewayLink} target="_blank" rel="noreferrer">打开 Gateway 登录页面 ↗</a>}
      <Button variant="outline" disabled={action.busy} onClick={() => void action.run(async () => { await native("account/gatewayOAuth/cancel", null); await gateway.refresh(); await handoffs.refresh(); }, "Gateway 登录已取消")}>取消 Gateway 登录</Button></details>
    <details><summary>用量与工作区信息</summary><NativeOperation state={state} method="account/usage/read" title="读取详细用量" /><NativeOperation state={state} method="account/workspaceMessages/read" title="读取工作区消息" /></details>
    <details><summary>重置额度</summary><p>使用账户可用的重置额度，会消耗一份额度。请求结果不明确时保留同一幂等标识重试。</p>
      <label className="native-checkbox"><input type="checkbox" checked={credit} onChange={(e) => setCredit(e.target.checked)} />确认消耗可用的重置额度</label>
      <Button disabled={!credit || action.busy} onClick={() => void action.run(async () => { const r = await native("account/rateLimitResetCredit/consume", { idempotencyKey: creditKey }, creditKey); setEvent(r); setCredit(false); setCreditKey(requestKey()); await refresh(); }, "重置请求已完成")}>使用重置额度</Button></details>
    <details><summary>充值提醒邮件</summary><NativeOperation state={state} method="account/sendAddCreditsNudgeEmail" title="向账户邮箱发送充值提醒" description="点击后由原生服务向当前账户发送邮件。" /></details>
    <Feedback error={action.error || account.error || gateway.error || handoffs.error} status={action.status} />
  </section>;
}
