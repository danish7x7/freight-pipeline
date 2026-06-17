-- Phase 9-prereq / migration 11: add 'container' to equipment_type (drayage switch).
--
-- MUST stand alone: Postgres forbids USING a newly added enum value in the SAME
-- transaction that adds it, so no migration that references 'container' (e.g. the
-- pricing_components drayage_base seed) may live in this file. Those go in a LATER
-- migration. 'container' is the drayage equipment class; the rate engine switches it
-- to the flat drayage costing model (base + FSC + accessorials), distinct from the
-- per-mile model used by dry_van/reefer/flatbed/step_deck/power_only.
alter type public.equipment_type add value if not exists 'container';
