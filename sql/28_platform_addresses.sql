-- SPV platforms file thousands of vehicles from one administrator's office: AngelList's
-- Belltower at 119 South Main St, Seattle, put ~18,000 SPVs "in Seattle" whose managers
-- are everywhere. An address shared by 200+ issuers, at least 80% of them SPV-named,
-- is treated as a platform address. Fund vehicles there are removed from any metro
-- (their location is the administrator, not the manager). Operating companies that
-- happen to use the same address keep theirs.

create or replace table platform_addresses as
with keyed as (
    select i.cik, i.zip5, i.name,
           upper(regexp_replace(coalesce(regexp_extract(i.street1, '^\s*(\d+\s+\S+(\s+\S+)?)', 1), ''),
                                '[^A-Za-z0-9 ]', '', 'g')) as addr_key
    from issuers i
    where i.street1 is not null and i.zip5 is not null
)
select addr_key, zip5, count(*) as n_issuers,
       avg(regexp_matches(name, '(?i)(a series of|\bspv\d*\b|\bseries \d+|syndicat)')::int) as spv_share
from keyed
where addr_key <> ''
group by addr_key, zip5
having count(*) >= 200
   and avg(regexp_matches(name, '(?i)(a series of|\bspv\d*\b|\bseries \d+|syndicat)')::int) >= 0.8;

create or replace temp table _platform_vehicles as
select i.cik
from issuers i
join platform_addresses p
  on p.zip5 = i.zip5
 and p.addr_key = upper(regexp_replace(coalesce(regexp_extract(i.street1, '^\s*(\d+\s+\S+(\s+\S+)?)', 1), ''),
                                       '[^A-Za-z0-9 ]', '', 'g'))
where i.cik in (select cik from offerings
                where industry_group = 'Pooled Investment Fund' or is_pooled_fund or has_3c
                   or regexp_matches(issuer_name, '(?i)(a series of|\bspv\d*\b|\bseries \d+|syndicat)'));

update issuers set metro = null, metro_label = null, geo_method = 'platform_admin_address'
where cik in (select cik from _platform_vehicles) and not coalesce(metro_overridden, false);

update offerings set metro = null
where cik in (select cik from _platform_vehicles)
  and cik not in (select cik from issuers where metro is not null);
