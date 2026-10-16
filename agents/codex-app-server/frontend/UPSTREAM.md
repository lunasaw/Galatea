# UI component provenance

The Console uses the official assistant-ui styled components rather than recreating
chat UI with primitives. The official distribution model copies editable component
source into the application (the same model as the assistant-ui/shadcn CLI).

Source: https://github.com/assistant-ui/assistant-ui
Commit: `2bdd534a3d8da45e6c7a9ee6553862b83713059d`
License: MIT, preserved in `licenses/assistant-ui-components.txt` and bundled notices.
`upstream-components.json` records each original path and Git blob for updates.

## Reused components

- `apps/docs/components/pages/examples/chatgpt.tsx`: centered welcome and
  768px content area, inline rounded composer, contrasting user bubbles,
  below-answer action bar, and conversation footer. Adapted into `Thread`
  alongside its grouped tools/reasoning and capability guards, using the same
  pinned commit and MIT license. `adapted_examples` records its original blob.
- `Thread`: welcome, suggestions, message layout, composer, send/cancel,
  scroll-to-bottom, action bar, Markdown export, loading and error displays.
- `MarkdownText`: GFM, code headers, clipboard lifecycle and Markdown styles;
  syntax highlighting uses `@assistant-ui/react-syntax-highlighter` with a local
  registered language set. No remote language loading.
- `ToolGroup`, `ToolFallback`: grouping, disclosure, scroll locking, arguments,
  results and error presentation.
- `ThreadList`, `ThreadListSidebar`: search, selection, new chat, rename UI,
  responsive sidebar and keyboard interaction.
- Official Radix/shadcn dependencies: Button, Input, Tooltip, Sidebar, Sheet,
  Tabs, DropdownMenu, Skeleton, Separator and supporting components/hooks.
- The registry's Tailwind theme and animation styles replace the previous custom
  chat CSS. Supporting attachment/media/reasoning components remain upstream code;
  controls only appear when the runtime has the corresponding capability.

## Local adaptations

- Chinese labels, Galatea branding, local workspace footer, stable input IDs.
- Runtime capability guards hide attachment, edit and reload actions unsupported
  by this Host. Archive/delete are omitted because this Host has neither API;
  closing a Codex session is a separate Host action. Rename persists the existing
  browser-local title, without claiming to rename backend sessions.
- Welcome suggestions populate the composer without sending and disappear after
  the conversation begins. Composer actions remain aligned right when attachments
  are unavailable.
- Public reasoning summaries become native `reasoning` message parts, rendered
  through official `ReasoningRoot/Trigger/Content/Text`: streaming preview,
  automatic collapse, manual disclosure and observed duration. Chinese state
  labels distinguish running/interrupted summaries. Only observed start/end
  timestamps produce a duration; legacy snapshots do not invent elapsed time.
- User messages have a copy action; answer copy/export remains below the text.
  Tool-only and reasoning-only rows have no empty answer toolbar. Unsupported
  feedback, voice, share, attachment and regenerate controls are omitted.
- Sidebar search, thread controls and Host inspection panels keep the existing
  runtime adapter; clone demo-only shell controls are not imported as a second
  independent sidebar state.
- `tool-ui.tsx` composes the official ToolFallback parts to preserve Host-specific
  running/failed/unknown observations and job metadata. An observed call without
  a receipt is never labeled a success. `ToolFallback.Trigger` accepts a label
  override for this state.
- `main.tsx` adapts the existing ConsoleStore to ExternalStoreRuntime and its
  thread-list adapter. Host fixed-watermark history pages replace the displayed
  page instead of prepending; existing history/latest controls are retained rather
  than binding them to the different `LoadEarlier` contract.
- `inspector.tsx` retains Host capability and native-event information, displayed
  in shared Sheet/Tabs components. This is the only domain-specific detail UI.
- Markdown images stay text-only because the Host has no attachment/image endpoint.
- Shared shadcn imports are consolidated under `components/ui/`.

## CSP compatibility

The existing `style-src 'self'` policy is unchanged. Radix uses
`react-style-singleton` to manage modal scrollbar styles. A guarded build transform
uses constructable CSSStyleSheet/adoptedStyleSheets instead of an inline `<style>`;
reference counts, generated styles and cleanup remain library-owned. A changed
upstream implementation fails the build and requires review. This requires a
modern browser with constructable stylesheet support (the rest of the UI already
uses modern CSS). The browser acceptance verifies focus trapping, body scroll lock,
mobile sheet dismissal and absence of CSP violations.

## Updating

Update the npm lockfile and the pinned upstream source together. Review the local
adaptations above, retain upstream licenses, then run build, frontend/Console tests
and `test:browser` with the synthetic Host fixture. Do not copy modified local
files over upstream baselines or report unchanged inherited runtime checks as new
acceptance. `build.mjs` ships complete notices with the compiled application.

## Chat interaction update (2026-10-16)

The shell now uses the upstream Dialog for on-demand settings. The sidebar keeps
conversation actions, history and Skills; diagnostics remain available inside settings.
`chat-controls.tsx` binds composer model, reasoning effort and plan mode to native
thread settings, adds a keyboard command menu and sends steering through the same
composer. `Thread` accepts an interactions slot inside its scrolling transcript.
Pending native requests are scoped to their owning session; unrelated requests use
a separate notification dialog. Account and OAuth login controls are omitted.
The build retains the upstream runtime, message grouping, attachment handling,
Markdown, focus management and CSP adaptation.
