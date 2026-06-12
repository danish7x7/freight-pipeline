import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Lazily created in the browser so static prerendering (build time, no env) doesn't
// instantiate the client. Reads the queue with the reviewer's JWT (RLS scopes rows);
// all WRITES go through the FastAPI backend.
let client: SupabaseClient | undefined;

export function getSupabase(): SupabaseClient {
  if (!client) {
    client = createClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL ?? "",
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
    );
  }
  return client;
}
