"""Regression tests for caption shortening.

A published caption embeds the tactical research line trimmed to 90 characters.
The original code cut at a word boundary, appended an ellipsis, and then the driver
stripped every trailing dot before adding its own period — so the ellipsis vanished
and readers saw a dangling clause ("...defendendo baixo e apostando nas.").
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jaguartv_prematch.pipeline import _short_text

REPO = Path(__file__).resolve().parents[1]


def _load_driver():
    spec = importlib.util.spec_from_file_location("jtv_task1_driver", REPO / "scripts" / "task1_driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_dir(tmp_path: Path, tactical_pt: str) -> Path:
    run_dir = tmp_path / "20260924_batch1"
    (run_dir / "phase1").mkdir(parents=True)
    (run_dir / "phase2").mkdir(parents=True)
    fixture = {
        "fixture_id": "fx-1",
        "home_team": "Holanda",
        "away_team": "Alemanha",
        "competition": "UEFA Nations League - League A - 1",
        "kickoff_at_brt": "15:45",
        "schedule_date": "2026-09-24",
    }
    (run_dir / "phase1" / "selected-fixtures.json").write_text(
        json.dumps({"fixtures": [fixture]}), encoding="utf-8"
    )
    (run_dir / "phase2" / "fx-1_research.json").write_text(
        json.dumps({
            "schema_version": "jaguartv-prematch-research-v1",
            "fixture_id": "fx-1",
            "source_records": [],
            "records": [],
            "poster_assets": [],
            "projected_or_inferred": [
                {"claim": "Editorial prediction: Holanda 2 x 1 Alemanha.",
                 "claim_pt": "Holanda 2 x 1 Alemanha."},
                {"claim": "Editorial win/draw/loss read: Holanda 40%, draw 26%, Alemanha 34%.",
                 "claim_pt": "Probabilidade: Holanda 40%, empate 26%, Alemanha 34%."},
                {"claim": "Editorial tactical point: press and possession.",
                 "claim_pt": tactical_pt},
            ],
        }),
        encoding="utf-8",
    )
    return run_dir


def test_short_text_leaves_short_values_untouched():
    assert _short_text("Holanda 2 x 1 Alemanha.", 90) == "Holanda 2 x 1 Alemanha."


def test_short_text_prefers_a_complete_sentence_inside_the_budget():
    value = (
        "Holanda deve ter a posse e atacar pelas laterais. "
        "A Alemanha pressiona alto e aposta nos contra-ataques rapidos pelos corredores."
    )
    trimmed = _short_text(value, 90)
    assert trimmed == "Holanda deve ter a posse e atacar pelas laterais."
    assert not trimmed.endswith("...")


def test_short_text_marks_a_mid_clause_cut_with_an_ellipsis():
    value = (
        "Holanda deve ter a posse de bola e amplitude, com Dumfries e Gakpo atacando "
        "pelas laterais, enquanto a Alemanha pressiona alto e explora os espacos"
    )
    trimmed = _short_text(value, 90)
    assert trimmed.endswith("...")
    assert len(trimmed) <= 90


def test_caption_never_ends_a_trimmed_tactical_line_with_a_fake_period(tmp_path):
    driver = _load_driver()
    long_clause = (
        "Holanda deve ter a posse de bola e amplitude, com Dumfries e Gakpo atacando "
        "pelas laterais, enquanto a Alemanha pressiona alto e explora os espacos"
    )
    run_dir = _run_dir(tmp_path, long_clause)
    captions = driver._captions_for_batch(run_dir, [{"task_id": "fx-1", "kind": "single"}], 1)
    description = captions["items"][0]["description"]
    assert long_clause[:40] in description
    # The trimmed quote must stay visibly trimmed instead of posing as a finished sentence.
    assert "espacos." not in description
    assert "..." in description


def test_caption_keeps_a_complete_first_sentence_verbatim(tmp_path):
    driver = _load_driver()
    tactical = (
        "Holanda deve ter a posse e atacar pelas laterais. "
        "A Alemanha pressiona alto e aposta em Musiala entre linhas e Havertz como falso 9."
    )
    run_dir = _run_dir(tmp_path, tactical)
    captions = driver._captions_for_batch(run_dir, [{"task_id": "fx-1", "kind": "single"}], 1)
    description = captions["items"][0]["description"]
    assert "Holanda deve ter a posse e atacar pelas laterais." in description
