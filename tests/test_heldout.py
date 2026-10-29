"""Held-out reworded questions: structure and ground-truth validity (no accuracy threshold on purpose)."""

from __future__ import annotations

from src.bi_agent.evaluate import HELDOUT_QUESTIONS
from src.bi_agent.seed import RetailDatabase, seed_all
from src.bi_agent.sql_guard import SQLGuard


def test_heldout_set_is_well_formed() -> None:
    seed_all()
    db, guard = RetailDatabase(), SQLGuard()
    assert len(HELDOUT_QUESTIONS) == 25
    assert len({q["id"] for q in HELDOUT_QUESTIONS}) == 25
    for q in HELDOUT_QUESTIONS:
        res = guard.validate(q["expected_sql"])
        assert res.safe, (q["id"], res.reason)
        _, rows = db.execute_query(res.sanitized_sql)
        assert len(rows) >= 1, q["id"]
        assert isinstance(q["ordered"], bool)


def test_heldout_block_in_eval_json(tmp_path) -> None:
    from src.bi_agent.evaluate import run_evaluation

    h = run_evaluation(output_root=tmp_path)["heldout"]
    assert h["questions_total"] == 25
    assert 0 <= h["questions_correct"] <= 25
    assert h["execution_accuracy_pct"] == round(100.0 * h["questions_correct"] / 25, 2)
    assert len(h["question_results"]) == 25
    assert all("ordered" in r for r in h["question_results"])
    assert h["domain_breakdown"]
