type GoalResult = { goal: any };

/** A disabled capability is stable for this thread/revision, not a retryable error. */
export function goalReader(read: () => Promise<GoalResult>) {
  let disabled = false;
  return async (): Promise<GoalResult> => {
    if (disabled) return { goal: null };
    try { return await read(); }
    catch (error) {
      if (!(error instanceof Error) || !/goals feature is disabled/i.test(error.message)) throw error;
      disabled = true;
      return { goal: null };
    }
  };
}
