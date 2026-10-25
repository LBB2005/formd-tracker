-- Shared macros: value parsing (10_filings) and name parsing (30_classify).
-- Kept separate so tests can load them without the rest of the build.

-- 2019q1-2020q2 files use '2019-03-29 17:29:14'; later files use '30-SEP-2020'.
create or replace macro parse_date(s) as coalesce(
    try_strptime(trim(s), '%d-%b-%Y'),
    try_strptime(trim(s), '%Y-%m-%d %H:%M:%S'),
    try_strptime(trim(s), '%Y-%m-%d'),
    try_strptime(trim(s), '%m/%d/%Y')
)::date;

-- Amounts arrive as text; 'Indefinite' and blanks become NULL.
create or replace macro parse_amount(s) as
    try_cast(nullif(regexp_replace(trim(s), '[$,]', '', 'g'), '') as double);

create or replace macro parse_bool(s) as lower(trim(s)) = 'true';

-- Roman numerals I..L, and spelled-out numbers ("Wing Strategic Partners Four").
create or replace macro romans() as [
    'i','ii','iii','iv','v','vi','vii','viii','ix','x','xi','xii','xiii','xiv','xv','xvi',
    'xvii','xviii','xix','xx','xxi','xxii','xxiii','xxiv','xxv','xxvi','xxvii','xxviii',
    'xxix','xxx','xxxi','xxxii','xxxiii','xxxiv','xxxv','xxxvi','xxxvii','xxxviii','xxxix',
    'xl','xli','xlii','xliii','xliv','xlv','xlvi','xlvii','xlviii','xlix','l'];

create or replace macro number_words() as
    ['one','two','three','four','five','six','seven','eight','nine','ten'];

-- Lowercase, '&' -> 'and', drop parentheticals and "a series of ..." tails, punctuation -> space.
create or replace macro clean_name(n) as trim(regexp_replace(
    regexp_replace(
        regexp_replace(replace(lower(coalesce(n, '')), '&', ' and '), '\([^)]*\)', ' ', 'g'),
        ',?\s+a series of\s.*$', '', 'g'),
    '[^a-z0-9]+', ' ', 'g'));

-- Fund number: first roman numeral, spelled number, or 1-2 digit number that follows
-- another word. "Accel XIV" -> 14, "Andreessen Horowitz Fund IX - AI Infrastructure" -> 9,
-- "01 Advisors 04" -> 4, "Fund I-A" -> 1, "Partners Four" -> 4. Four-digit years are ignored.
-- Longest alternatives first, so "xiv" wins over "x".
create or replace macro fund_number(n) as (
    with t as (
        select regexp_extract(clean_name(n),
            '^\S+(?:\s+\S+)*?\s+(' || array_to_string(list_sort(romans() || number_words(),
                                                                   'DESC'), '|')
            || '|\d{1,2})(?:\s|$)', 1) as tok
    )
    select case
        when tok = '' then null
        when regexp_matches(tok, '^\d+$') then nullif(tok::integer, 0)
        when list_contains(number_words(), tok) then list_position(number_words(), tok)
        else list_position(romans(), tok)
    end from t
);

-- Words that describe the vehicle rather than the manager.
create or replace macro vehicle_words() as
    '\b(fund|funds|parallel|feeder|offshore|onshore|cayman|intermediate|aiv|associates|'
    'affiliates|entrepreneurs|annex|sidecar|side|car|coinvest|co|invest|invt|spv|series|vehicle|'
    'master|qp|lp|l|p|llc|ltd|inc|limited|partnership|lllp|scsp|the|'
    || array_to_string(romans() || number_words(), '|')
    || '|(i{1,3}|iv|vi{0,3}|ix|x)[ab]|\d{1,4}|[a-d])\b';

-- Manager stem: the issuer name up to its fund number, minus vehicle words.
--   "Andreessen Horowitz Fund IX - AI Infrastructure" -> "andreessen horowitz"
--   "01 Advisors 04, L.P." -> "01 advisors" (a leading number is part of the name)
-- SPVs on a platform ("X, a Series of Rhone VC, LP") take the platform as manager.
create or replace macro manager_stem(n) as (
    with base as (
        select case
            when regexp_matches(lower(n), 'a series of')
                then regexp_extract(lower(n), 'a series of\s+(.*)$', 1)
            else n
        end as b
    ),
    protected as (
        -- Prefix a leading number with 'N' so it survives the number-stripping below.
        -- clean_name() output is lowercase, so the uppercase marker can't collide with a
        -- name that really starts with "n" plus a digit ("n2 Venture Capital", "N49P").
        select regexp_replace(clean_name(b), '^(\d+)\b', 'N\1') as c from base
    ),
    cut as (
        select coalesce(nullif(regexp_extract(c,
            '^(.*?)\s+(?:' || array_to_string(list_sort(romans() || number_words(), 'DESC'), '|')
            || '|\d{1,2})(?:\s|$)', 1),
            ''), c) as c
        from protected
    )
    select regexp_replace(trim(regexp_replace(regexp_replace(c, vehicle_words(), ' ', 'g'),
                                              '\s+', ' ', 'g')), '^N(\d)', '\1')
    from cut
);

-- Generic stems ("capital partners") would merge unrelated managers; those stay per-CIK.
create or replace macro is_generic_stem(s) as
    length(s) < 3 or regexp_full_match(s,
        '((capital|ventures?|partners|vc|management|investments?|group|holdings|equity|'
        'growth|opportunit\w*|global|private|venture|and|of|us|america|american|real|estate)\s*)+');

-- A short code that tells apart a manager's otherwise identical vehicle names, usually one
-- per portfolio company: "AL-D Ribbit Opportunity V, LLC", "FundersClub 6XV LLC". Returns
-- {code, family}, family being the cleaned name without the code; a generic remainder
-- ("ALF-III Capital Partners, LLC") or a platform series doesn't count. Roman numerals ("MVP LS
-- Fund CIII"), 1-2 digit numbers, vintage years and quarter classes ("Fund 8Q") belong to
-- funds, not deals. A trailing token counts only for LLCs: LP series such as "HCM S9Aii LP"
-- were hand-labelled as funds. Rolling-fund classes ("Ravikant Capital, LP - G2") are not
-- codes either; see 30_classify.sql.
create or replace macro vehicle_code(n, entity) as (
    with m as (
        select coalesce(
            case when regexp_matches(n, '^[A-Z0-9]{1,5}-[A-Z0-9]{1,5}\s+\S')
                 then [regexp_extract(n, '^([A-Z0-9]{1,5}-[A-Z0-9]{1,5})\s', 1),
                       regexp_replace(n, '^[A-Z0-9]{1,5}-[A-Z0-9]{1,5}\s+', '')] end,
            case when entity = 'Limited Liability Company'
                  and regexp_matches(n, '\S\s+[A-Z0-9]{1,4},?\s+(LLC|L\.L\.C\.)$')
                 then [regexp_extract(n, '\s([A-Z0-9]{1,4}),?\s+(LLC|L\.L\.C\.)$', 1),
                       regexp_replace(n, '\s[A-Z0-9]{1,4}(,?\s+(LLC|L\.L\.C\.))$', '\1')] end
        ) as cf
    )
    select case when cf is not null
                     and not regexp_full_match(cf[1], '[IVXLCDM]+|\d{1,2}|(19|20)\d\d|\d{1,2}Q')
                     and not regexp_matches(lower(cf[2]), 'a series of')
                     and not is_generic_stem(manager_stem(cf[2]))
                then {'code': cf[1], 'family': clean_name(cf[2])} end
    from m
);

-- The SEC's TSVs wrap some text values in quotes and double the inner ones:
-- "The actual name is ""All Weather, Ltd.""" -> The actual name is "All Weather, Ltd."
create or replace macro unquote(s) as
    case when length(s) >= 2 and s like '"%"' then replace(s[2:-2], '""', '"') else s end;

-- Adviser names (Form ADV) stem like fund names, minus words that describe an adviser:
-- "Kindred Ventures Management, LLC" -> "kindred ventures".
create or replace macro adviser_stem(n) as trim(regexp_replace(manager_stem(n),
    '\b(management|mgmt|advisors|advisers|advisory|adviser|advisor|general partner|gp|'
    'investment|investments|associates|company|services)\b', ' ', 'g'));
