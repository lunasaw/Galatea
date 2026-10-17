import { App, applyDocumentTheme } from "@modelcontextprotocol/ext-apps";

const app = new App({ name: "Hello World", version: "1.0.0" }, {});
const nameInput = document.querySelector("#name");
const greeting = document.querySelector("#greeting");
const status = document.querySelector("#status");
const draw = document.querySelector("#draw");
let connected = false;

function render(name = "World") {
  const recipient = name.trim().slice(0, 40) || "World";
  const message = `Hello, ${recipient}!`;
  greeting.textContent = message;
  greeting.setAttribute("font-size", "58");
  const width = greeting.getComputedTextLength();
  if (width > 640) greeting.setAttribute("font-size", String(58 * 640 / width));
  document.querySelector("#art-title").textContent = message;
  nameInput.value = recipient;
}

function applyResult(result) {
  if (result.isError) throw new Error(result.content?.find(part => part.type === "text")?.text || "绘制失败");
  if (typeof result.structuredContent?.name === "string") render(result.structuredContent.name);
  status.textContent = "已绘制 · 试试换一个名字";
}

app.ontoolinput = ({ arguments: args }) => {
  if (typeof args?.name === "string") render(args.name);
};
app.ontoolresult = result => {
  try { applyResult(result); } catch (error) { status.textContent = error.message; }
};
app.ontoolcancelled = () => { status.textContent = "绘制已取消"; };
app.onhostcontextchanged = context => {
  if (context.theme) applyDocumentTheme(context.theme);
};
app.onteardown = async () => ({});

draw.addEventListener("click", async () => {
  const name = nameInput.value.trim() || "World";
  if (!connected) {
    render(name);
    status.textContent = "本地预览 · 已绘制";
    return;
  }
  draw.disabled = true;
  try {
    applyResult(await app.callServerTool({ name: "draw_hello_world", arguments: { name } }));
  } catch (error) {
    status.textContent = `绘制失败：${error.message}`;
  } finally {
    draw.disabled = false;
  }
});
nameInput.addEventListener("keydown", event => {
  if (event.key === "Enter" && !event.isComposing) {
    event.preventDefault();
    draw.click();
  }
});

if (window.parent === window) {
  status.textContent = "本地预览 · 输入名字，送出一句问候";
} else {
  draw.disabled = true;
  app.connect().then(() => {
    connected = true;
    draw.disabled = false;
    const context = app.getHostContext();
    if (context?.theme) applyDocumentTheme(context.theme);
    if (status.textContent === "正在连接…") status.textContent = "已连接 · 输入名字，送出一句问候";
  }).catch(error => { status.textContent = `连接失败：${error.message}`; });
}
