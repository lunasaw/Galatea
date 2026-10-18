import { native, type Obj } from "./native";

export async function readAccountUsage() {
  const { account } = await native("account/read", { refreshToken: false });
  // Rate limits are a ChatGPT account capability, not an API-key capability.
  if (!account || account.type === "apiKey") return { account, limits: null, errors: [] as string[] };
  try { return { account, limits: await native("account/rateLimits/read", {}), errors: [] as string[] }; }
  catch (error) { return { account, limits: null, errors: [String(error)] }; }
}
export function rateLimitBuckets(limits: Obj | null | undefined): Obj[] {
  return limits?.rateLimitsByLimitId ? Object.values(limits.rateLimitsByLimitId) : limits?.rateLimits ? [limits.rateLimits] : [];
}
