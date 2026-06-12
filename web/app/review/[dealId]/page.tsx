"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import { Button, Card } from "@/components/ui";
import { rejectDeal, sendQuote } from "@/lib/api";
import { type DealRow, dollars, lane } from "@/lib/types";

const SELECT =
  "id, origin_city, origin_state, dest_city, dest_state, equipment," +
  " quotes(id, amount_cents, currency, is_computed)," +
  " email_messages(sender, subject, body, confidence)";

export default function DraftDetail({ params }: { params: { dealId: string } }) {
  const router = useRouter();
  const [deal, setDeal] = useState<DealRow | null>(null);
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      const {
        data: { session },
      } = await getSupabase().auth.getSession();
      if (!session) {
        router.push("/login");
        return;
      }
      const { data } = await getSupabase()
        .from("deals")
        .select(SELECT)
        .eq("id", params.dealId)
        .single();
      const row = data as unknown as DealRow | null;
      if (row) {
        setDeal(row);
        const quote = row.quotes[0];
        setReply(
          `Thank you for your enquiry. Our quote for ${lane(row)} (${row.equipment ?? "—"}) ` +
            `is ${quote ? dollars(quote.amount_cents) : "—"} ${quote?.currency ?? ""}.`,
        );
      }
    })();
  }, [params.dealId, router]);

  async function onSend() {
    const quote = deal?.quotes[0];
    if (!quote) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await sendQuote(quote.id, reply);
      setMessage(`Sent — Gmail id ${result.gmail_message_id}.`);
    } catch (err) {
      setMessage(`Error: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function onReject() {
    if (!deal) return;
    setBusy(true);
    setMessage(null);
    try {
      await rejectDeal(deal.id);
      router.push("/review");
    } catch (err) {
      setMessage(`Error: ${(err as Error).message}`);
      setBusy(false);
    }
  }

  if (!deal) {
    return <main className="p-6 text-gray-500">Loading…</main>;
  }
  const quote = deal.quotes[0];
  const original = deal.email_messages[0];

  return (
    <main className="mx-auto max-w-2xl space-y-4 p-6">
      <button onClick={() => router.push("/review")} className="text-sm underline">
        ← back to queue
      </button>
      <h1 className="text-xl font-semibold">{lane(deal)}</h1>

      <Card>
        <div className="text-sm text-gray-500">Proposed quote</div>
        {quote ? (
          <div className="mt-1 text-lg font-semibold">
            {dollars(quote.amount_cents)} {quote.currency}
            {quote.is_computed && (
              <span className="ml-2 text-xs text-amber-600">
                computed (no contracted rate)
              </span>
            )}
          </div>
        ) : (
          <div className="mt-1 text-gray-500">No quote.</div>
        )}
        {original?.confidence != null && (
          <div className="mt-1 text-sm text-gray-500">
            extraction confidence {(original.confidence * 100).toFixed(0)}%
          </div>
        )}
      </Card>

      {original && (
        <Card>
          <div className="text-sm text-gray-500">Original enquiry</div>
          <div className="mt-1 text-sm">
            <span className="font-medium">{original.sender}</span> — {original.subject}
          </div>
          <p className="mt-2 whitespace-pre-wrap text-sm text-gray-700">
            {original.body}
          </p>
        </Card>
      )}

      <Card>
        <label className="text-sm font-medium">Reply (editable)</label>
        <textarea
          value={reply}
          onChange={(e) => setReply(e.target.value)}
          rows={5}
          className="mt-2 w-full rounded-md border border-gray-300 p-2 text-sm"
        />
        <div className="mt-3 flex gap-2">
          <Button onClick={onSend} disabled={busy || !quote}>
            {busy ? "Working…" : "Approve & Send"}
          </Button>
          <Button variant="danger" onClick={onReject} disabled={busy}>
            Reject
          </Button>
        </div>
        {message && <p className="mt-3 text-sm text-gray-700">{message}</p>}
      </Card>
    </main>
  );
}
