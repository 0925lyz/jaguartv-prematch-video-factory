"""Náutico vs Botafogo-SP real-player likeness driver (single-match, Task-1 full flow).

Scoped copy of scripts/run_r3_real_players.py for ONE fixture only:
  manual-20260903-nautico-botafogosp  (Campeonato Brasileiro Série B, 20:00 BRT, SPORTV/PREMIERE)

Differences from the r3 batch driver:
1. RUN_DIR = runs/20260903_nautico (does not collide with runs/20260903_r3).
2. task_id uses the canonical pipeline _task_id(fixture) == fixture_id, so
   _fixture_for / _prediction_block / _captions in pipeline.py resolve correctly
   (the r3 driver's "r3-" prefix broke caption matching).
3. Phase1 writes a scoped selected-fixtures.json (Náutico only).
4. Phase2 reuses runs/20260903_r2/phase2 evidence and overrides the
   player_asset_decision to real_player_likeness (established r3 user preference).
5. --dry-run skips real Image2 / Dreamina / upload calls (placeholder poster,
   looped master hook) so the full wiring + prompts can be validated cheaply.

Reuses every helper from jaguartv_prematch.* — no logic forked.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from PIL import Image  # noqa: E402

from jaguartv_prematch.config import FactoryConfig  # noqa: E402
from jaguartv_prematch.image2 import generate_image2, save_image_route_manifest  # noqa: E402
from jaguartv_prematch.video import (  # noqa: E402
    compose_v7,
    deterministic_batch_rotation,
    download_dreamina_result,
    make_exact_hook,
    make_vertical_master,
    submit_dreamina_hook,
    video_filenames,
)
from jaguartv_prematch.upload import upload_pending_review  # noqa: E402
from jaguartv_prematch.pipeline import (  # noqa: E402
    ROOT,
    _apply_fixed_logo,
    _check_duration,
    _component_pools,
    _date_pt,
    _prediction_block,
    _read_json,
    _sha256,
    _write_json,
    _yyMMdd,
    _zh,
    ANTI_FIGURE,
    _captions,
    _task_id,
)

CONFIG_PATH = REPO / "config/local.json"
RUN_DIR = REPO / "runs/20260903_nautico"
R3_SELECTED = REPO / "runs/20260903_r3/phase1/selected-fixtures.json"
R2_RESEARCH_DIR = REPO / "runs/20260903_r2/phase2"
TODAY_BRT = "2026-09-03"
NAUTICO_FIXTURE_ID = "manual-20260903-nautico-botafogosp"


# Real-player picks (established r3 preference: photorealistic likeness INSPIRED BY
# these named players, not an exact photo reproduction; operator verifies XI pre-publish).
PLAYERS = {
    "Náutico": ["Paulo Sérgio", "Jean Carlos"],
    "Botafogo-SP": ["Edson Carioca", "Alexandre Tam"],
}
KITS = {
    "Náutico": "red shirt with blue and white trim home kit",
    "Botafogo-SP": "black and white striped shirt home kit",
}


PLAYER_PROMPT_TEMPLATE = (
    "Use case: ads-marketing. "
    "Asset type: JaguarTV pre-match single-game prediction poster, 4:5 portrait PNG, "
    "final composition will be 2048x2560. "
    "Primary request: Create one premium Image2-generated cinematic background for {HOME} vs {AWAY}, "
    "{COMP}, {DATE} at {TIME} Brasília Time, broadcast on {CHANNELS}. "
    "Image2 background only — absolutely no readable text, no digits, no typographic shapes, "
    "no pseudo-words, no UI panels, no banners, no scoreboards, no logos, no crests, no sponsor marks, "
    "no watermark. Do not write competition, time, date, team names, channels, scores or percentages anywhere. "
    "Scene: {STYLE}. "
    "Left side: photorealistic likeness of {P_HOME_A} and {P_HOME_B} in {HOME_SHORT} current official "
    "home kit ({KIT_HOME}), both waist-up, facing forward/three-quarter toward the camera with focused "
    "pre-match expressions. "
    "Right side: photorealistic likeness of {P_AWAY_A} and {P_AWAY_B} in {AWAY_SHORT} current official "
    "away kit ({KIT_AWAY}), both waist-up, mirrored, facing the home pair in a classic pre-match "
    "confrontation pose. "
    "Composition: frame all four players waist-up at the extreme outer edges — home pair x 0-20% of "
    "image width, away pair x 80-100% of image width. Their heads and faces must sit between 36% and "
    "50% of image height. The central vertical band (x 20%-80%) between 35% and 65% of height must "
    "contain only clean stadium atmosphere — pitch, stands, floodlights, haze, smoke. No face, head, "
    "hair, shoulder, arm, hand or body part may enter the central band, the top overlay area (top 34%) "
    "or the lower overlay area (below 66%). Leave clean negative space at the top for the competition/date "
    "overlays, in the centre for VS + both crests, in a mid-lower strip for channel logos, and in the "
    "lower third for a single prediction panel. Do NOT draw any of these areas as text boxes or screen graphics. "
    "Deterministic visible pt-BR copy to be overlaid later (do NOT generate): \"{COMP}\"; \"{STAGE}\"; \"{HOME}\"; "
    "\"VS\"; \"{AWAY}\"; \"{DATE}\"; \"{TIME}\"; \"HORÁRIO DE BRASÍLIA\"; \"CANAIS\"; \"PREVISÃO JAGUARTV\"; "
    "\"PREVISÃO DE PLACAR: {PREDICTION}\"; \"{PROBABILITIES}\"; \"{TAKEAWAY}\". "
    "Negative constraints: no generated text, no background writing, no black translucent boxes outside the "
    "single bottom prediction panel, no betting advice, no odds, no 18+, no disclaimer, no responsible-gambling "
    "copy, no Downloader footer, no Chinese, no English explanatory body text, no invented slogans, no multiple "
    "prediction boxes, no crest above/on a player's head, no text over a player's face/body, no readable brand "
    "wordmarks or channel wordmarks (the JaguarTV brand mark and every channel logo are added programmatically). "
    "QA: 4:5 portrait PNG, nonblank, readable on mobile, correct match mapping, exact date incl. year, exact "
    "Brasília time, exact channels, one continuous prediction panel, no prohibited betting/disclaimer copy, "
    "exact JaguarTV brand mark upper-right, faces fully outside any overlay zone. {ANTI_FIGURE}"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_prompt(fixture: dict) -> str:
    home, away = str(fixture["home_team"]), str(fixture["away_team"])
    home_short = home.split()[-1] if " " in home else home
    away_short = away.split()[-1] if " " in away else away
    p_home = PLAYERS.get(home, ["a Brazilian forward", "a Brazilian midfielder"])
    p_away = PLAYERS.get(away, ["a Brazilian forward", "a Brazilian midfielder"])
    comp_full = str(fixture.get("competition") or "")
    comp = comp_full.split("—")[0].strip() if "—" in comp_full else comp_full
    stage = comp_full.split("—")[-1].strip() if "—" in comp_full else ""
    style = (
        "Itaipava Arena Pernambuco night under Náutico red-blue-white cinematic grade, vibrant Recife "
        "floodlights, electric haze, crowd glow"
        if "Náutico" in home
        else "Arena do Grêmio night under blue-black-white cinematic grade, electric haze, crowd glow, floodlights"
    )
    return PLAYER_PROMPT_TEMPLATE.format(
        HOME=home, AWAY=away, HOME_SHORT=home_short, AWAY_SHORT=away_short,
        P_HOME_A=p_home[0], P_HOME_B=p_home[1], P_AWAY_A=p_away[0], P_AWAY_B=p_away[1],
        KIT_HOME=KITS.get(home, "current official home kit"),
        KIT_AWAY=KITS.get(away, "current official away kit"),
        COMP=comp, STAGE=stage, DATE=_date_pt(fixture["schedule_date"]),
        TIME=fixture["kickoff_at_brt"], CHANNELS=", ".join(fixture.get("channels") or []),
        STYLE=style,
        PREDICTION=_prediction_block(fixture, RUN_DIR).get("score") or "",
        PROBABILITIES=_prediction_block(fixture, RUN_DIR).get("probabilities") or "",
        TAKEAWAY=_prediction_block(fixture, RUN_DIR).get("tactical") or "",
        ANTI_FIGURE=ANTI_FIGURE,
    )


def _make_placeholder(poster: Path) -> None:
    poster.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1024, 1280), (12, 40, 92)).save(poster)


def _run_date(run_dir: Path) -> str:
    sel = _read_json(run_dir / "phase1" / "selected-fixtures.json")
    return str(sel.get("date") or TODAY_BRT)


def main(dry_run: bool = False) -> int:
    config = FactoryConfig.load(CONFIG_PATH)

    # --- Phase1: scoped single fixture ---
    r3_selected = _read_json(R3_SELECTED)
    nautico = next(
        (fx for fx in r3_selected["fixtures"] if fx["fixture_id"] == NAUTICO_FIXTURE_ID),
        None,
    )
    if nautico is None:
        raise RuntimeError(f"Fixture {NAUTICO_FIXTURE_ID} not found in {R3_SELECTED}")
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    phase1 = RUN_DIR / "phase1"
    _write_json(phase1 / "selected-fixtures.json", {
        "date": TODAY_BRT,
        "timezone_label": "Horário de Brasília",
        "source": "manual_fixture_file",
        "fixtures": [nautico],
    })

    task_id = _task_id(nautico)
    home, away = str(nautico["home_team"]), str(nautico["away_team"])

    # --- Phase2: reuse r2 evidence + real-player decision override ---
    phase2 = RUN_DIR / "phase2"
    existing = R2_RESEARCH_DIR / f"{task_id}_research.json"
    base = json.loads(existing.read_text(encoding="utf-8")) if existing.is_file() else {}
    base["player_asset_decision"] = {
        "mode": "real_player_likeness",
        "predicted_xi_home": PLAYERS.get(home, []),
        "predicted_xi_away": PLAYERS.get(away, []),
        "current_kit_home": KITS.get(home),
        "current_kit_away": KITS.get(away),
        "likeness_generation": "Image2 photorealistic likeness INSPIRED BY the named players (master prompt PART A.1), not an exact photo reproduction",
        "commercial_use_basis": (
            "user_explicit_override_2026-09-03 — the operator explicitly authorised real-player likeness "
            "imagery for this Náutico run. The likeness is photorealistic-inspired rather than an exact photo "
            "reproduction. Operator MUST verify the predicted XI against pre-match press before publish."
        ),
        "operator_verification_required": True,
        "verified_brazilian_players": PLAYERS.get(home, []) + PLAYERS.get(away, []),
        "licensed_player_imagery_available": False,
        "policy_reference": "User instruction (priority 1) overrides the conservative anonymous-only CREST_SAFETY default",
    }
    base["retrieval_time"] = _now()
    _write_json(phase2 / f"{task_id}_research.json", base)
    _write_json(phase2 / "research-manifest.json", {
        "ok": True, "status": "PHASE2_COMPLETE", "research_count": 1,
        "records": [{"fixture_id": task_id, "path": str((phase2 / f'{task_id}_research.json').resolve()), "dry_run": dry_run}],
        "user_override": "Náutico real-player likeness — CREST_SAFETY anonymity clause overridden per explicit user request",
    })

    # --- Phase3: poster (real Image2 or placeholder in dry-run) ---
    phase3 = RUN_DIR / "phase3"
    prompt = _build_prompt(nautico)
    (phase3 / "prompts").mkdir(parents=True, exist_ok=True)
    (phase3 / "prompts" / f"{task_id}.txt").write_text(prompt, encoding="utf-8")

    filename = f"{_zh(home)}_vs_{_zh(away)}_{_yyMMdd(nautico['schedule_date'])}_海报_nautico.png"
    poster = phase3 / "posters" / filename
    if dry_run:
        _make_placeholder(poster)
        route = [{"provider_id": "dry-run", "model_id": config.data["image"]["model_id"], "status": "not_called"}]
    else:
        attempts = generate_image2(prompt, poster, config.data["image"])
        save_image_route_manifest(phase3 / "image-routes" / f"{task_id}.json", attempts)
        route = [a.__dict__ for a in attempts]
    logo_overlay = _apply_fixed_logo(poster)
    _write_json(phase3 / "poster-manifest.json", {
        "ok": True, "status": "PHASE3_COMPLETE", "poster_count": 1,
        "items": [{
            "task_id": task_id, "kind": "single", "prompt": str(phase3 / "prompts" / f"{task_id}.txt"),
            "poster": str(poster), "fixed_logo_overlay": logo_overlay, "channel_logo_overlay": None,
            "image_route": route, "players_home": PLAYERS.get(home, []), "players_away": PLAYERS.get(away, []),
        }],
        "logo_strategy": "Right-corner official JaguarTV mark only (12% image width). NO additional logo overlay on the video canvas.",
    })

    # --- Phase4: vertical master + Dreamina hook + compose 12s ---
    phase4 = RUN_DIR / "phase4"
    pools = _component_pools(ROOT)
    rotations = deterministic_batch_rotation(_run_date(RUN_DIR), [task_id], pools)
    poster_path = Path(poster)
    names = video_filenames(poster_path, int(config.data["video"].get("generation_seconds", 4)), 1)
    out = phase4 / names["media_stem"]
    master = out / names["master"]
    hook = out / names["hook"]
    final = out / names["final"]
    cover = out / names["cover"]

    master_info = make_vertical_master(poster_path, master)
    Image.open(master).save(cover, "JPEG", quality=92)

    motion_prompt = (
        f"Animate this exact 9:16 JaguarTV pre-match master for 4 seconds: {master.name}. "
        "Keep the poster-cover composition visible as the dominant full-frame subject throughout the first "
        "3 seconds; do not treat it as a single-frame flash. "
        "Preserve all Brazilian Portuguese text, player identity, club kit, crests, channel icons, date, "
        "kickoff time, prediction, and the upper-right JaguarTV logo. The upper-right JaguarTV logo is the "
        "ONLY logo present and is already baked into the poster — do NOT add any other logo or wordmark anywhere in the video frame. "
        "Make the poster background visibly alive with stadium lights, crowd depth, sparks, cloth movement, "
        "and a fierce face-to-face player confrontation when players are present. "
        "Keep every logo, face, text block, and score readable and fixed in identity; no face obstruction."
    )
    (out / "motion-prompt.txt").write_text(motion_prompt, encoding="utf-8")

    if dry_run:
        from jaguartv_prematch.pipeline import _loop_video
        raw = out / names["raw_video"]
        _loop_video(master, raw, int(config.data["video"].get("generation_seconds", 4)))
        dreamina = {"provider_id": "dry-run", "task_id": None, "raw_video": str(raw)}
    else:
        dreamina_task = submit_dreamina_hook(master, motion_prompt, config.data["video"])
        raw = download_dreamina_result(dreamina_task, out, config.data["video"].get("dreamina_command", "dreamina"))
        dreamina = {"provider_id": config.data["video"]["provider_id"], "model_id": config.data["video"]["model_id"], "task_id": dreamina_task, "raw_video": str(raw)}
    make_exact_hook(master, raw, hook)
    compose_v7(ROOT, master=master, hook=hook, output=final, components=rotations[task_id])
    _check_duration(final, 12.0)

    item = {
        "task_id": task_id, "kind": "single", "sequence": 1,
        "poster": str(poster_path), "master": str(master), "hook": str(hook),
        "final": str(final), "cover": str(cover), "master_info": master_info, "dreamina": dreamina,
        "components": rotations[task_id],
        "generation_policy": {"generated": "poster + 3s hook only", "reused": "3-9s operation/cta/music/voice inventory", "final_seconds": 12},
    }
    captions = _captions(RUN_DIR, [item])
    _write_json(phase4 / "captions.json", captions)
    _write_json(phase4 / "build-manifest.json", {
        "ok": True, "status": "PHASE4_COMPLETE", "video_count": 1, "items": [item], "captions": str(phase4 / "captions.json"),
    })

    # --- Phase5: upload (skipped in dry-run) ---
    phase5 = RUN_DIR / "phase5"
    if dry_run:
        _write_json(phase5 / "upload-manifest.json", {
            "ok": True, "status": "DRY_RUN_NOT_UPLOADED", "upload_count": 0, "would_upload": [str(final)],
        })
        print(json.dumps({"ok": True, "dry_run": True, "run_dir": str(RUN_DIR), "task_id": task_id, "final": str(final)}, ensure_ascii=False, indent=2))
        return 0

    publish = config.data["publishing"]
    metadata = {
        "category": "pre_match_prediction",
        "match_name": f"{home} vs {away}",
        "match_date": nautico["schedule_date"],
        "match_time_sao_paulo": f"{nautico['schedule_date']}T{nautico['kickoff_at_brt']}:00-03:00",
        "competition": nautico["competition"],
        "home_team": home, "away_team": away,
        "channels": nautico.get("channels") or ["Jaguar TV"],
        "kickoff_at_brt": nautico["kickoff_at_brt"],
        "match_info": {"content_category": "赛前预测", "task_id": task_id, "timezone_label": "Horário de Brasília"},
        "metadata": {"artifact_revision": _sha256(final), "run_dir": str(RUN_DIR), "batch": "nautico-real-player",
                     "players_home": PLAYERS.get(home, []), "players_away": PLAYERS.get(away, [])},
    }
    upload = upload_pending_review(
        Path(final), metadata,
        base_url=config.env_value(publish, "dashboard_url_env"),
        upload_token=config.env_value(publish, "upload_token_env"),
        dashboard_token=config.env_value(publish, "dashboard_token_env"),
    )
    _write_json(phase5 / "upload-manifest.json", {
        "ok": True, "status": "PHASE5_COMPLETE", "upload_count": 1,
        "uploads": [{"task_id": task_id, **upload}],
        "logo_strategy": "right-upper JaguarTV mark only",
    })
    print(json.dumps({"ok": True, "run_dir": str(RUN_DIR), "task_id": task_id, "upload": upload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(dry_run="--dry-run" in sys.argv))
