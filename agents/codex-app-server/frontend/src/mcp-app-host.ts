import type { McpUiHostCapabilities, McpUiHostContext } from "@modelcontextprotocol/ext-apps/app-bridge";
import type { Obj } from "./native";

// Advertise only modalities and browser permissions implemented by this Host.
export const appCapabilities: McpUiHostCapabilities = {
  openLinks: {}, downloadFile: {}, serverTools: {}, serverResources: {}, logging: {},
  sandbox: { permissions: {} }, message: { text: {}, image: {}, audio: {} },
  updateModelContext: { text: {}, structuredContent: {} },
};
export function appToolAllowed(tool: Obj | undefined) {
  const visibility = tool?._meta?.ui?.visibility;
  return !!tool && (visibility === undefined || Array.isArray(visibility) && visibility.includes("app"));
}
export function appHostContext(): McpUiHostContext {
  const css = getComputedStyle(document.documentElement);
  return {
    theme: document.documentElement.classList.contains("dark") ? "dark" : "light",
    locale: navigator.language, timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    platform: "web", userAgent: "Codex Web/0.1.0", availableDisplayModes: ["inline", "fullscreen", "pip"],
    deviceCapabilities: { touch: navigator.maxTouchPoints > 0, hover: matchMedia("(hover: hover)").matches },
    styles: { variables: {
      "--color-background-primary": css.getPropertyValue("--background").trim(),
      "--color-text-primary": css.getPropertyValue("--foreground").trim(),
      "--color-border-primary": css.getPropertyValue("--border").trim(),
      "--font-sans": css.fontFamily,
    } as NonNullable<McpUiHostContext["styles"]>["variables"] },
  };
}
export function downloadResource(resource: Obj, name?: string): { blob: Blob; name: string } {
  let bytes: Uint8Array<ArrayBuffer>;
  if (resource.text?.length > 8 * 1024 * 1024 || resource.blob?.length > Math.ceil(8 * 1024 * 1024 / 3) * 4)
    throw new Error("下载文件超过 8 MiB");
  if (typeof resource.text === "string") bytes = new TextEncoder().encode(resource.text);
  else if (typeof resource.blob === "string") bytes = Uint8Array.from(atob(resource.blob), c => c.charCodeAt(0));
  else throw new Error("下载资源缺少内容");
  if (bytes.byteLength > 8 * 1024 * 1024) throw new Error("下载文件超过 8 MiB");
  const filename = (name || String(resource.uri || "download").split(/[/?#]/).filter(Boolean).at(-1) || "download")
    .replace(/[\\/:*?"<>|\u0000-\u001f\u007f]/g, "_").slice(0, 180);
  return { blob: new Blob([bytes], { type: resource.mimeType || "application/octet-stream" }), name: filename };
}
