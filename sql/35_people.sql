-- People and entities named on Form D (executive officers, directors, promoters).
--
-- person_key = first + last name + the state on their address. The middle name is left
-- out: the SEC's quarterly RELATEDPERSONS table copies one person's middle name onto the
-- others in a filing. Adding the state cuts down on name collisions ("David Lee"); a
-- founder who moves states shows up as two keys. Entities (GPs, management companies)
-- are filed with first name "N/A" and the entity in the last-name field.

create or replace table issuer_people as
with rp as (
    select f.cik, f.filing_date, r.*,
           upper(trim(regexp_replace(coalesce(r.FIRSTNAME, ''), '[^A-Za-z0-9 \-'']', '', 'g'))) as fn,
           upper(trim(regexp_replace(coalesce(r.LASTNAME, ''), '[^A-Za-z0-9 \-'']', '', 'g')))  as ln
    from raw_relatedpersons r
    join filings f on f.accession = r.ACCESSIONNUMBER
),
typed as (
    select *,
        fn in ('NA', 'N A', '', '-', 'NONE')
            or regexp_matches(ln, '\b(LLC|L L C|LP|L P|INC|LTD|CORP|CORPORATION|COMPANY|PARTNERS|'
                                  'MANAGEMENT|CAPITAL|ADVISORS|ADVISERS|GP|FUND|TRUST|HOLDINGS|VENTURES)\b')
            as is_entity,
        -- Entities may be split either way round: "N/A" + "Fund GP, LLC", "Belltower" +
        -- "Fund Group, Ltd.", or reversed: "LLC" + "Fund GP,".
        trim(regexp_replace(
            case when fn in ('NA', 'N A', '', '-', 'NONE') then trim(LASTNAME)
                 when regexp_full_match(fn, 'LLC|L L C|LTD|INC|LP|L P|CORP|LLLP')
                     then trim(LASTNAME) || ' ' || trim(FIRSTNAME)
                 else trim(FIRSTNAME) || ' ' || trim(LASTNAME) end,
            '[,\s]+$', '')) as entity_name
    from rp
)
select
    cik,
    -- Entities are sometimes split across both name fields ("Belltower" + "Fund GP, LLC").
    case when is_entity then 'e:' || lower(trim(regexp_replace(entity_name, '[^A-Za-z0-9]+', ' ', 'g')))
         else 'p:' || lower(fn || ' ' || ln) || '|' || coalesce(upper(trim(STATEORCOUNTRY)), '')
    end as person_key,
    any_value(case when is_entity then unquote(entity_name)
                   else trim(FIRSTNAME) || ' ' || trim(LASTNAME) end) as display_name,
    any_value(is_entity) as is_entity,
    list_sort(list_distinct(flatten(list([RELATIONSHIP_1, RELATIONSHIP_2, RELATIONSHIP_3])))) as roles,
    min(filing_date) as first_seen,
    max(filing_date) as last_seen,
    count(*) as n_filings
from typed
where length(ln) > 1
group by all;

-- Strip NULL/blank roles left by list flattening.
update issuer_people set roles = list_filter(roles, x -> x is not null and trim(x) <> '');

create or replace table issuer_kind as
select cik,
       arg_max(kind, {'d': last_filed, 'o': offering_id})         as kind,
       arg_max(vehicle_type, {'d': last_filed, 'o': offering_id}) as vehicle_type,
       arg_max(manager_key, {'d': last_filed, 'o': offering_id})  as manager_key,
       bool_or(is_startup_raise)                                  as has_startup_raise,
       bool_or(vehicle_type = 'blind_pool' and fund_bucket = 'venture') as is_vc_fund
from offerings
group by cik;

create or replace table people as
select
    p.person_key,
    any_value(p.display_name)                                         as display_name,
    any_value(p.is_entity)                                            as is_entity,
    count(distinct p.cik)                                             as n_issuers,
    count(distinct p.cik) filter (where k.has_startup_raise
                                   and list_contains(p.roles, 'Executive Officer'))
                                                                      as n_startups_as_exec,
    count(distinct p.cik) filter (where k.kind = 'fund')              as n_funds,
    count(distinct p.cik) filter (where k.vehicle_type = 'blind_pool') as n_blind_pool,
    count(distinct p.cik) filter (where k.vehicle_type = 'spv')       as n_spv,
    list(distinct k.manager_key) filter (where k.manager_key is not null) as manager_keys,
    min(p.first_seen)                                                 as first_seen,
    max(p.last_seen)                                                  as last_seen,
    list(distinct i.metro) filter (where i.metro is not null)         as metros,
    count(distinct p.cik) filter (where k.is_vc_fund)                 as n_vc_funds,
    -- Executive officer of a startup that raised, later named on a blind-pool VC fund.
    min(p.first_seen) filter (where k.has_startup_raise
                               and list_contains(p.roles, 'Executive Officer'))
        < max(p.first_seen) filter (where k.is_vc_fund)               as exec_turned_gp
from issuer_people p
join issuer_kind k using (cik)
join issuers i using (cik)
group by p.person_key;

-- Fund administrators, platform officers and PE signatories are named on hundreds of
-- vehicles and portfolio companies. Anyone on 50+ issuers is treated as platform staff
-- and excluded from the founder measures.
alter table people add column is_platform_staff boolean;
alter table people add column is_repeat_founder boolean;
update people set is_platform_staff = n_issuers >= 50;
update people set
    is_repeat_founder = not is_entity and not is_platform_staff and n_startups_as_exec >= 2,
    exec_turned_gp = coalesce(exec_turned_gp, false) and not is_entity
                        and not is_platform_staff;
