"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import { Card } from "@/components/ui";
import { type DealRow, dollars, lane } from "@/lib/types";

const SELECT =
  "id, origin_city, origin_state, dest_city, dest_state, equipment," +
  " quotes(id, amount_cents, currency, is_computed)," +
  " email_messages(sender, subject, body, confidence)";

export default function ReviewQueue() {
  const router = useRouter();
  const [deals, setDeals] = useState<DealRow[]>([]);
  const [email, setEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      const {
        data: { session },
      } = await getSupabase().auth.getSession();
      if (!session) {
        router.push("/login");
        return;
      }
      setEmail(session.user.email ?? null);
      // RLS scopes deals to the signed-in reviewer (or all, for admin).
      const { data } = await getSupabase().from("deals").select(SELECT).eq("state", "quoted");
      setDeals((data as unknown as DealRow[] | null) ?? []);
      setLoading(false);
    })();
  }, [router]);

  async function signOut() {
    await getSupabase().auth.signOut();
    router.push("/login");
  }

  return (
    <main className="mx-auto max-w-3xl p-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold">Review queue</h1>
        <div className="text-sm text-gray-500">
          {email}
          <button onClick={signOut} className="ml-3 underline">
            sign out
          </button>
        </div>
      </header>

      {loading ? (
        <p className="text-gray-500">Loading…</p>
      ) : deals.length === 0 ? (
        <p className="text-gray-500">No drafts awaiting review.</p>
      ) : (
        <ul className="space-y-3">
          {deals.map((d) => {
            const quote = d.quotes[0];
            const message = d.email_messages[0];
            return (
              <li key={d.id}>
                <Link href={`/review/${d.id}`}>
                  <Card className="transition hover:border-gray-400">
                    <div className="flex items-start justify-between">
                      <div>
                        <div className="font-medium">{lane(d)}</div>
                        <div className="text-sm text-gray-500">
                          {d.equipment ?? "—"}
                          {message ? ` · from ${message.sender}` : ""}
                        </div>
                      </div>
                      <div className="text-right">
                        {quote && (
                          <div className="font-semibold">
                            {dollars(quote.amount_cents)} {quote.currency}
                            {quote.is_computed && (
                              <span className="ml-1 text-xs text-amber-600">
                                computed
                              </span>
                            )}
                          </div>
                        )}
                        {message?.confidence != null && (
                          <div className="text-xs text-gray-500">
                            confidence {(message.confidence * 100).toFixed(0)}%
                          </div>
                        )}
                      </div>
                    </div>
                  </Card>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
