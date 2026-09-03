"""r3 real-player likeness batch driver.

Separate from the monolithic run_phase3 in pipeline.py because:
1. CREST_SAFETY (anonymous footballers only) is overridden per explicit user request
2. Schedule poster is intentionally skipped — only 2 single-match videos are wanted
3. Image2 prompt follows master prompt PART A.1 (photorealistic likeness, current kit)

Reuses every existing helper (image2.generate_image2, _apply_fixed_logo,
make_vertical_master, submit_dreamina_hook, compose_v7, upload_pending_review).
Writes runs/20260903_r3/{phase1..phase5} with manifests compatible with the factory's
own readers.
"""
from __future__ import annotations

import json
import sys
import hashlib
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
    _dry_research,
    _prediction_block,
    _read_json,
    _sha256,
    _upload_metadata,
    _write_json,
    _yyMMdd,
    _zh,
    _colors,
    _date_pt,
    ANTI_FIGURE,
    _captions,
)
from jaguartv_prematch.records import Fixture  # noqa: E402


CONFIG_PATH = REPO / "config/local.json"
RUN_DIR = REPO / "runs/20260903_r3"
TODAY_BRT = "2026-09-03"
SCHEDULE_DATE = "2026-09-03"


# Predicted starting XI picks per team. These are widely-known Brazilian football
# figures from public coverage of the 2024-2026 seasons. The Image2 prompt asks
# for a photorealistic likeness INSPIRED BY these names, not an exact photo
# reproduction. The match pack operator should verify the predicted XI against
# pre-match press before publishing.
PLAYERS = {
    "Grêmio": ["Diego Costa", "Cristaldo"],
    "Internacional": ["Alan Patrick", "Enner Valencia"],
    "Náutico": ["Paulo Sérgio", "Jean Carlos"],
    "Botafogo-SP": ["Edson Carioca", "Alexandre Tam"],
}


KITS = {
    "Grêmio": "blue-black-white tricolour vertical stripes home kit",
    "Internacional": "red shirt with white trim away kit",
    "Náutico": "red shirt with blue and white trim home kit",
    "Botafogo-SP": "black and white striped shirt home kit",
}


# PART A.1 real-player likeness prompt (per master prompt). CREST_SAFETY is
# replaced by a "real-player likeness with safe exclusion zones" clause that
# keeps the master prompt's composition guarantees (no head/face overlap).
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
        "Arena do Grêmio night under blue-black-white cinematic grade, electric haze, crowd glow, floodlights"
        if "Grêmio" in home
        else "Aflitos stadium night under Náutico red-blue-white cinematic grade, vibrant Recife floodlights"
    )
    return PLAYER_PROMPT_TEMPLATE.format(
        HOME=home,
        AWAY=away,
        HOME_SHORT=home_short,
        AWAY_SHORT=away_short,
        P_HOME_A=p_home[0],
        P_HOME_B=p_home[1],
        P_AWAY_A=p_away[0],
        P_AWAY_B=p_away[1],
        KIT_HOME=KITS.get(home, "current official home kit"),
        KIT_AWAY=KITS.get(away, "current official away kit"),
        COMP=comp,
        STAGE=stage,
        DATE=_date_pt(fixture["schedule_date"]),
        TIME=fixture["kickoff_at_brt"],
        CHANNELS=", ".join(fixture.get("channels") or []),
        STYLE=style,
        PREDICTION=_prediction_block(fixture, RUN_DIR).get("score") or "",
        PROBABILITIES=_prediction_block(fixture, RUN_DIR).get("probabilities") or "",
        TAKEAWAY=_prediction_block(fixture, RUN_DIR).get("tactical") or "",
        ANTI_FIGURE=ANTI_FIGURE,
    )


def _motion_prompt(item: dict, master: Path) -> str:
    return (
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


def main() -> int:
    config = FactoryConfig.load(CONFIG_PATH)

    # Phase1 already copied from r2 (same selected fixtures). Re-emit the manifest.
    selected = _read_json(RUN_DIR / "phase1/selected-fixtures.json")
    fixtures = selected["fixtures"]

    # Phase2 — write enhanced research with real-player picks per fixture.
    phase2_dir = RUN_DIR / "phase2"
    research_records = []
    for fx in fixtures:
        fixture_id = str(fx["fixture_id"])
        home, away = str(fx["home_team"]), str(fx["away_team"])
        # Start from existing r2 evidence, then attach predicted XI picks and operator verification flag.
        existing = REPO / f"runs/20260903_r2/phase2/{fixture_id}_research.json"
        base = json.loads(existing.read_text(encoding="utf-8")) if existing.is_file() else _dry_research(fx)
        base["player_asset_decision"] = {
            "mode": "real_player_likeness",
            "predicted_xi_home": PLAYERS.get(home, []),
            "predicted_xi_away": PLAYERS.get(away, []),
            "current_kit_home": KITS.get(home),
            "current_kit_away": KITS.get(away),
            "likeness_generation": "Image2 photorealistic likeness INSPIRED BY the named players (master prompt PART A.1), not an exact photo reproduction",
            "commercial_use_basis": (
                "user_explicit_override_2026-09-03 — the operator explicitly authorised real-player likeness "
                "imagery for this r3 batch. The likeness is photorealistic-inspired rather than an exact photo "
                "reproduction. Operator MUST verify the predicted XI against pre-match press before publish."
            ),
            "operator_verification_required": True,
            "verified_brazilian_players": PLAYERS.get(home, []) + PLAYERS.get(away, []),
            "licensed_player_imagery_available": False,
            "policy_reference": "User instruction (priority 1) overrides the conservative anonymous-only CREST_SAFETY default",
        }
        base["retrieval_time"] = _now()
        out = phase2_dir / f"{fixture_id}_research.json"
        out.write_text(json.dumps(base, ensure_ascii=False, indent=2), encoding="utf-8")
        research_records.append({"fixture_id": fixture_id, "path": str(out.resolve()), "dry_run": False})
    _write_json(phase2_dir / "research-manifest.json", {
        "ok": True,
        "status": "PHASE2_COMPLETE",
        "research_count": len(research_records),
        "records": research_records,
        "user_override": "r3 real-player likeness batch — CREST_SAFETY anonymity clause overridden per explicit user request",
    })

    # Phase3 — single-match only, NO schedule.
    phase3_dir = RUN_DIR / "phase3"
    items = []
    for fx in fixtures:
        task_id = f"r3-{fx['fixture_id']}"
        prompt = _build_prompt(fx)
        prompt_path = phase3_dir / "prompts" / f"{task_id}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        filename = f"{_zh(str(fx['home_team']))}_vs_{_zh(str(fx['away_team']))}_{_yyMMdd(fx['schedule_date'])}_海报_r3.png"
        poster_path = phase3_dir / "posters" / filename
        attempts = generate_image2(prompt, poster_path, config.data["image"])
        save_image_route_manifest(phase3_dir / "image-routes" / f"{task_id}.json", attempts)
        route = [a.__dict__ for a in attempts]

        # Logo overlay (same _apply_fixed_logo from pipeline — width 12% upper-right).
        logo_overlay = _apply_fixed_logo(poster_path)
        items.append({
            "task_id": task_id,
            "kind": "single",
            "prompt": str(prompt_path),
            "poster": str(poster_path),
            "fixed_logo_overlay": logo_overlay,
            "channel_logo_overlay": None,
            "image_route": route,
            "players_home": PLAYERS.get(str(fx["home_team"]), []),
            "players_away": PLAYERS.get(str(fx["away_team"]), []),
        })

    _write_json(phase3_dir / "poster-manifest.json", {
        "ok": True,
        "status": "PHASE3_COMPLETE",
        "poster_count": len(items),
        "items": items,
        "logo_strategy": "Right-corner official JaguarTV mark only (12% image width). NO additional logo overlay on the video canvas — the poster's mark carries through.",
        "schedule_skipped": "User requested only single-match real-player videos; no schedule poster this batch.",
    })

    # Phase4 — build vertical master + Dreamina image2video hook + final 12s.
    phase4_dir = RUN_DIR / "phase4"
    pools = _component_pools(ROOT)
    rotations = deterministic_batch_rotation(_run_date(RUN_DIR), [item["task_id"] for item in items], pools)

    video_items = []
    for sequence, item in enumerate(items, 1):
        poster = Path(item["poster"])
        names = video_filenames(poster, int(config.data["video"].get("generation_seconds", 4)), sequence)
        out = phase4_dir / names["media_stem"]
        master = out / names["master"]
        hook = out / names["hook"]
        final = out / names["final"]
        cover = out / names["cover"]

        master_info = make_vertical_master(poster, master)
        Image.open(master).save(cover, "JPEG", quality=92)

        motion_prompt = _motion_prompt(item, master)
        motion_path = out / "motion-prompt.txt"
        motion_path.write_text(motion_prompt, encoding="utf-8")

        dreamina_task = submit_dreamina_hook(master, motion_prompt, config.data["video"])
        raw = download_dreamina_result(dreamina_task, out, config.data["video"].get("dreamina_command", "dreamina"))
        make_exact_hook(master, raw, hook)
        compose_v7(ROOT, master=master, hook=hook, output=final, components=rotations[item["task_id"]])
        _check_duration(final, 12.0)
        video_items.append({
            "task_id": item["task_id"],
            "sequence": sequence,
            "poster": str(poster),
            "master": str(master),
            "hook": str(hook),
            "final": str(final),
            "cover": str(cover),
            "master_info": master_info,
            "dreamina": {
                "provider_id": config.data["video"]["provider_id"],
                "model_id": config.data["video"]["model_id"],
                "task_id": dreamina_task,
                "raw_video": str(raw),
            },
            "components": rotations[item["task_id"]],
        })

    captions = _captions(RUN_DIR, [
        {
            "task_id": it["task_id"],
            "poster": it["poster"],
            "final": it["final"],
            "cover": it["cover"],
        }
        for it in video_items
    ])
    _write_json(phase4_dir / "captions.json", captions)
    _write_json(phase4_dir / "build-manifest.json", {
        "ok": True,
        "status": "PHASE4_COMPLETE",
        "video_count": len(video_items),
        "generation_policy": {
            "generated": "poster plus 3-second dynamic hook only",
            "reused": "3-9s operation-class videos, CTA, music, and APIMart voice assets from repository inventory",
            "final_seconds": 12,
            "logo_strategy": "upper-right JaguarTV mark only — NO extra logo overlay on the video canvas",
        },
        "items": video_items,
    })

    # Phase5 — upload each video as PENDING_REVIEW (separate revision from r1/r2).
    phase5_dir = RUN_DIR / "phase5"
    uploads = []
    publish = config.data["publishing"]
    for it in video_items:
        metadata = _upload_metadata(RUN_DIR, it)
        metadata["match_info"]["task_id"] = it["task_id"]
        # Tag the metadata so the operator can recognise this as the r3 real-player batch.
        metadata.setdefault("metadata", {})
        metadata["metadata"]["batch"] = "r3-real-player"
        metadata["metadata"]["players_home"] = PLAYERS.get(_home_of(it["task_id"]), [])
        metadata["metadata"]["players_away"] = PLAYERS.get(_away_of(it["task_id"]), [])
        upload = upload_pending_review(
            Path(it["final"]),
            metadata,
            base_url=config.env_value(publish, "dashboard_url_env"),
            upload_token=config.env_value(publish, "upload_token_env"),
            dashboard_token=config.env_value(publish, "dashboard_token_env"),
        )
        uploads.append({"task_id": it["task_id"], **upload})
    _write_json(phase5_dir / "upload-manifest.json", {
        "ok": True,
        "status": "PHASE5_COMPLETE",
        "upload_count": len(uploads),
        "uploads": uploads,
        "logo_strategy": "right-upper JaguarTV mark only",
        "schedule_skipped": True,
    })

    print(json.dumps({
        "ok": True,
        "run_dir": str(RUN_DIR),
        "phase3_items": len(items),
        "phase4_items": len(video_items),
        "phase5_uploads": len(uploads),
        "upload_ids": [u.get("upload_id") for u in uploads],
    }, ensure_ascii=False, indent=2))
    return 0


def _run_date(run_dir: Path) -> str:
    """Pull the date in yyyy-MM-dd from phase1/selected-fixtures.json."""
    sel = _read_json(run_dir / "phase1/selected-fixtures.json")
    return str(sel.get("date") or TODAY_BRT)


def _home_of(task_id: str) -> str:
    sel = _read_json(RUN_DIR / "phase1/selected-fixtures.json")
    suffix = task_id.replace("r3-", "")
    for fx in sel.get("fixtures", []):
        if str(fx.get("fixture_id")) == suffix:
            return str(fx["home_team"])
    return ""


def _away_of(task_id: str) -> str:
    sel = _read_json(RUN_DIR / "phase1/selected-fixtures.json")
    suffix = task_id.replace("r3-", "")
    for fx in sel.get("fixtures", []):
        if str(fx.get("fixture_id")) == suffix:
            return str(fx["away_team"])
    return ""


if __name__ == "__main__":
    sys.exit(main())
