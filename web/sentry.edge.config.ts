// Edge runtime Sentry init (loaded by instrumentation.ts when NEXT_RUNTIME === "edge").
// Same minimal posture: fail-closed on an empty DSN, error capture only, no PII.
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: 0,
  sendDefaultPii: false,
});
