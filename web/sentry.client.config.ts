// Browser-side Sentry init. DSN from NEXT_PUBLIC_SENTRY_DSN (inlined at build time).
// Fail-closed: an empty/unset DSN disables the SDK (Sentry.init no-ops), so local dev
// and any environment without the env var run silent. Error capture only —
// tracesSampleRate: 0 (no performance/tracing), no profiling, no PII.
import * as Sentry from "@sentry/nextjs";

Sentry.init({
  dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
  tracesSampleRate: 0,
  sendDefaultPii: false,
});
