export const STOPS = [0, 0.17, 0.34, 0.49, 0.74, 1];

export const STAGES = [
  {
    label: "聊天入口",
    owner: "Web 工作台 · 通用 Host",
    title: "你说目标，Host 把请求交给原生 Codex。",
    body: "网页处理项目、聊天、文件、终端和审批界面。Python Host 托管原生 codex app-server，通过 stdio JSON-RPC 传递请求，再把事件投影到浏览器。",
    fact: "Thread / Turn 是对话生命周期；它们本身不是训练授权。",
  },
  {
    label: "理解与行动",
    owner: "原生 Codex Runtime",
    title: "真正决定下一步的是 Codex。",
    body: "模型调用、Agent Loop、Thread/Turn、Skill、内建工具、沙箱与原生审批在 Codex Runtime 中。Skill 提供工作方法；文件/Shell 工具、MCP 和可选 Python Provider 提供执行能力。",
    fact: "仓库中的 agents/codex-app-server 是 Host；它启动的 codex app-server 子进程才是 Agent Runtime。",
  },
  {
    label: "记录与展示",
    owner: "Task Service · Galatea App",
    title: "任务事实保存下来，面板据此展示。",
    body: "Agent 调用 galatea_task.start_task 建立当前聊天的 Task，再按实际需要组织动态阶段。Provider 回执带 app_view，Host 自动打开任务 App；面板经 MCP AppBridge 读取快照，显示阶段、指标与产物。",
    fact: "Task 与当前可信会话绑定；刷新恢复视图不代表重新执行。讲解中的六站不是固定业务阶段。",
  },
  {
    label: "执行条件",
    owner: "任务方案 · 治理入口",
    title: "训练要把方案、资源与授权逐项对齐。",
    body: "具体训练方案确认与匹配的治理授权都需要满足。Release、数据与 split 身份、readiness、执行绑定、预算和有效期由相应入口校验。下面的开关只演示这一关系。",
    fact: "Codex 完全访问权限 ≠ GPU 预算或训练授权。App 中的授权操作也只能使用已有的委派范围。",
  },
  {
    label: "执行与观察",
    owner: "Ray · MLflow · 外部 Worker",
    title: "Ray 执行训练，Worker 观察并请求接续。",
    body: "当前项目通过 job/governed.mjs 和独立治理库提交固定的 Ray 入口。MLflow 保存 Run、指标与产物引用，MinIO/S3 承载对象。Task Worker 对账外部操作，必要时通过 Host 的通用接口请求原会话继续。",
    fact: "Worker 不调用模型；后续决策仍回到 Codex。Host 本身不解释训练阶段或轮询 Ray Job。",
  },
  {
    label: "核验与交付",
    owner: "Task Service · Codex · App",
    title: "服务核验证据，Codex 再解释结果。",
    body: "report_result 上报的是产物候选；服务检查实际产物与阶段验收规则。complete_task 只有满足必要阶段和交付要求才能封存。App 显示可核验结果，Codex 在原聊天总结并回应后续要求。",
    fact: "回复结束 ≠ 任务完成；Ray Job 成功 ≠ 质量门槛通过；模型推广仍是独立动作。",
  },
];

export function allowedStage(requested, mode, confirmed, governed) {
  const index = Math.max(0, Math.min(5, Math.round(requested)));
  return mode === "training" && !(confirmed && governed) ? Math.min(index, 3) : index;
}

export function stageContent(index, mode) {
  const result = { ...STAGES[index] };
  if (mode === "data" && index === 3) {
    Object.assign(result, {
      owner: "原生权限 · 当前任务范围",
      title: "普通数据检查使用已获授权的工具范围。",
      body: "例如检查 CSV 缺失值与重复行，再生成报告：Codex 可以通过本地工具完成，Task Service 记录阶段与产物。这个场景没有优化器更新，因此不需要训练方案或 GPU 预算。",
      fact: "Task App 同时支持数据处理和训练；并非所有任务都经过 Ray。",
    });
  }
  if (mode === "data" && index === 4) {
    Object.assign(result, {
      owner: "Codex 原生工具 · Task Worker",
      title: "原生工具做检查，服务校验交付。",
      body: "Codex 在当前工作区运行检查、写出报告，再把产物候选交给 Task Service。Worker 执行验证、对账与必要的接续投递；它本身不具备模型客户端。",
      fact: "这是普通 CSV 需求已验收的路径；无需把它包装成一次训练。",
    });
  }
  return result;
}
