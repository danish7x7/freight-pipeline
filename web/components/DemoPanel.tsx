"use client";

import { useState } from "react";
import { Button, Card } from "@/components/ui";
import { loadDemoSample, type DemoOutcome } from "@/lib/api";

// The "load sample order" showcase control. Renders only when NEXT_PUBLIC_DEMO_ENABLED
// is "true" (the backend is the authoritative gate — it 404s otherwise).
//
// HONESTY: the banner states plainly what is REAL (the validation gate, pricing,
// finalize, RLS, and the human-approval send gate) vs RECORDED (the model's extraction
// step), and that it SEEDS a sample email — it does not imply a real inbound or that the
// signature transport ran.
export function DemoPanel({ onLoaded }: { onLoaded: () => void }) {
  const [busy, setBusy] = useState<"clean" | "injection" | null>(null);
  const [result, setResult] = useState<DemoOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (process.env.NEXT_PUBLIC_DEMO_ENABLED !== "true") return null;

  async function run(sample: "clean" | "injection") {
    setBusy(sample);
    setError(null);
    setResult(null);
    try {
      const outcome = await loadDemoSample(sample);
      setResult(outcome);
      onLoaded(); // refresh the queue so a clean order's draft appears
    } catch (e) {
      setError(e instanceof Error ? e.message : "demo request failed");
    } finally {
      setBusy(null);
    }
  }

  const contained = result?.status === "needs_review";

  return (
    <Card className="mb-6 border-dashed">
      <h2 className="text-sm font-semibold">Try the pipeline</h2>
      <p className="mt-1 text-xs text-gray-500">
        Seeds a sample email and runs it through the <strong>live</strong> validation
        gate, pricing, finalize, and RLS on this deploy. The model&apos;s extraction step
        is a <strong>recorded</strong> sample (not a live model call). A human still
        approves every send — the demo only ever produces a draft.
      </p>

      <div className="mt-3 flex gap-2">
        <Button
          variant="primary"
          disabled={busy !== null}
          onClick={() => run("clean")}
        >
          {busy === "clean" ? "Loading…" : "Load a clean order"}
        </Button>
        <Button
          variant="ghost"
          disabled={busy !== null}
          onClick={() => run("injection")}
        >
          {busy === "injection" ? "Loading…" : "Load an injection attempt"}
        </Button>
      </div>

      {error && <p className="mt-3 text-xs text-red-600">{error}</p>}

      {result && (
        <div
          className={`mt-3 rounded-md p-3 text-xs ${
            contained
              ? "bg-amber-50 text-amber-800"
              : "bg-green-50 text-green-800"
          }`}
        >
          <p className="font-medium">
            {contained
              ? "🛡 Contained by the validation gate → routed to review."
              : "✓ Extracted, validated, and priced → draft in the queue below."}
          </p>
          <p className="mt-1">{result.blurb}</p>
          <p className="mt-1 text-[11px] opacity-80">
            outcome: <code>{result.status}</code>
            {result.review_reason ? (
              <>
                {" "}
                · gate reason: <code>{result.review_reason}</code>
              </>
            ) : null}
            {result.deal_state ? (
              <>
                {" "}
                · deal: <code>{result.deal_state}</code>
              </>
            ) : null}
          </p>
        </div>
      )}
    </Card>
  );
}
