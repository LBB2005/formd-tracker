-- Match fund managers to their Form ADV adviser (pipeline/adv.py).
-- The adviser's name is stemmed like a fund name, then words that describe an adviser
-- rather than a brand are dropped: "Kindred Ventures Management, LLC" -> "kindred ventures".
-- A manager matches an adviser with the same stem; ties prefer a California office,
-- then the adviser with the most private funds. Generic stems never match, and one-word
-- stems match only advisers with a California office.

create or replace table manager_adv as
with stems as (
    select *, regexp_replace(adviser_stem(name), '\s+', ' ', 'g') as stem from adv_advisers
    union all
    select *, regexp_replace(adviser_stem(legal_name), '\s+', ' ', 'g') from adv_advisers
    where legal_name is not null and legal_name <> name
)
select m.manager_key,
       arg_max(s.crd, {'ca': s.state = 'CA', 'n': coalesce(s.n_private_funds, 0), 'crd': s.crd}) as crd
from managers m
join stems s on s.stem = m.manager_key
where m.manager_key not like 'cik:%' and not is_generic_stem(m.manager_key) and length(m.manager_key) >= 4
  -- One-word keys ("wonder", "class") are ambiguous nationally; require a California office.
  and (m.manager_key like '% %' or s.state = 'CA')
group by m.manager_key;

alter table managers add column if not exists adv_crd varchar;
alter table managers add column if not exists adv_name varchar;
alter table managers add column if not exists adv_registration varchar;
alter table managers add column if not exists adv_vc_exemption boolean;
alter table managers add column if not exists adv_private_funds integer;
alter table managers add column if not exists adv_private_fund_assets double;
alter table managers add column if not exists adv_since date;
alter table managers add column if not exists adv_state varchar;
update managers set
    adv_crd = a.crd, adv_name = a.name, adv_registration = a.registration,
    adv_vc_exemption = a.vc_exemption, adv_private_funds = a.n_private_funds,
    adv_private_fund_assets = a.private_fund_gross_assets, adv_since = a.sec_status_date,
    adv_state = a.state
from manager_adv ma join adv_advisers a on a.crd = ma.crd
where ma.manager_key = managers.manager_key;
