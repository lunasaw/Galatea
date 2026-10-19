// Source: assistant-ui/assistant-ui @ 2bdd534a3d8da45e6c7a9ee6553862b83713059d (MIT). See UPSTREAM.md.
"use client";

import { useEffect, useRef, useState } from "react";
import { copyText } from "@/clipboard";

export type UseCopyToClipboardOptions = {
  copiedDuration?: number;
};

export const useCopyToClipboard = ({
  copiedDuration = 3000,
}: UseCopyToClipboardOptions = {}) => {
  const [isCopied, setIsCopied] = useState<boolean>(false);
  const [copyError, setCopyError] = useState("");
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const scopeGenerationRef = useRef(0);

  useEffect(
    () => () => {
      scopeGenerationRef.current += 1;
      if (copiedTimerRef.current === undefined) return;

      clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = undefined;
      setIsCopied(false);
    },
    [],
  );

  const copyToClipboard = (value: string) => {
    if (!value || typeof navigator === "undefined") {
      return;
    }

    setCopyError("");
    setIsCopied(false);
    if (copiedTimerRef.current !== undefined) {
      clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = undefined;
    }
    const scopeGeneration = ++scopeGenerationRef.current;
    copyText(value).then(
      () => {
        if (scopeGeneration !== scopeGenerationRef.current) return;

        if (copiedTimerRef.current !== undefined) {
          clearTimeout(copiedTimerRef.current);
        }
        setIsCopied(true);
        copiedTimerRef.current = setTimeout(() => {
          copiedTimerRef.current = undefined;
          setIsCopied(false);
        }, copiedDuration);
      },
      () => {
        if (scopeGeneration === scopeGenerationRef.current) setCopyError("复制失败，请重试");
      },
    );
  };

  return { isCopied, copyError, copyToClipboard };
};
