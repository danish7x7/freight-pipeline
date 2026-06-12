"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { getSupabase } from "@/lib/supabase";
import { Button, Card } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("reviewer1@freight.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const { error: authError } = await getSupabase().auth.signInWithPassword({
      email,
      password,
    });
    setBusy(false);
    if (authError) {
      setError(authError.message);
      return;
    }
    router.push("/review");
  }

  return (
    <main className="mx-auto flex max-w-sm flex-col gap-6 p-8">
      <h1 className="text-xl font-semibold">Freight Console</h1>
      <Card>
        <form onSubmit={onSubmit} className="flex flex-col gap-3">
          <label className="text-sm font-medium">Email</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          <label className="text-sm font-medium">Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
          <Button type="submit" disabled={busy} className="mt-2">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </Card>
    </main>
  );
}
