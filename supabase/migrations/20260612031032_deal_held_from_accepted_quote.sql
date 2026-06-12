-- Phase 4 / migration 8: deal columns for resume + accepted quote.
--
-- held_from: the active state a deal was held FROM, so resume returns into it (the
-- pure state machine carries no history). Set when a deal is moved to on_hold.
-- accepted_quote_id: which quote the deal settled on. Column added now; populated in
-- Phase 5 when a human approves a quote.
alter table public.deals
    add column held_from public.deal_state,
    add column accepted_quote_id uuid references public.quotes (id);
