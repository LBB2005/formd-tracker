-- Classify offerings: fund vs operating company, SPV vs blind-pool fund, fund number,
-- manager grouping, and emerging manager. Every call carries a *_reason column so the
-- Week 2 review can see which rule fired. Hand corrections from
-- config/manual_overrides.csv are applied before managers and fund families are built.

create or replace temp table _class as
with base as (
    select
        o.offering_id, o.cik, o.issuer_name, o.industry_group, o.fund_type, o.entity_type,
        o.is_pooled_fund, o.has_3c, o.is_equity, o.is_debt, o.is_option, o.is_other_type,
        o.other_type_desc, f.is_security_to_be_acquired, o.first_filed, o.first_sale_date,
        o.offering_amt, o.investors_latest, o.metro, o.is_new_in_window, o.duplicate_of,
        i.is_venture_stage,
        regexp_matches(o.issuer_name,
            '(?i)(a series of|\bspv\d*\b|syndicat|co-?invest|\(investment in|\bspecial purpose|'
            '\s-\s.*\bseries$|'
            '\bseries \d+\b|\bseries [a-z0-9]*\d[a-z0-9]*\b|(llc|lp|l\.p\.),?\s*-?\s*series\b)')
            as vehicle_name,
        -- A partnership, LLC or trust with "Fund" as a word in its name pools investors'
        -- money even when filed under "Other" ("Hyla Fund III, LLC", "TR3 Capital Fund VI
        -- LLC"). Corporations are left out: "Ember Fund Inc" and "HazelTree Fund Services,
        -- Inc." are startups. So are management companies ("Meta4 Fund Management, LLC").
        o.entity_type <> 'Corporation'
            and regexp_matches(o.issuer_name, '(?i)\bfund\b')
            and not regexp_matches(o.issuer_name, '(?i)\b(inc|corp|corporation|incorporated)\.?$')
            and not regexp_matches(o.issuer_name,
                '(?i)\bfund\s+(manager|management|advisors?|advisers?|services|solutions|'
                'admin\w*|technolog\w*|software)\b')
            as fund_named
    from offerings o
    join filings f on f.accession = o.latest_accession
    join issuers i on i.cik = o.cik
),
kinded as (
    select *,
        case
            when industry_group = 'Pooled Investment Fund' then 'fund'
            when is_pooled_fund then 'fund'
            when has_3c then 'fund'
            when vehicle_name then 'fund'
            when industry_group in ('Commercial', 'Residential', 'Other Real Estate',
                                    'REITS and Finance', 'Construction') then 'real_estate'
            -- Fintech startups sometimes pick "Investing" (FalconX, Snowball Finance).
            when industry_group = 'Investing' and entity_type = 'Corporation' then 'operating_company'
            when industry_group = 'Investing' then 'other_investing'
            -- Oil, gas and energy "funds" are drilling, royalty or project programs
            -- ("USEDC 2024 B Drilling Fund LP", "Main Street Solar Fund II, LLC").
            when fund_named and industry_group in ('Oil and Gas', 'Coal Mining', 'Other Energy',
                                                   'Electric Utilities', 'Energy Conservation')
                then 'other_investing'
            when fund_named then 'fund'
            else 'operating_company'
        end as kind,
        case
            when industry_group = 'Pooled Investment Fund' then 'industry_group'
            when is_pooled_fund then 'pooled_interest_flag'
            when has_3c then '3c_exemption'
            when vehicle_name then 'vehicle_name'
            when industry_group in ('Commercial', 'Residential', 'Other Real Estate',
                                    'REITS and Finance', 'Construction', 'Investing')
                then 'industry_group'
            when fund_named then 'name: fund word (not a corporation)'
            else 'industry_group'
        end as kind_reason,
        -- What the operating company sold. SAFEs are usually filed as "option" or "other".
        case
            when regexp_matches(coalesce(other_type_desc, ''),
                    '(?i)(\bsafe\b|simple agreement|convertible|future equity)')
                 or is_option or is_security_to_be_acquired then 'safe_or_convertible'
            when is_equity then 'equity'
            when is_debt then 'debt'
            else 'other'
        end as security_class
    from base
),
vehicles as (
    select *,
        case when kind <> 'fund' then null
             when fund_type = 'Venture Capital Fund' then 'venture'
             when fund_type = 'Private Equity Fund' then 'private_equity'
             when fund_type = 'Hedge Fund' then 'hedge'
             when fund_type = 'Other Investment Fund' then 'other'
             else 'unspecified'
        end as fund_bucket,
        case when kind = 'fund' then fund_number(issuer_name) end as fund_number,
        regexp_matches(issuer_name,
            '(?i)(\bparallel\b|\bfeeder\b|offshore|cayman|\baiv\b|\bannex\b|side[- ]?car|'
            '\bqp\b|entrepreneurs fund|\baffiliates\b|\bassociates\b|[ivx]+-[a-d]\b|\b[ivx]+[a-d]\b)')
            as is_parallel,
        case when kind = 'fund' then vehicle_code(issuer_name, entity_type) end as vehicle_code
    from kinded
)
select *,
    case
        when kind <> 'fund' then null
        when vehicle_name then 'spv'
        -- A single company in the name: "THVC (Virta E)", "(Investment in KVM)".
        when regexp_matches(issuer_name, '(?i)\((investment in|series [a-z0-9])') then 'spv'
        when fund_number is not null
          or regexp_matches(issuer_name,
                '(?i)(\bfund\b|opportunit|vintage|early stage|\bseed\b|\bgrowth\b|\b(19|20)\d\d\b)')
            then 'blind_pool'
        -- AngelList rolling funds file one class per quarter: "Ravikant Capital, LP - G2".
        when regexp_matches(issuer_name, '\b(LP|L\.P\.)\s*-\s*[A-Z]\d(\s+QP)?$') then 'blind_pool'
        -- An LP named like a manager ("Indus Valley Capital, LP"). SPVs are mostly LLCs or
        -- carry a deal word; an LP with anything after the manager words stays unclear.
        when entity_type = 'Limited Partnership'
         and regexp_matches(issuer_name, '(?i)\b(capital|ventures?|partners),?\s+(lp|l\.p\.)$')
            then 'blind_pool'
        else 'unclear'
    end as vehicle_type,
    case
        when kind <> 'fund' then null
        when vehicle_name then 'name: series/spv/syndicate/co-invest'
        when regexp_matches(issuer_name, '(?i)\((investment in|series [a-z0-9])') then 'name: single deal'
        when fund_number is not null then 'name: fund number'
        when regexp_matches(issuer_name,
                '(?i)(\bfund\b|opportunit|vintage|early stage|\bseed\b|\bgrowth\b|\b(19|20)\d\d\b)')
            then 'name: fund word or vintage year'
        when regexp_matches(issuer_name, '\b(LP|L\.P\.)\s*-\s*[A-Z]\d(\s+QP)?$')
            then 'name: rolling-fund quarterly class'
        when entity_type = 'Limited Partnership'
         and regexp_matches(issuer_name, '(?i)\b(capital|ventures?|partners),?\s+(lp|l\.p\.)$')
            then 'structure: LP named like a manager'
        else 'no name signal'
    end as vehicle_reason,
    case when kind = 'fund' then
        case when is_generic_stem(manager_stem(issuer_name)) then 'cik:' || cik
             else manager_stem(issuer_name) end
    end as manager_key,
    kind = 'operating_company' and security_class in ('equity', 'safe_or_convertible')
        and is_venture_stage as is_startup_raise
from vehicles;

-- Deal-by-deal series ("HCM G I" ... "HCM G XX") look like numbered funds. No manager
-- raises four distinct numbered funds within 12 months, so those become SPVs. Likewise,
-- three or more vehicles whose names differ only by a short code ("AL-D" / "CO-B" / "GW-E
-- Ribbit Opportunity V, LLC") are one vehicle per deal, whatever the fund number says.
create or replace table offering_class as
with code_families as (
    select struct_extract(vehicle_code, 'family') as family
    from _class
    where vehicle_code is not null
    group by 1
    having count(distinct struct_extract(vehicle_code, 'code')) >= 3
),
numbered as (
    select offering_id, manager_key, fund_number, first_filed
    from _class
    where vehicle_type = 'blind_pool' and fund_number is not null
),
series_rate as (
    select a.offering_id, count(distinct b.fund_number) as numbers_in_year
    from numbered a
    join numbered b
      on b.manager_key = a.manager_key
     and b.first_filed between a.first_filed - interval 365 day and a.first_filed + interval 365 day
    group by a.offering_id
)
select c.* exclude (vehicle_code, fund_named) replace (
        case when r.numbers_in_year >= 4 or f.family is not null then 'spv'
             else c.vehicle_type end as vehicle_type,
        case when r.numbers_in_year >= 4
                 then 'manager: 4+ numbered vehicles within 12 months (deal series)'
             when f.family is not null and c.vehicle_type <> 'spv'
                 then 'manager: 3+ vehicles that differ only by a code (one per deal)'
             else c.vehicle_reason end as vehicle_reason),
       []::varchar[] as overridden_fields
from _class c
left join series_rate r using (offering_id)
left join code_families f on f.family = struct_extract(c.vehicle_code, 'family');

-- Unclear vehicles named "<known manager> <extra words>" are often SPVs ("Mucker T2",
-- "Draper Associates Venus") but not reliably ("GGV Capital Holdings" is a GP entity), so
-- they are flagged for review rather than reclassified.
create or replace temp table _known_prefix as
with unclear as (
    select offering_id, string_split(manager_stem(issuer_name), ' ') as toks
    from offering_class where vehicle_type = 'unclear'
),
prefixes as (  -- proper word-prefixes: "mucker t2" -> "mucker"
    select offering_id, array_to_string(toks[1:k], ' ') as prefix
    from unclear, range(1, len(toks)) r(k)
)
select distinct p.offering_id
from prefixes p
join (select distinct manager_key from offering_class
      where vehicle_type = 'blind_pool' and manager_key not like 'cik:%'
        and length(manager_key) >= 5) b
  on b.manager_key = p.prefix;

update offering_class
set vehicle_reason = 'no name signal; known manager + extra words (often an SPV)'
where offering_id in (select offering_id from _known_prefix);

-- What the rules alone say, kept when hand overrides below change kind or vehicle_type,
-- so the hand-labelled review sample can be scored against today's rules.
alter table offering_class add column rule_kind varchar;
alter table offering_class add column rule_vehicle_type varchar;
update offering_class set rule_kind = kind, rule_vehicle_type = vehicle_type;

-- Hand corrections. An offering-specific row beats a CIK-wide row for the same field.
create or replace temp table _ov as
select c.offering_id, m.field, arg_min(m.value, m.offering_id is null) as value
from offering_class c
join manual_overrides m
  on m.cik = c.cik and (m.offering_id is null or m.offering_id = c.offering_id)
where m.field in ('kind', 'vehicle_type', 'fund_bucket', 'manager_key')
group by c.offering_id, m.field;

update offering_class set kind = o.value, overridden_fields = list_append(overridden_fields, 'kind')
from _ov o where o.offering_id = offering_class.offering_id and o.field = 'kind';
update offering_class set vehicle_type = o.value,
    overridden_fields = list_append(overridden_fields, 'vehicle_type')
from _ov o where o.offering_id = offering_class.offering_id and o.field = 'vehicle_type';
update offering_class set fund_bucket = o.value,
    overridden_fields = list_append(overridden_fields, 'fund_bucket')
from _ov o where o.offering_id = offering_class.offering_id and o.field = 'fund_bucket';
update offering_class set manager_key = o.value,
    overridden_fields = list_append(overridden_fields, 'manager_key')
from _ov o where o.offering_id = offering_class.offering_id and o.field = 'manager_key';
update offering_class
set is_startup_raise = kind = 'operating_company' and is_venture_stage
                       and security_class in ('equity', 'safe_or_convertible')
where len(overridden_fields) > 0;

-- Managers: one row per manager_key across all fund vehicles. Duplicate filings
-- (duplicate_of, 20_offerings.sql) are not counted as extra vehicles.
create or replace table managers as
select
    manager_key,
    count(*) filter (where duplicate_of is null)                as n_vehicles,
    count(*) filter (where vehicle_type = 'blind_pool' and duplicate_of is null) as n_blind_pool,
    count(*) filter (where vehicle_type = 'spv' and duplicate_of is null)        as n_spv,
    count(distinct cik)                                         as n_ciks,
    min(first_filed)                                            as first_vehicle_filed,
    max(fund_number)                                            as fund_number_max,
    bool_or(fund_bucket = 'venture')                            as has_vc_vehicle,
    mode(metro)                                                 as metro,
    list(distinct issuer_name order by issuer_name)[1:8]        as sample_names
from offering_class
where manager_key is not null
group by manager_key;

-- Fund families collapse parallel vehicles ("Fund I, LP" + "Fund I-A, LP") into one fund:
-- same manager and fund number, or same manager and vintage year when unnumbered.
-- Emerging manager = Fund I-III. Unnumbered funds count as emerging when the manager has
-- at most two earlier blind-pool fund families in the data. (Our choice; see README.)
create or replace table fund_families as
with fams as (
    select manager_key, fund_bucket, metro,
           manager_key || '|' || coalesce('fund' || fund_number::varchar,
                                          'vintage' || year(coalesce(first_sale_date, first_filed))::varchar)
               as fund_family_id,
           fund_number, first_filed, offering_amt, offering_id, is_parallel, is_new_in_window
    from offering_class
    where vehicle_type = 'blind_pool' and duplicate_of is null  -- a duplicate is not a vehicle
),
agg as (
    select fund_family_id, any_value(manager_key) as manager_key,
           arg_min(fund_bucket, first_filed) as fund_bucket, arg_min(metro, first_filed) as metro,
           any_value(fund_number) as fund_number, min(first_filed) as first_filed,
           -- Parallel vehicles often each state the whole fund's target, so take the
           -- largest rather than summing. NULL when every vehicle says "Indefinite".
           count(*) as n_vehicles, max(offering_amt) as target_amt,
           bool_and(is_new_in_window) as is_new_in_window,
           list(offering_id order by first_filed, offering_id) as offering_ids
    from fams group by fund_family_id
)
select *,
    count(*) over (partition by manager_key order by first_filed, fund_family_id
                   rows between unbounded preceding and 1 preceding) as prior_families,
    case
        when fund_number is not null then fund_number <= 3
        else count(*) over (partition by manager_key order by first_filed, fund_family_id
                            rows between unbounded preceding and 1 preceding) <= 2
    end as is_emerging,
    case when fund_number is not null then 'fund number'
         else 'prior families in data' end as emerging_basis
from agg;

alter table offerings add column if not exists kind varchar;
alter table offerings add column if not exists vehicle_type varchar;
alter table offerings add column if not exists fund_bucket varchar;
alter table offerings add column if not exists fund_number integer;
alter table offerings add column if not exists manager_key varchar;
alter table offerings add column if not exists security_class varchar;
alter table offerings add column if not exists is_startup_raise boolean;
alter table offerings add column if not exists overridden_fields varchar[];
alter table offerings add column if not exists rule_kind varchar;
alter table offerings add column if not exists rule_vehicle_type varchar;
update offerings set
    kind = c.kind, vehicle_type = c.vehicle_type, fund_bucket = c.fund_bucket,
    fund_number = c.fund_number, manager_key = c.manager_key,
    security_class = c.security_class, is_startup_raise = c.is_startup_raise,
    overridden_fields = c.overridden_fields,
    rule_kind = c.rule_kind, rule_vehicle_type = c.rule_vehicle_type
from offering_class c
where c.offering_id = offerings.offering_id;
