-- Collapse amendment chains into offerings: one row per raise.
--
-- link_method on each filing records how it was tied to its offering:
--   original              a D filing; it is its own offering root
--   prev_pointer          D/A whose previous-accession chain reaches a D in our data
--   prev_pointer_external D/A whose chain leaves our window (offering began before start_year)
--   fallback_sale_date    D/A with a broken chain, matched to an earlier D by (CIK, first-sale date)
--   fallback_amount       ... matched by (CIK, total offering amount) within 18 months
--   orphan                D/A we could not link; stands alone as its own offering

-- 1. Walk previous-accession pointers back as far as they go.
create or replace temp table _walk as
with recursive walk(accession, cur, depth) as (
    select accession, accession, 0 from filings
    union all
    select w.accession, f.prev_accession, w.depth + 1
    from walk w
    join filings f on f.accession = w.cur
    where f.is_amendment
      and f.prev_accession is not null
      and f.prev_accession <> f.accession
      and w.depth < 50
)
select accession, arg_max(cur, depth) as root, max(depth) as chain_depth
from walk
group by accession;

-- 2. Classify each chain end.
create or replace temp table _chain as
select w.accession, w.root, w.chain_depth,
       f.accession as self_acc, f.form, f.cik, f.filing_date, f.first_sale_date, f.offering_amt,
       r.accession is not null as root_in_data,
       r.is_amendment as root_is_amendment
from _walk w
join filings f on f.accession = w.accession
left join filings r on r.accession = w.root;

-- 3. Fallbacks for chains ending at an amendment with no usable pointer.
create or replace temp table _broken_roots as
select distinct r.accession as root, r.cik, r.filing_date, r.first_sale_date, r.offering_amt
from _chain c
join filings r on r.accession = c.root
where c.root_in_data and c.root_is_amendment;

create or replace temp table _fallback as
with by_sale_date as (
    select b.root, d.accession as matched
    from _broken_roots b
    join filings d
      on d.cik = b.cik and d.form = 'D' and d.filing_date <= b.filing_date
     and d.first_sale_date = b.first_sale_date
    qualify row_number() over (partition by b.root order by d.filing_date desc, d.accession desc) = 1
),
by_amount as (
    select b.root, d.accession as matched
    from _broken_roots b
    join filings d
      on d.cik = b.cik and d.form = 'D' and d.filing_date <= b.filing_date
     and d.filing_date >= b.filing_date - interval 548 day
     and d.offering_amt = b.offering_amt
    where b.root not in (select root from by_sale_date)
    qualify row_number() over (partition by b.root order by d.filing_date desc, d.accession desc) = 1
)
select root, matched, 'fallback_sale_date' as method from by_sale_date
union all
select root, matched, 'fallback_amount' from by_amount;

-- 4. Chains that leave the window may enter it at different points
--    (one D/A points at the 2017 original, another at a 2018 amendment).
--    Merge external roots for the same CIK and first-sale date.
create or replace temp table _external_merge as
select root, min(root) over (partition by cik, first_sale_date) as canonical
from (
    select root, arg_max(cik, {'d': filing_date, 'a': accession}) as cik,
           arg_max(first_sale_date, {'d': filing_date, 'a': accession}) as first_sale_date
    from _chain
    where not root_in_data
    group by root
)
where first_sale_date is not null;

create or replace temp table _map as
select c.accession,
       case
           when c.root_in_data and not c.root_is_amendment then c.root
           when not c.root_in_data then coalesce(x.canonical, c.root)
           when fb.matched is not null then fb.matched
           else c.root
       end as offering_id,
       case
           when c.chain_depth = 0 and not c.root_is_amendment then 'original'
           when c.root_in_data and not c.root_is_amendment then 'prev_pointer'
           when not c.root_in_data then 'prev_pointer_external'
           when fb.matched is not null then fb.method
           else 'orphan'
       end as link_method
from _chain c
left join _fallback fb on fb.root = c.root
left join _external_merge x on x.root = c.root;

update filings
set offering_id = m.offering_id, link_method = m.link_method
from _map m
where filings.accession = m.accession;

-- 5. Per-amendment increments. Negative increments are corrections: flagged, not dropped.
create or replace table filing_increments as
select offering_id, accession, filing_date, form, sold, investors,
       sold - lag(sold) over w           as sold_increment,
       investors - lag(investors) over w as investor_increment,
       coalesce(sold - lag(sold) over w < 0, false) as is_correction
from filings
window w as (partition by offering_id order by filing_date, accession);

-- 6. One row per offering. Amounts come from the latest filing, never a sum.
create or replace table offerings as
with latest as (
    select * from filings
    qualify row_number() over (partition by offering_id order by filing_date desc, accession desc) = 1
),
agg as (
    select offering_id,
           min(filing_date)                          as first_filed,
           max(filing_date)                          as last_filed,
           count(*)                                  as n_filings,
           count(*) filter (where is_amendment)      as n_amendments,
           bool_or(link_method = 'original')         as has_original_in_window,
           bool_or(link_method = 'prev_pointer_external') as began_before_window,
           arg_max(first_sale_date, {'d': filing_date, 'a': accession}) filter (where first_sale_date is not null)
                                                     as first_sale_date,
           list(distinct link_method)                as link_methods,
           max(sold)                                 as sold_max,
           min(investors)                            as investors_first
    from filings
    group by offering_id
),
corrections as (
    select offering_id, count(*) filter (where is_correction) as n_corrections
    from filing_increments group by offering_id
)
select
    a.offering_id,
    l.cik,
    l.issuer_name,
    a.first_filed,
    a.last_filed,
    a.last_filed - a.first_filed                     as days_open,
    a.first_sale_date,
    a.first_filed - a.first_sale_date                as days_sale_to_first_filing,
    a.n_filings,
    a.n_amendments,
    -- An offering is "new in window" only if we hold its original D.
    a.has_original_in_window                         as is_new_in_window,
    a.began_before_window,
    a.link_methods,
    l.accession                                      as latest_accession,
    l.offering_amt,
    l.offering_indefinite,
    l.sold                                           as sold_latest,
    l.remaining                                      as remaining_latest,
    case when l.offering_amt > 0 then l.sold / l.offering_amt end as fill_pct,
    l.investors                                      as investors_latest,
    l.investors - a.investors_first                  as investors_added,
    l.min_investment,
    l.industry_group,
    l.fund_type,
    l.exemptions,
    l.is_506b,
    l.is_506c,
    l.has_3c,
    l.is_equity,
    l.is_debt,
    l.is_option,
    l.is_pooled_fund,
    l.is_other_type,
    l.other_type_desc,
    l.entity_type,
    l.sales_commission,
    l.finders_fee,
    coalesce(c.n_corrections, 0)                     as n_corrections,
    coalesce(l.offering_amt > 0 and l.sold > 2 * l.offering_amt, false) as flag_oversold,
    -- Typos like $1 trillion "sold". Flagged for review, never silently dropped.
    coalesce(l.sold >= 1e11 or l.offering_amt >= 1e11, false) as flag_implausible,
    coalesce(a.sold_max > l.sold, false)             as flag_sold_decreased
from agg a
join latest l on l.offering_id = a.offering_id
left join corrections c on c.offering_id = a.offering_id;
