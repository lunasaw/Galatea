// Source: assistant-ui/assistant-ui @ 2bdd534a3d8da45e6c7a9ee6553862b83713059d (MIT). See UPSTREAM.md.
import * as React from "react";

const MOBILE_BREAKPOINT = 768;

const subscribe = (onStoreChange: () => void) => {
  const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`);
  mql.addEventListener("change", onStoreChange);
  return () => mql.removeEventListener("change", onStoreChange);
};

const getSnapshot = () => window.innerWidth < MOBILE_BREAKPOINT;

const getServerSnapshot = () => false;

export function useIsMobile() {
  return React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
