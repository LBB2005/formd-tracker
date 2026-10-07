-- Inferred round stage for startup raises. Form D never states a round label, so this is
-- a guess from the raise size (latest amount sold, else the target) and is always shown
-- as "inferred". Bands are our choice:
--   < $3M seed, $3-15M Series A, $15-50M Series B, $50M+ growth.

alter table offerings add column if not exists raise_size double;
alter table offerings add column if not exists inferred_stage varchar;
update offerings set
    raise_size = coalesce(nullif(sold_latest, 0), offering_amt),
    inferred_stage = case
        when not is_startup_raise then null
        when coalesce(nullif(sold_latest, 0), offering_amt) is null then 'unknown'
        when coalesce(nullif(sold_latest, 0), offering_amt) < 3e6 then 'seed'
        when coalesce(nullif(sold_latest, 0), offering_amt) < 15e6 then 'series_a'
        when coalesce(nullif(sold_latest, 0), offering_amt) < 50e6 then 'series_b'
        else 'growth'
    end;
