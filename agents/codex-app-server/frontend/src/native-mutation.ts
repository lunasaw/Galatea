import { native, type Obj } from "./native";
import { ApiError, requestKey } from "./store";

/** Keep the exact request identity after an uncertain HTTP response. */
export class NativeMutation {
  private pending?: { identity: string; method: string; params: Obj; key: string };
  async run(method: string, params: Obj, identity = JSON.stringify([method, params])) {
    if (!this.pending || this.pending.identity !== identity)
      this.pending = { identity, method, params: structuredClone(params), key: requestKey() };
    const request = this.pending;
    try {
      const result = await native(request.method, request.params, request.key);
      if (this.pending === request) this.pending = undefined;
      return result;
    } catch (error) {
      if (error instanceof ApiError && [400, 409, 422].includes(error.status) && this.pending === request)
        this.pending = undefined;
      throw error;
    }
  }
}

// Keep uncertain operations retryable after their panel unmounts.
const mutations = new Map<string, NativeMutation>();
export function sharedMutation(scope: string) {
  if (!mutations.has(scope)) mutations.set(scope, new NativeMutation());
  return mutations.get(scope)!;
}
