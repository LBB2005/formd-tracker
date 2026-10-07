-- One row per issuer (CIK), placed by the principal office on its latest filing.
-- ZIP -> county uses the Census ZCTA file; non-ZCTA ZIPs (PO boxes) fall back to
-- the dominant county of their 3-digit prefix. geo_method records which was used.

create or replace table issuers as
with latest as (
    select * from filings
    qualify row_number() over (partition by cik order by filing_date desc, accession desc) = 1
),
names as (
    select cik,
           list_sort(list_distinct(flatten(list(previous_names)) || list(issuer_name))) as all_names,
           count(*)                                  as n_filings,
           count(distinct offering_id)               as n_offerings,
           min(filing_date)                          as first_filing,
           max(filing_date)                          as last_filing
    from filings
    group by cik
),
us_states as (
    select unnest(['AL','AK','AZ','AR','CA','CO','CT','DE','DC','FL','GA','HI','ID','IL','IN',
                   'IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH',
                   'NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT',
                   'VT','VA','WA','WV','WI','WY','PR']) as state
),
by_zip as (
    select l.cik, l.city, l.state, l.zip5, l.is_us, z.county_fips
    from (select *, state in (select state from us_states) as is_us from latest) l
    left join zip_county z on z.zip5 = l.zip5 and l.is_us
),
-- Typo'd or PO-box ZIPs: use the county most issuers in the same city resolve to,
-- when at least 5 do and 80% agree. Only then fall back to the ZIP3's county.
city_counts as (
    select state, city, county_fips, count(*) as n,
           sum(count(*)) over (partition by state, city) as total
    from by_zip
    where county_fips is not null and city is not null
    group by state, city, county_fips
),
city_county as (
    select state, city, county_fips from city_counts
    where total >= 5 and n >= 0.8 * total
),
placed as (
    select b.cik,
           coalesce(b.county_fips, cc.county_fips, z3.county_fips) as county_fips,
           case when b.county_fips is not null then 'zcta'
                when cc.county_fips is not null then 'city'
                when z3.county_fips is not null then 'zip3' end as geo_method
    from by_zip b
    left join city_county cc on b.county_fips is null and cc.state = b.state and cc.city = b.city
    left join zip3_county z3 on b.county_fips is null and cc.county_fips is null
                            and b.is_us and z3.zip3 = left(b.zip5, 3)
)
select
    l.cik,
    l.issuer_name                       as name,
    list_filter(n.all_names, x -> x <> l.issuer_name) as former_names,
    l.street1,
    l.city,
    l.state,
    l.zip5,
    p.county_fips,
    mc.county_name,
    mc.metro,
    mc.metro_label,
    p.geo_method,
    l.entity_type,
    l.jurisdiction,
    l.year_inc,
    l.year_inc_choice,
    n.n_filings,
    n.n_offerings,
    n.first_filing,
    n.last_filing,
    -- Established companies (Intel, TriNet) file the odd Form D too. CIKs are assigned
    -- in sequence, so a CIK below 1,400,000 means EDGAR registration before about 2007;
    -- combined with "incorporated over five years ago" that marks a non-startup.
    not (l.cik::bigint < 1400000 and coalesce(l.year_inc_choice, '') = 'overFiveYears')
        as is_venture_stage,
    'https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&type=D&CIK=' || l.cik as edgar_url
from latest l
join names n  on n.cik = l.cik
join placed p on p.cik = l.cik
left join metro_counties mc on mc.county_fips = p.county_fips;

-- Map coordinates from pipeline/geocode.py (Census geocoder, else ZIP centroid).
alter table issuers add column lat double;
alter table issuers add column lon double;
alter table issuers add column geocode_method varchar;
update issuers set lat = g.lat, lon = g.lon, geocode_method = g.geocode_method
from geocodes g where g.cik = issuers.cik;

-- A street address that geocodes outside the metro beats a ZIP that pointed inside it
-- (a mistyped ZIP, e.g. Glenview IL with a San Francisco ZIP). Drop those from the metro.
update issuers set metro = null, metro_label = null, lat = null, lon = null,
    geo_method = 'zip_conflicts_with_address'
from metro_bbox b
where b.metro = issuers.metro and issuers.geocode_method = 'address'
  and not (issuers.lat between b.lat_min and b.lat_max and issuers.lon between b.lon_min and b.lon_max);

-- Hand corrections to metro from config/manual_overrides.csv.
alter table issuers add column metro_overridden boolean default false;
update issuers set metro = m.value, metro_overridden = true,
    metro_label = (select any_value(metro_label) from metro_counties mc where mc.metro = m.value)
from manual_overrides m
where m.field = 'metro' and m.cik = issuers.cik and m.offering_id is null;

-- Carry metro onto offerings for convenience.
alter table offerings add column if not exists metro varchar;
alter table offerings add column if not exists county_name varchar;
update offerings set metro = i.metro, county_name = i.county_name
from issuers i where i.cik = offerings.cik;
