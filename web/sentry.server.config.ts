// Server-side (Node runtime) Sentry init. Same minimal posture as the client config:
// fail-closed on an empty DSN, error capture only (no tracing/profiling), no PII.
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: 0,
  sendDefaultPii: false,
});
