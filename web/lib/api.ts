import { getSupabase } from "./supabase";

// The backend is the ONLY thing that sends/rejects. Calls carry the Supabase JWT;
// the backend verifies it (who → audit actor; is-it-their-deal → authz).
const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function authedPost(path: string, body: unknown): Promise<Record<string, string>> {
  const { data } = await getSupabase().auth.getSession();
  const token = data.session?.access_token ?? "";
  const res = await fetch(`${API_BASE}${path}`, {
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
