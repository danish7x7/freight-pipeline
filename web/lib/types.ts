// Shapes returned by the Supabase queries (nested selects come back as arrays).
export type SendStatus = "claimed" | "sent" | "failed";

export interface SendRow {
  status: SendStatus;
}

export interface QuoteRow {
  id: string;
  amount_cents: number;
  currency: string;
  is_computed: boolean;
  // sends.quote_id is UNIQUE, so PostgREST embeds this one-to-ONE: an object when the
  // quote has a send, null when it doesn't (NOT an array). 'sent' = the reply went out.
  sends: SendRow | null;
}

export interface EmailRow {
  sender: string;
  subject: string | null;
  body: string | null;
  confidence: number | null;
}

export interface DealRow {
  id: string;
  origin_city: string | null;
  origin_state: string | null;
  dest_city: string | null;
  dest_state: string | null;
  equipment: string | null;
  is_demo: boolean;
  quotes: QuoteRow[];
  email_messages: EmailRow[];
}

// Single source of truth for the review SELECT/embed, shared by the queue and detail
// pages so the two copies can't drift (the drift risk flagged in the PGRST201 fix).
//
// `deals` has TWO FK paths to `quotes` (quotes.deal_id and deals.accepted_quote_id), so
// the embed must name the constraint or PostgREST refuses it (PGRST201, 300 Multiple
// Choices). We want the deal's quotes via quotes.deal_id. `sends` nests under the quote
// via the single sends.quote_id FK (unambiguous), and `email_messages` has one FK — both
// need no hint. Constraint names verified against the live schema (pg_constraint).
export const REVIEW_SELECT =
  "id, origin_city, origin_state, dest_city, dest_state, equipment, is_demo," +
  " quotes!quotes_deal_id_fkey(id, amount_cents, currency, is_computed, sends(status))," +
  " email_messages(sender, subject, body, confidence)";

export function lane(d: DealRow): string {
  return `${d.origin_city ?? "?"}, ${d.origin_state ?? "?"} → ${d.dest_city ?? "?"}, ${d.dest_state ?? "?"}`;
}

export function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(0)}`;
}
