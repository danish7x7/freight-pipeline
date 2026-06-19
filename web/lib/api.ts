import { getSupabase } from "./supabase";

// The backend is the ONLY thing that sends/rejects. Calls carry the Supabase JWT;
// the backend verifies it (who → audit actor; is-it-their-deal → authz).
//
// Fail loudly if the backend URL is unconfigured rather than silently defaulting to
// localhost: NEXT_PUBLIC_* is baked at build, so a missing var would otherwise ship a
// broken bundle that quietly calls localhost. Lazy (mirrors lib/supabase.ts) so it
// never throws during build/prerender — only at call time. Local dev sets it in
// web/.env.local.
function apiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!base) {
    throw new Error(
      "NEXT_PUBLIC_API_BASE_URL is not set — the console cannot reach the backend. " +
        "Set it (e.g. https://freight-pipeline.onrender.com) in Vercel env, or " +
        "http://localhost:8000 in web/.env.local for local dev.",
    );
  }
  return base;
}

async function authedPost(path: string, body: unknown): Promise<Record<string, string>> {
  const { data } = await getSupabase().auth.getSession();
  const token = data.session?.access_token ?? "";
  const res = await fetch(`${apiBase()}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(detail.detail ?? `request failed (${res.status})`);
  }
  return res.json() as Promise<Record<string, string>>;
}

export const sendQuote = (quoteId: string, body: string) =>
  authedPost("/review/send", { quote_id: quoteId, body });

export const rejectDeal = (dealId: string) =>
  authedPost("/review/reject", { deal_id: dealId });

// The showcase demo: seed a fixed sample and run the REAL validation gate + pricing on
// the backend. Returns the outcome (status / intent / review_reason / deal_state) so the
// panel can show what happened. The backend 404s unless DEMO_ENABLED is set.
export type DemoOutcome = {
  sample: "clean" | "injection";
  status: string;
  intent: string | null;
  review_reason: string | null;
  deal_id: string | null;
  deal_state: string | null;
  blurb: string;
};

export async function loadDemoSample(
  sample: "clean" | "injection",
): Promise<DemoOutcome> {
  const res = await authedPost("/demo/sample", { sample });
  return res as unknown as DemoOutcome;
}
