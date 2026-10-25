-- Capital momentum: the regulatory-filing sub-score of the Momentum Scorecard.
-- One row per venture-stage startup with at least one startup raise.
--
-- Inputs (all from Form D):
--   recency     days from the latest filing on any offering to the data's as-of date
--   step_up     latest raise size / previous raise size (same CIK)
--   pace        median months between successive new raises (gaps under 30 days ignored)
--   fill_90d    share of the latest raise's target sold within 90 days of first sale
--   investors   investor count on the latest raise
-- Each input becomes a percentile within the peer group (AI or not x metro x size band
-- of the latest raise); the score is the mean of available percentiles x 100.
-- Missing Form Ds mean no data is not the same as no momentum, so confidence is 'low'
-- when a company has a single filing.

create or replace table capital_momentum as
with as_of as (select max(filing_date) as d from filings),
raises as (
    select o.*, coalesce(o.offering_amt, o.sold_latest) as size,
           row_number() over (partition by o.cik order by o.first_filed desc, o.offering_id desc) as rn_desc,
           lag(o.first_filed) over (partition by o.cik order by o.first_filed, o.offering_id) as prev_filed,
           lag(coalesce(o.offering_amt, o.sold_latest))
               over (partition by o.cik order by o.first_filed, o.offering_id) as prev_size
    from offerings o
    where o.is_startup_raise and o.duplicate_of is null  -- a re-filed copy is not a raise
),
fill as (
    select r.offering_id,
           max(f.sold) filter (where f.filing_date <= r.first_sale_date + interval 90 day) as sold_90d
    from raises r
    join filings f on f.offering_id = r.offering_id
    where r.rn_desc = 1
    group by r.offering_id
),
per_company as (
    select r.cik,
           count(*)                                                 as n_raises,
           max(r.last_filed)                                        as last_filing,
           (select d from as_of) - max(r.last_filed)                 as recency_days,
           arg_min(r.size, r.rn_desc)                               as latest_size,
           arg_min(r.prev_size, r.rn_desc)                          as previous_size,
           -- Offerings filed within 30 days of each other (an equity round plus a SAFE,
           -- say) are one round, so those gaps don't count toward pace.
           median(date_diff('day', r.prev_filed, r.first_filed) / 30.44)
               filter (where date_diff('day', r.prev_filed, r.first_filed) >= 30)
                                                                    as pace_months,
           arg_min(r.offering_id, r.rn_desc)                        as latest_offering_id,
           arg_min(r.investors_latest, r.rn_desc)                   as investors,
           arg_min(r.offering_amt, r.rn_desc)                       as latest_target,
           sum(r.n_filings)                                         as n_filings,
           any_value(r.metro)                                       as metro,
           bool_or(r.is_ai)                                         as is_ai
    from raises r
    group by r.cik
),
inputs as (
    select p.*,
           case when p.previous_size > 0 then p.latest_size / p.previous_size end as step_up,
           case when p.latest_target > 0 then least(f.sold_90d / p.latest_target, 1.0) end as fill_90d,
           case when p.latest_size < 2e6 then 'a <$2M' when p.latest_size < 10e6 then 'b $2-10M'
                when p.latest_size < 50e6 then 'c $10-50M' else 'd $50M+' end as size_band
    from per_company p
    left join fill f on f.offering_id = p.latest_offering_id
),
ranked as (
    select *,
        -- percent_rank over non-null values only; NULL inputs stay NULL.
        case when recency_days is not null then 1 - percent_rank() over (
            partition by is_ai, metro, size_band, recency_days is null order by recency_days) end as p_recency,
        case when step_up is not null then percent_rank() over (
            partition by is_ai, metro, size_band, step_up is null order by step_up) end as p_step_up,
        case when pace_months is not null then 1 - percent_rank() over (
            partition by is_ai, metro, size_band, pace_months is null order by pace_months) end as p_pace,
        case when fill_90d is not null then percent_rank() over (
            partition by is_ai, metro, size_band, fill_90d is null order by fill_90d) end as p_fill,
        case when investors is not null then percent_rank() over (
            partition by is_ai, metro, size_band, investors is null order by investors) end as p_investors,
        count(*) over (partition by is_ai, metro, size_band) as peer_group_size
    from inputs
)
select *,
    round(100 * (coalesce(p_recency, 0) + coalesce(p_step_up, 0) + coalesce(p_pace, 0)
                 + coalesce(p_fill, 0) + coalesce(p_investors, 0))
          / nullif((p_recency is not null)::int + (p_step_up is not null)::int
                   + (p_pace is not null)::int + (p_fill is not null)::int
                   + (p_investors is not null)::int, 0), 1) as capital_score,
    case when n_filings < 2 then 'low' when n_raises < 2 then 'medium' else 'high' end
        as data_confidence
from ranked;
