"""Claude tie-break judge in pipeline/claude_judge.py: the quote gate and the spending cap (no network)."""

import json

import pytest

from pipeline import claude_judge as cj

SENT = ("Acme Labs\nAcme builds an AI-powered underwriting engine for small lenders.\n"
        "We also offer a mobile app and partner with OpenAI platform vendors.")


def out(answer, quote, reason="r"):
    return json.dumps({"answer": answer, "quote": quote, "reason": reason})


def test_yes_with_a_verbatim_ai_quote_counts():
    rec = cj.interpret(out("yes", "AI-powered underwriting engine"), SENT, "end_turn")
    assert rec == {"answer": "yes", "quote": "AI-powered underwriting engine",
                   "quote_verified": True, "reason": "r"}


def test_quote_match_ignores_case_and_whitespace():
    rec = cj.interpret(out("yes", "an  ai-powered\nunderwriting   ENGINE"), SENT, "end_turn")
    assert rec["answer"] == "yes" and rec["quote_verified"]
    assert rec["quote"] == "an ai-powered underwriting ENGINE"


def test_yes_with_an_invented_quote_is_not_counted():
    rec = cj.interpret(out("yes", "an AI copilot for lenders"), SENT, "end_turn")
    assert rec["answer"] == "unclear" and not rec["quote_verified"]
    assert rec["quote"] == "an AI copilot for lenders"   # kept for review


def test_yes_with_a_real_quote_but_no_ai_term_is_not_counted():
    rec = cj.interpret(out("yes", "underwriting engine for small lenders"), SENT, "end_turn")
    assert rec["answer"] == "unclear" and not rec["quote_verified"]


def test_quote_must_not_split_a_word():
    # "AI platform" appears only inside "OpenAI platform": not a verbatim AI phrase.
    rec = cj.interpret(out("yes", "AI platform"), SENT, "end_turn")
    assert rec["answer"] == "unclear" and not rec["quote_verified"]


def test_yes_with_an_empty_quote_is_not_counted():
    rec = cj.interpret(out("yes", ""), SENT, "end_turn")
    assert rec["answer"] == "unclear" and not rec["quote_verified"]


def test_refusal_is_unclear_even_with_text():
    rec = cj.interpret(out("yes", "AI-powered underwriting engine"), SENT, "refusal")
    assert rec == {"answer": "unclear", "quote": "", "quote_verified": False, "reason": "no answer"}


@pytest.mark.parametrize("raw", [None, ""])
def test_empty_output_is_unclear(raw):
    assert cj.interpret(raw, SENT, "end_turn")["reason"] == "no answer"


@pytest.mark.parametrize("raw", ['{"answer": "yes", "quote": "AI-pow', "yes", "null", "[1, 2]", '"yes"'])
def test_malformed_or_non_object_json_is_unclear(raw):
    # Truncated output (max_tokens) and valid JSON that isn't an object both land here.
    rec = cj.interpret(raw, SENT, "end_turn")
    assert rec == {"answer": "unclear", "quote": "", "quote_verified": False, "reason": "bad json"}


def test_no_is_kept_whether_or_not_the_quote_checks_out():
    verified = cj.interpret(out("no", "AI-powered underwriting engine"), SENT, "end_turn")
    assert verified["answer"] == "no" and verified["quote_verified"]
    invented = cj.interpret(out("no", "made up"), SENT, "end_turn")
    assert invented["answer"] == "no" and not invented["quote_verified"]


def test_unclear_stays_unclear():
    rec = cj.interpret(out("unclear", ""), SENT, "end_turn")
    assert rec["answer"] == "unclear" and rec["reason"] == "r"


def test_odd_answers_are_normalised():
    assert cj.interpret(out("YES ", "AI-powered underwriting engine"), SENT, "end_turn")["answer"] == "yes"
    assert cj.interpret(out("maybe", "AI-powered underwriting engine"), SENT, "end_turn")["answer"] == "unclear"
    assert cj.interpret(json.dumps({"quote": "x"}), SENT, "end_turn")["answer"] == "unclear"


def test_long_quote_and_reason_are_truncated_after_checking():
    long = "AI-powered underwriting engine for small lenders. " * 2
    sent = SENT + "\n" + long
    rec = cj.interpret(out("yes", long.strip(), "x" * 500), sent, "end_turn")
    assert rec["answer"] == "yes" and len(rec["reason"]) == 300


# ---------- spending cap ----------

def item(cik, chars=2000):
    return (cik, f"Company {cik}", f"c{cik}.com", "a" * chars)


def test_estimate_is_batch_price_with_the_output_assumption():
    est = cj.estimate_usd("Company 1", "c1.com", "a" * 2000)
    floor = 0.5 * cj.EST_OUTPUT_TOKENS * cj.PRICE_OUT / 1e6      # output alone
    assert floor < est < floor + 0.5 * 2000 * cj.PRICE_IN / 1e6   # input is < 1 token/char
    assert cj.estimate_usd("Company 1", "c1.com", "a" * 4000) > est


def test_cap_takes_leading_items_that_fit_and_leaves_the_rest():
    items = [item(str(i)) for i in range(10)]
    each = cj.estimate_usd(*items[0][1:])
    send, est = cj.within_cap(items, cap=each * 3.5)
    assert [c for c, *_ in send] == ["0", "1", "2"]
    assert est == pytest.approx(each * 3)
    assert cj.within_cap(items, cap=1000)[0] == items
    assert cj.within_cap(items, cap=0) == ([], 0.0)


def test_cap_stops_at_the_first_item_that_does_not_fit():
    # Order is kept: a cheap item after an expensive one waits for the next run too.
    items = [item("small"), item("huge", 200_000), item("small2")]
    send, _ = cj.within_cap(items, cap=cj.estimate_usd(*items[0][1:]) * 2.5)
    assert [c for c, *_ in send] == ["small"]


def test_max_usd_reads_the_environment(monkeypatch):
    monkeypatch.delenv("CLAUDE_MAX_USD", raising=False)
    assert cj.max_usd() == 10.0
    monkeypatch.setenv("CLAUDE_MAX_USD", "")          # unset repository variable in CI
    assert cj.max_usd() == 10.0
    monkeypatch.setenv("CLAUDE_MAX_USD", "2.5")
    assert cj.max_usd() == 2.5
    monkeypatch.setenv("CLAUDE_MAX_USD", "ten")
    with pytest.raises(SystemExit, match="CLAUDE_MAX_USD"):
        cj.max_usd()


def test_unsent_companies_are_not_pending(tmp_path, monkeypatch):
    """What the cap leaves out isn't recorded, so ai_tag's next run selects it again."""
    monkeypatch.setattr(cj, "STATE", tmp_path / "claude_batches.json")

    class FakeBatches:
        def create(self, requests):
            return type("Batch", (), {"id": f"msgbatch_{len(requests)}"})()

    class FakeClient:
        messages = type("M", (), {"batches": FakeBatches()})()

    monkeypatch.setattr(cj.anthropic, "Anthropic", FakeClient)
    items = [item(str(i)) for i in range(5)]
    send, _ = cj.within_cap(items, cap=cj.estimate_usd(*items[0][1:]) * 2.5)
    assert cj.submit(send) == ["msgbatch_2"]
    assert cj.pending_ciks() == {"0", "1"}
