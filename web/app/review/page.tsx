"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import { Card } from "@/components/ui";
import { DemoPanel } from "@/components/DemoPanel";
import { type DealRow, dollars, lane, REVIEW_SELECT } from "@/lib/types";

export default function ReviewQueue() {
  const router = useRouter();
  const [deals, setDeals] = useState<DealRow[]>([]);
  const [email, setEmail] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadDeals = useCallback(async () => {
    // RLS scopes deals to the signed-in reviewer (or all, for admin).
    const { data, error } = await getSupabase()
      .from("deals")
      .select(REVIEW_SELECT)
      .eq("state", "quoted");
    // Surface query errors — a swallowed PGRST201 once looked like an empty queue.
    if (error) console.error("review queue query failed:", error);
    // Deal state stays 'quoted' after a send; the 'sent' sends row is the send signal
    // (Phase 5: review queue = 'quoted' deals with no completed send). Hide deals whose
    // quote has already been sent. A 'claimed' (stuck, RECOVERY.md §4) or 'failed' send
    // stays VISIBLE — those need a reviewer's eyes, not hiding.
    const pending = ((data as unknown as DealRow[] | null) ?? []).filter(
      (d) => !d.quotes.some((q) => q.sends?.status === "sent"),
    );
    setDeals(pending);
  }, []);

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
      await loadDeals();
      setLoading(false);
    })();
  }, [router, loadDeals]);

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

      <DemoPanel onLoaded={() => void loadDeals()} />

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
