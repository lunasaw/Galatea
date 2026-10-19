import {
  AppBridge,
  PostMessageTransport,
} from "@modelcontextprotocol/ext-apps/app-bridge";
const frame = document.querySelector("iframe")!;
const bridge = new AppBridge(
  null,
  { name: "Galatea evidence viewer", version: "1.0.0" },
  {
    serverTools: {},
    serverResources: {},
    message: { text: {} },
    updateModelContext: { text: {}, structuredContent: {} },
  } as any,
  {
    hostContext: {
      theme: matchMedia("(prefers-color-scheme:dark)").matches
        ? "dark"
        : "light",
      displayMode: "fullscreen",
      availableDisplayModes: ["inline", "fullscreen"],
      locale: "zh-CN",
      timeZone: "Asia/Shanghai",
    },
  },
);
bridge.oncalltool = async ({ name, arguments: args }) => {
  const response = await fetch("/preview/tool", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, arguments: args }),
  });
  const result = await response.json();
  return result;
};
bridge.onmessage = async () => ({ isError: true });
bridge.onupdatemodelcontext = async () => ({});
bridge.onrequestdisplaymode = async () => ({ mode: "fullscreen" });
bridge.oninitialized = () => {
  const taskId = new URL(location.href).searchParams.get("task_id");
  if (taskId) void bridge.sendToolInput({ arguments: { task_id: taskId } });
};
await bridge.connect(
  new PostMessageTransport(frame.contentWindow!, frame.contentWindow!),
);
frame.src = "/preview/app";
