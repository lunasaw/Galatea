export type FileMatch = {
  file_name: string;
  match_type: "file" | "directory";
  root: string;
  path: string;
  score: number;
  indices?: number[] | null;
};

type Params = Record<string, any>;
type Transport = {
  call: (method: string, params: Params) => Promise<unknown>;
  observe: (method: string, receive: (params: Params) => void, fail: (error: unknown) => void) => Promise<() => void>;
};

export function fileMatchPath(match: FileMatch) {
  return match.path.startsWith("/") ? match.path : `${match.root.replace(/\/+$/, "")}/${match.path}`;
}

/** A search owns its native session, including while start/update is still in flight. */
export class FileSearchSession {
  private closed = false;
  private startAttempted = false;
  private query = "";
  private stopObserving?: () => void;
  private ready: Promise<void>;
  private writes: Promise<void>;

  constructor(
    private transport: Transport,
    private sessionId: string,
    roots: string[],
    receive: (files: FileMatch[], query: string) => void,
    private fail: (error: unknown) => void,
  ) {
    this.ready = (async () => {
      this.stopObserving = await transport.observe("fuzzyFileSearch/sessionUpdated", (event) => {
        if (!this.closed && event.sessionId === sessionId && event.query === this.query)
          receive(event.files, event.query);
      }, (error) => { if (!this.closed) fail(error); });
      if (this.closed) { this.stopObserving(); return; }
      this.startAttempted = true;
      await transport.call("fuzzyFileSearch/sessionStart", { sessionId, roots });
    })();
    this.writes = this.ready.catch((error) => { if (!this.closed) fail(error); });
  }

  update(query: string) {
    this.query = query;
    this.writes = this.writes.then(async () => {
      await this.ready;
      if (!this.closed && query === this.query)
        await this.transport.call("fuzzyFileSearch/sessionUpdate", { sessionId: this.sessionId, query });
    }).catch((error) => { if (!this.closed) this.fail(error); });
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    this.stopObserving?.();
    // Start may have succeeded even if its response was lost. Stop is attempted
    // after it settles so a late start cannot leave a search running on the host.
    await this.writes;
    this.stopObserving?.();
    if (this.startAttempted)
      await this.transport.call("fuzzyFileSearch/sessionStop", { sessionId: this.sessionId });
  }
}
