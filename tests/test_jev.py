"""Jev second-labeller logic in pipeline/jev.py (no network)."""

import json
from types import SimpleNamespace

import pipeline.jev as jev


def ai_item(iid, predicted, human=""):
    return {"id": iid, "queue": "ai", "name": iid, "predicted": predicted, "human": human,
            "state": {"company": iid, "domain": ""}}


def fund_item(iid, kind, vehicle, human="", human_vehicle=""):
    return {"id": iid, "queue": "cls", "name": iid, "predicted": kind, "predicted_vehicle": vehicle,
            "human": human, "human_vehicle": human_vehicle, "state": {"issuer_name": iid}}


def answer(label, conf, vehicle=""):
    return {"jev": label, "jev_vehicle": vehicle, "confidence": str(conf)}


def test_matches_compares_vehicle_only_for_funds():
    spv = fund_item("f", "fund", "spv", human="fund", human_vehicle="blind_pool")
    assert jev.matches(spv, answer("fund", 0.9, "spv"), "predicted")
    assert not jev.matches(spv, answer("fund", 0.9, "spv"), "human")
    op = fund_item("o", "operating_company", "")
    assert jev.matches(op, answer("operating_company", 0.9), "predicted")
    assert jev.matches(ai_item("a", "ai"), answer("ai", 0.5), "predicted")


def test_routes_send_disagreements_and_doubts_to_a_human(monkeypatch):
    items = [ai_item("ai-1", "ai"), ai_item("ai-2", "ai"), ai_item("ai-3", "not_ai"),
             ai_item("ai-4", "ai", human="ai")]
    answers = {"ai-1": answer("not_ai", 0.95), "ai-2": answer("ai", 0.6),
               "ai-3": answer("not_ai", 0.97), "ai-4": answer("ai", 0.99)}
    monkeypatch.setattr(jev, "load_items", lambda: items)
    monkeypatch.setattr(jev, "load_answers", lambda: {k: v | {"item_id": k} for k, v in answers.items()})
    monkeypatch.setattr(jev, "BLIND", {"ai": 0, "cls": 0})
    assert jev.routes() == {"ai-1": "disagree", "ai-2": "low_conf", "ai-3": "auto"}


def test_blind_draw_is_seeded_and_only_from_confident_agreements(monkeypatch):
    items = [ai_item(f"ai-{i}", "ai") for i in range(30)]
    monkeypatch.setattr(jev, "load_items", lambda: items)
    monkeypatch.setattr(jev, "load_answers", lambda: {it["id"]: answer("ai", 0.9) for it in items})
    monkeypatch.setattr(jev, "BLIND", {"ai": 5, "cls": 0})
    first, second = jev.routes(), jev.routes()
    assert first == second
    assert list(first.values()).count("blind") == 5 and list(first.values()).count("auto") == 25


def test_ask_never_shows_jev_the_taggers_call(monkeypatch):
    seen = {}

    class FakeClient:
        def system_one(self, state, questions, model):
            seen.update(state=state, questions=questions)
            pick = SimpleNamespace(choice="fund", confidence=0.91, probabilities={"fund": 0.91})
            veh = SimpleNamespace(choice="spv", confidence=0.7, probabilities={"spv": 0.7})
            return SimpleNamespace(model="jev-test", choices={"kind": pick, "vehicle": veh})

    item = fund_item("cls-1", "fund", "blind_pool")
    got = jev.ask(FakeClient(), item)
    sent = json.dumps(seen["state"])
    assert "blind_pool" not in sent and "predicted" not in sent
    assert set(seen["questions"]) == {"kind", "vehicle"}
    assert got["jev"] == "fund" and got["jev_vehicle"] == "spv" and got["confidence"] == 0.91


def test_desk_adds_jev_fields_and_routes(tmp_path, monkeypatch):
    src, dst = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    (src / "c0.json").write_text(json.dumps({"items": [{"id": "ai-1"}, {"id": "ai-2"}]}))
    monkeypatch.setattr(jev, "load_answers", lambda: {"ai-1": answer("ai", 0.8) | {"jev_vehicle": ""}})
    monkeypatch.setattr(jev, "routes", lambda: {"ai-1": "disagree"})
    jev.desk(src, dst)
    out = json.loads((dst / "c0.json").read_text())["items"]
    assert out[0] == {"id": "ai-1", "jev": "ai", "jev_vehicle": None, "jev_conf": 0.8, "route": "disagree"}
    assert out[1] == {"id": "ai-2", "route": None}


def test_focus_routes_rechecks_and_tops_up_to_targets(tmp_path, monkeypatch):
    import csv as _csv
    from pipeline import review
    ai_rows = [{"cik": f"{n:04d}", "predicted": "ai" if n < 6 else "not_ai",
                "human_label": "ai" if n in (0, 6) else ""} for n in range(12)]
    ai_path, cls_path, rc_path = tmp_path / "ai.csv", tmp_path / "cls.csv", tmp_path / "recheck.csv"
    for path, rows in ((ai_path, ai_rows), (cls_path, [{"offering_id": "o1", "reviewed": ""}]),
                       (rc_path, [{"item_id": "ai-0000"}])):
        with open(path, "w", newline="") as fh:
            w = _csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    monkeypatch.setattr(review, "AI_SAMPLE", ai_path)
    monkeypatch.setattr(review, "SAMPLE", cls_path)
    monkeypatch.setattr(review, "RECHECK", rc_path)
    monkeypatch.setattr(review, "TARGET_AI", {"ai": 3, "not_ai": 2})
    monkeypatch.setattr(review, "TARGET_CLS", 1)
    src, dst = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    items = [{"id": f"ai-{r['cik']}", "queue": "ai"} for r in ai_rows] + [{"id": "cls-o1", "queue": "cls"}]
    (src / "c0.json").write_text(json.dumps({"items": items}))
    review.focus(src, dst)
    out = {it["id"]: it for it in json.loads((dst / "c0.json").read_text())["items"]}
    assert out["ai-0000"]["route"] == "recheck" and "recheck_after" in out["ai-0000"]
    assert "route" not in out["ai-0006"]                       # labelled, not re-checked
    todo = [i for i, it in out.items() if it.get("route") == "todo"]
    assert sum(i < "ai-0006" for i in todo if i.startswith("ai")) == 2   # 1 tagged-AI labelled, want 3
    assert sum(i >= "ai-0006" for i in todo if i.startswith("ai")) == 1  # 1 untagged labelled, want 2
    assert out["cls-o1"]["route"] == "todo"
