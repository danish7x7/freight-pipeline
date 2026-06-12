// Shapes returned by the Supabase queries (nested selects come back as arrays).
export interface QuoteRow {
  id: string;
  amount_cents: number;
  currency: string;
  is_computed: boolean;
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
  quotes: QuoteRow[];
  email_messages: EmailRow[];
}

export function lane(d: DealRow): string {
  return `${d.origin_city ?? "?"}, ${d.origin_state ?? "?"} → ${d.dest_city ?? "?"}, ${d.dest_state ?? "?"}`;
}

export function dollars(cents: number): string {
  return `$${(cents / 100).toFixed(0)}`;
}
