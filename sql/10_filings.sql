-- One row per Form D accession (D or D/A), typed and cleaned.
-- Source: raw_submission + raw_offering + the primary issuer in raw_issuers.
-- offering_id and link_method are filled in by 20_offerings.sql.

create or replace table filings as
with issuer as (
    select * from raw_issuers
    where IS_PRIMARYISSUER_FLAG = 'YES'
    qualify row_number() over (partition by ACCESSIONNUMBER order by ISSUER_SEQ_KEY) = 1
),
offering as (
    select * from raw_offering
    qualify row_number() over (partition by ACCESSIONNUMBER order by source_quarter desc) = 1
),
exemptions as (
    select ACCESSIONNUMBER,
           list_filter(list_transform(string_split(coalesce(FEDERALEXEMPTIONS_ITEMS_LIST, ''), ','),
                                      x -> trim(x)), x -> x <> '') as exemptions
    from offering
)
select
    s.ACCESSIONNUMBER                                   as accession,
    lpad(trim(i.CIK), 10, '0')                          as cik,
    unquote(trim(i.ENTITYNAME))                         as issuer_name,
    parse_date(s.FILING_DATE)                           as filing_date,
    s.source_quarter                                    as source_quarter,
    trim(s.SUBMISSIONTYPE)                              as form,
    trim(s.SUBMISSIONTYPE) = 'D/A' or parse_bool(o.ISAMENDMENT) as is_amendment,
    parse_accession(o.PREVIOUSACCESSIONNUMBER)          as prev_accession,
    nullif(trim(s.SIC_CODE), '')                        as sic_code,

    -- What is being raised
    nullif(trim(o.INDUSTRYGROUPTYPE), '')               as industry_group,
    nullif(trim(o.INVESTMENTFUNDTYPE), '')              as fund_type,
    parse_bool(o.IS40ACT)                               as is_40act,
    nullif(trim(o.REVENUERANGE), '')                    as revenue_range,
    nullif(trim(o.AGGREGATENETASSETVALUERANGE), '')     as nav_range,
    e.exemptions,
    list_contains(e.exemptions, '06b')                  as is_506b,
    list_contains(e.exemptions, '06c')                  as is_506c,
    list_contains(e.exemptions, '04')                   as is_504,
    len(list_filter(e.exemptions, x -> x like '3C%')) > 0 as has_3c,

    -- Security types
    parse_bool(o.ISEQUITYTYPE)                          as is_equity,
    parse_bool(o.ISDEBTTYPE)                            as is_debt,
    parse_bool(o.ISOPTIONTOACQUIRETYPE)                 as is_option,
    parse_bool(o.ISSECURITYTOBEACQUIREDTYPE)            as is_security_to_be_acquired,
    parse_bool(o.ISPOOLEDINVESTMENTFUNDTYPE)            as is_pooled_fund,
    parse_bool(o.ISTENANTINCOMMONTYPE)                  as is_tenant_in_common,
    parse_bool(o.ISMINERALPROPERTYTYPE)                 as is_mineral_property,
    parse_bool(o.ISOTHERTYPE)                           as is_other_type,
    unquote(nullif(trim(o.DESCRIPTIONOFOTHERTYPE), '')) as other_type_desc,
    parse_bool(o.ISBUSINESSCOMBINATIONTRANS)            as is_business_combination,

    -- Timing
    parse_date(o.SALE_DATE)                             as first_sale_date,
    parse_bool(o.YETTOOCCUR)                            as sale_yet_to_occur,
    parse_bool(o.MORETHANONEYEAR)                       as more_than_one_year,

    -- Money and investors (snapshot as of this filing)
    parse_amount(o.MINIMUMINVESTMENTACCEPTED)           as min_investment,
    parse_amount(o.TOTALOFFERINGAMOUNT)                 as offering_amt,
    lower(trim(o.TOTALOFFERINGAMOUNT)) = 'indefinite'   as offering_indefinite,
    parse_amount(o.TOTALAMOUNTSOLD)                     as sold,
    parse_amount(o.TOTALREMAINING)                      as remaining,
    parse_bool(o.HASNONACCREDITEDINVESTORS)             as has_nonaccredited,
    try_cast(o.NUMBERNONACCREDITEDINVESTORS as integer) as n_nonaccredited,
    try_cast(o.TOTALNUMBERALREADYINVESTED as integer)   as investors,
    parse_amount(o.SALESCOMM_DOLLARAMOUNT)              as sales_commission,
    parse_amount(o.FINDERSFEE_DOLLARAMOUNT)             as finders_fee,
    parse_amount(o.GROSSPROCEEDSUSED_DOLLARAMOUNT)      as gross_proceeds_used,

    -- Issuer as stated on this filing
    nullif(trim(i.ENTITYTYPE), '')                      as entity_type,
    nullif(trim(i.JURISDICTIONOFINC), '')               as jurisdiction,
    nullif(trim(i.YEAROFINC_TIMESPAN_CHOICE), '')       as year_inc_choice,
    try_cast(i.YEAROFINC_VALUE_ENTERED as integer)      as year_inc,
    nullif(trim(i.STREET1), '')                         as street1,
    nullif(trim(i.STREET2), '')                         as street2,
    upper(nullif(trim(i.CITY), ''))                     as city,
    upper(nullif(trim(i.STATEORCOUNTRY), ''))           as state,
    nullif(regexp_extract(coalesce(i.ZIPCODE, ''), '^\s*(\d{5})', 1), '') as zip5,
    list_transform(list_filter([i.ISSUER_PREVIOUSNAME_1, i.ISSUER_PREVIOUSNAME_2, i.ISSUER_PREVIOUSNAME_3,
                 i.EDGAR_PREVIOUSNAME_1, i.EDGAR_PREVIOUSNAME_2, i.EDGAR_PREVIOUSNAME_3],
                x -> nullif(trim(x), '') is not null and upper(trim(x)) not in ('NONE', 'N/A')),
                   x -> unquote(trim(x)))
                                                        as previous_names,

    -- Filled by 20_offerings.sql
    null::varchar                                       as offering_id,
    null::varchar                                       as link_method
from raw_submission s
join offering o on o.ACCESSIONNUMBER = s.ACCESSIONNUMBER
join issuer i   on i.ACCESSIONNUMBER = s.ACCESSIONNUMBER
left join exemptions e on e.ACCESSIONNUMBER = s.ACCESSIONNUMBER
where coalesce(upper(trim(s.TESTORLIVE)), 'LIVE') = 'LIVE'
qualify row_number() over (partition by s.ACCESSIONNUMBER order by s.source_quarter desc) = 1;
