import { useAui, useAuiState } from "@assistant-ui/react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { TooltipIconButton } from "./components/assistant-ui/elements/tooltip-icon-button";
import { useCopyToClipboard } from "./hooks/use-copy-to-clipboard";

export function MessageCopyButton({ label = "复制回复", className }: { label?: string; className?: string }) {
  const messageId = useAuiState(s => s.message.id);
  return <CopyButton key={messageId} label={label} className={className} />;
}

function CopyButton({ label, className }: { label: string; className?: string }) {
  const aui = useAui();
  const { isCopied, copyError, copyToClipboard } = useCopyToClipboard();
  return (
    <>
      <TooltipIconButton
        tooltip={copyError || (isCopied ? "已复制" : label)}
        aria-label={label}
        data-copied={isCopied || undefined}
        className={className}
        onClick={() => {
          // Read the clicked message at click time, including its final streamed text.
          const composer = aui.composer.getState();
          copyToClipboard(composer.isEditing ? composer.text : aui.message.getCopyText());
        }}
      >
        {isCopied ? <CheckIcon /> : <CopyIcon />}
      </TooltipIconButton>
      {copyError && <span role="status" className="text-xs text-destructive">{copyError}</span>}
    </>
  );
}
