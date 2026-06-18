import { withSentryConfig } from "@sentry/nextjs";

/** @type {import('next').NextConfig} */
const nextConfig = {};

// Minimal Sentry wrapper: it injects the client/server instrumentation only. No
// authToken => NO sourcemap upload (minified traces are acceptable for this showcase),
// no tunneling. silent keeps the build output clean when no DSN/token is configured.
export default withSentryConfig(nextConfig, {
  silent: true,
  sourcemaps: { disable: true },
});
