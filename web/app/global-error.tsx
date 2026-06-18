"use client";

// App-Router global error boundary: report uncaught render errors to Sentry, then
// show a minimal fallback. Fail-closed inherits from the client config (no DSN => the
// SDK no-ops, so captureException is a silent no-op).
import * as Sentry from "@sentry/nextjs";
import { useEffect } from "react";

export default function GlobalError({
  error,
}: {
  error: Error & { digest?: string };
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <html lang="en">
      <body>
        <h2>Something went wrong.</h2>
      </body>
    </html>
  );
}
