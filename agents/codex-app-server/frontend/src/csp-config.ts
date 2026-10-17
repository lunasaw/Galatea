import { config } from "zod/v4";

// The console's script-src policy forbids Zod's optional eval-based compiler.
// Configure it before modules create their schemas, avoiding the CSP probe.
config({ jitless: true });
