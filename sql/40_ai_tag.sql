-- AI tagging (Week 3). Definition, written before labelling:
--   An AI company is one whose core product is an AI or ML model, or is built on one.
--   Mentioning AI features does not qualify.
--
-- Evidence comes from independent sources, one row per signal in ai_signals:
--   name     issuer name or a former name (this file)
--   website  homepage text and domain (pipeline/ai_tag.py, later in Week 3)
--   yc       Y Combinator directory tags (pipeline/ai_tag.py)
--   llm      model tie-break on ambiguous homepages (pipeline/ai_tag.py)
-- Python layers write to ai_signals_external, which survives rebuilds.
--
-- Tier:
--   strong   strong signals from 2+ independent sources
--   likely   one strong signal
--   possible weak signals only ("Neural", "Intelligence", "Cognition")

create table if not exists ai_signals_external (
    cik varchar, source varchar, strength varchar, evidence varchar
);

create or replace temp table _names as
select cik, name, false as is_former from issuers
union all
select cik, unnest(former_names), true from issuers;

create or replace table ai_signals as
with rules(rule, pattern, case_sensitive, strength) as (
    values
        ('ai word',                 '(?i)\bai\b',                          false, 'strong'),
        ('a.i.',                    '(?i)\ba\.i\.',                        false, 'strong'),
        ('.ai name',                '(?i)\w\.ai\b',                        false, 'strong'),
        ('camelcase ai',            '[a-z]AI\b',                           true,  'strong'),
        ('artificial intelligence', '(?i)artificial intelligence',         false, 'strong'),
        ('machine learning',        '(?i)machine learning|deep learning',  false, 'strong'),
        ('llm / gpt',               '(?i)\bllms?\b|gpt',                   false, 'strong'),
        ('agentic',                 '(?i)\bagentic\b',                     false, 'strong'),
        ('neural',                  '(?i)\bneural',                        false, 'weak'),
        ('cognition',               '(?i)\bcognit',                        false, 'weak'),
        ('intelligence',            '(?i)intelligence',                    false, 'weak')
)
select n.cik, 'name' as source, r.strength,
       r.rule || ': ' || n.name || case when n.is_former then ' (former name)' else '' end
           as evidence
from _names n
join rules r on regexp_matches(n.name, r.pattern)
union all
select cik, source, strength, evidence from ai_signals_external;

-- Hand-set tiers (field ai_tier in config/manual_overrides.csv) win over every rule.
create or replace table ai_tags as
with agg as (
    select cik,
           count(distinct source) filter (where strength = 'strong') as strong_sources,
           count(*) filter (where strength = 'weak')                  as weak_signals,
           list(evidence order by source, evidence)                   as ai_evidence
    from ai_signals
    group by cik
),
tiered as (
    select cik, ai_evidence,
           case when strong_sources >= 2 then 'strong'
                when strong_sources = 1 then 'likely'
                when weak_signals > 0 then 'possible' end as ai_tier
    from agg
)
select coalesce(t.cik, m.cik) as cik,
       coalesce(m.value, t.ai_tier) as ai_tier,
       t.ai_evidence,
       m.value is not null as ai_tier_overridden
from tiered t
full join (select cik, value from manual_overrides
           where field = 'ai_tier' and offering_id is null) m on m.cik = t.cik;

alter table issuers add column if not exists ai_tier varchar;
alter table issuers add column if not exists ai_evidence varchar[];
update issuers set ai_tier = t.ai_tier, ai_evidence = t.ai_evidence
from ai_tags t where t.cik = issuers.cik;

-- Offerings: AI tag for operating companies. For funds, ai_theme marks AI-themed
-- vehicles ("Fund IX - AI Infrastructure", "Menlo AI Leaders"), a separate question.
alter table offerings add column if not exists ai_tier varchar;
alter table offerings add column if not exists is_ai boolean;
alter table offerings add column if not exists ai_theme boolean;
update offerings set
    ai_tier = i.ai_tier,
    is_ai = kind = 'operating_company' and coalesce(i.ai_tier in ('strong', 'likely'), false),
    ai_theme = kind = 'fund' and coalesce(i.ai_tier in ('strong', 'likely'), false)
from issuers i where i.cik = offerings.cik;

-- Time-correct alternative. Homepage, YC and model evidence describe a company today, so
-- a company that pivoted to AI in 2024 also counts as AI for its 2019 raise. The issuer
-- name as filed on the raise's own filings doesn't leak: ai_name_at_filing uses only that.
alter table offerings add column if not exists ai_name_at_filing boolean;
update offerings set ai_name_at_filing = false;
update offerings set ai_name_at_filing = true
where kind = 'operating_company' and offering_id in (
    select f.offering_id from filings f
    where regexp_matches(f.issuer_name,
        '(?i)(\bai\b|\ba\.i\.|\w\.ai\b|artificial intelligence|machine learning|deep learning|\bllms?\b|gpt|\bagentic\b)')
       or regexp_matches(f.issuer_name, '[a-z]AI\b')
);
