"""Website-layer helpers in pipeline/ai_tag.py and pipeline/web.py (no network)."""

from pipeline.ai_tag import candidate_domains, core, distinctive, norm, score_page, verify
from pipeline.web import _read_cache, _write_cache, parse_page, valid_domain


def test_name_keys():
    assert norm("Harvey AI Corp") == "harveyai"
    assert norm("X.AI Holdings Corp.") == "xai"
    assert core("Acme Robotics Technologies, Inc.") == "acme"
    assert distinctive("Acme Robotics Technologies, Inc.") == "acme"


def test_candidate_domains_prefer_yc_then_guesses():
    got = [d for d, _ in candidate_domains("Lumen Labs, Inc.", "https://www.lumen.dev/about")]
    assert got[0] == "lumen.dev"
    assert "lumenlabs.com" in got and "lumen.ai" in got and len(got) <= 12


HOMEPAGE = """<html><head><title>Lumen | AI agents for freight</title>
<meta name="description" content="Lumen builds AI agents that book freight."></head>
<body><script>var ai = 1;</script><h1>Freight, automated</h1><p>Book loads in seconds.</p></body></html>"""


def test_parse_page_skips_scripts_and_reads_hero():
    page = parse_page(HOMEPAGE)
    assert page.title == "Lumen | AI agents for freight"
    assert "AI agents that book freight" in page.description
    assert page.headings == ["Freight, automated"]
    assert "var ai" not in page.text


def test_verify_requires_name_in_hero_and_rejects_parked_pages():
    assert verify("Lumen Labs, Inc.", parse_page(HOMEPAGE))
    shop = parse_page("<title>My Store</title><body>Lumen lamps on sale</body>")
    assert not verify("Lumen Labs, Inc.", shop)
    parked = parse_page("<title>lumen.com</title><body>This domain is for sale!</body>")
    assert not verify("Lumen Labs, Inc.", parked)


def test_score_page_hero_terms_and_ai_domain_ownership():
    page = parse_page(HOMEPAGE)
    assert score_page("lumen.com", page, "Lumen Labs, Inc.")[0] == "strong"
    plain = parse_page("<title>Humans</title><h1>People first</h1><p>We help teams.</p>")
    # A guessed .ai domain for a name without "AI" is someone else's until proven otherwise.
    assert score_page("humans.ai", plain, "Humans, Inc.", "guess")[0] == "weak"
    assert score_page("humans.ai", plain, "Humans AI, Inc.", "guess")[0] == "strong"
    assert score_page("humans.ai", plain, "Humans, Inc.", "yc")[0] == "strong"
    assert score_page("humans.com", plain, "Humans, Inc.")[0] == "none"


def test_invalid_domains_are_never_candidates():
    long = "tremayne antonio williams estate private jurisdiction tribunal irrevocable trust and us government"
    assert not valid_domain(long.replace(" ", "") + ".com")
    assert all(valid_domain(d) for d, _ in candidate_domains(long, None))


def test_corrupt_cache_is_a_miss_and_writes_are_atomic(tmp_path):
    p = tmp_path / "x.json.gz"
    p.write_bytes(b"\x1f\x8b\x08 half-written")
    assert _read_cache(p) is None
    _write_cache(p, {"fetched_at": 9e12, "html": "<p>ok</p>"})
    assert _read_cache(p)["html"] == "<p>ok</p>"
    assert not list(tmp_path.glob("*.tmp"))
