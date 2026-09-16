from __future__ import annotations

import hashlib
import io
import re
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


W, H = 2048, 2560
GOLD = (250, 205, 70, 255)
WHITE = (255, 255, 255, 255)

CHANNEL_ALIASES = {
    "PRIME VIDEO": "Prime_Video", "AMAZON PRIME": "Prime_Video",
    "SPORTV": "SporTV", "PREMIERE": "Premiere", "TV GLOBO": "TV_Globo",
    "GLOBO": "TV_Globo", "DISNEY+": "Disney_Plus", "DISNEY PLUS": "Disney_Plus",
    "HBO MAX": "HBO_Max", "CANAL GOAT": "Canal_GOAT", "GOAT": "Canal_GOAT",
    "ONEFOOTBALL": "OneFootball_PPV", "PPV ONEFOOTBALL": "OneFootball_PPV",
    "APPLE TV": "Apple_TV", "GE TV": "Ge_TV", "CAZÉTV": "CazeTV",
    "CAZETV": "CazeTV", "TV CULTURA": "TV_Cultura",
}


def _font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ):
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit(draw: ImageDraw.ImageDraw, text: str, width: int, start: int, minimum: int) -> ImageFont.ImageFont:
    floor = max(12, min(minimum, 18))
    for size in range(start, floor - 1, -2):
        face = _font(size)
        box = draw.textbbox((0, 0), text, font=face, stroke_width=2)
        if box[2] - box[0] <= width:
            return face
    raise ValueError(f"Poster text cannot fit width {width}: {text[:160]}")


def _wrap(draw: ImageDraw.ImageDraw, text: str, face: ImageFont.ImageFont, width: int) -> list[str]:
    words = str(text).split()
    output: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=face)[2] <= width:
            current = candidate
        elif current:
            output.append(current)
            current = word
        else:
            chunk = ""
            for character in word:
                candidate = chunk + character
                if chunk and draw.textbbox((0, 0), candidate, font=face)[2] > width:
                    output.append(chunk)
                    chunk = character
                else:
                    chunk = candidate
            current = chunk
    if current:
        output.append(current)
    return output


def _fit_wrapped(
    draw: ImageDraw.ImageDraw, text: str, width: int, start: int, minimum: int, max_lines: int,
) -> tuple[ImageFont.ImageFont, list[str]]:
    for size in range(start, minimum - 1, -2):
        face = _font(size)
        wrapped = _wrap(draw, text, face, width)
        if len(wrapped) <= max_lines:
            return face, wrapped
    raise ValueError(f"Poster text does not fit in {max_lines} lines: {text[:160]}")


def _text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, face: ImageFont.ImageFont, fill=WHITE, anchor="mm") -> None:
    draw.text(xy, text, font=face, fill=fill, anchor=anchor, stroke_width=3, stroke_fill=(0, 0, 0, 230))


def _transparent_logo(path: Path, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    pixels = []
    for red, green, blue, _ in image.getdata():
        background = green > 55 and green > red * 1.22 and green > blue * 1.18
        pixels.append((red, green, blue, 0 if background else 255))
    image.putdata(pixels)
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    image.thumbnail(size, Image.Resampling.LANCZOS)
    return image


def _transparent_icon(path: Path, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    for point in ((0, 0), (image.width - 1, 0), (0, image.height - 1), (image.width - 1, image.height - 1)):
        red, green, blue, alpha = image.getpixel(point)
        if alpha > 240 and (max(red, green, blue) < 45 or min(red, green, blue) > 225):
            ImageDraw.floodfill(image, point, (red, green, blue, 0), thresh=34)
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    image.thumbnail(size, Image.Resampling.LANCZOS)
    return image


def _fixture_crest_url(fixture: dict[str, Any], side: str) -> str:
    """Return only the provider-supplied official crest URL for one team."""
    raw = fixture.get("raw") if isinstance(fixture.get("raw"), dict) else {}
    teams = raw.get("teams") if isinstance(raw.get("teams"), dict) else {}
    team = teams.get(side) if isinstance(teams.get(side), dict) else {}
    candidates = (
        fixture.get(f"{side}_crest_url"), fixture.get(f"{side}_logo"),
        raw.get(f"{side}_crest_url"), raw.get(f"{side}_logo"), raw.get(f"{side}Logo"),
        team.get("logo"), team.get("crest"),
    )
    return next(
        (str(value).strip() for value in candidates if str(value or "").strip().startswith(("https://", "http://"))),
        "",
    )


def ensure_crest(url: str, path: Path) -> Path:
    """Cache one validated official crest without accepting an HTML/error payload."""
    if path.is_file():
        try:
            with Image.open(path) as image:
                image.verify()
            return path
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "JaguarTV-Prematch/1.0", "Accept": "image/*"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except Exception as error:  # noqa: BLE001 - do not publish a poster with an unverified crest
        raise RuntimeError(f"official crest unavailable: {url} ({type(error).__name__})") from error
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def resolve_fixture_crests(
    fixtures: list[dict[str, Any]], crest_dir: Path, *, required: bool,
) -> dict[str, dict[str, Path]]:
    """Resolve both crests before composition; production never emits a crestless match poster."""
    resolved: dict[str, dict[str, Path]] = {}
    for fixture in fixtures:
        fixture_id = str(fixture.get("fixture_id") or "").strip()
        if not fixture_id:
            raise RuntimeError("official crest resolution requires a fixture_id")
        entries: dict[str, Path] = {}
        for side in ("home", "away"):
            url = _fixture_crest_url(fixture, side)
            if not url:
                if required:
                    raise RuntimeError(f"official {side} crest URL is missing for {fixture_id}")
                continue
            entries[side] = ensure_crest(url, crest_dir / f"{fixture_id}-{side}.png")
        if required and set(entries) != {"home", "away"}:
            raise RuntimeError(f"both official crests are required for {fixture_id}")
        resolved[fixture_id] = entries
    return resolved


def _crest_disc(layer: Image.Image, crest_path: Path, center: tuple[int, int], radius: int) -> list[int]:
    draw = ImageDraw.Draw(layer)
    box = [center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius]
    draw.ellipse(box, fill=(247, 249, 247, 245), outline=GOLD, width=7)
    crest = _transparent_icon(crest_path, (int(radius * 1.48), int(radius * 1.48)))
    xy = (center[0] - crest.width // 2, center[1] - crest.height // 2)
    layer.alpha_composite(crest, xy)
    return box


def _text_box(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, face: ImageFont.ImageFont, anchor: str = "mm") -> list[int]:
    return list(draw.textbbox(xy, text, font=face, anchor=anchor, stroke_width=3))


def _separate(first: list[int], second: list[int], clearance: int = 0) -> bool:
    return (
        first[2] + clearance <= second[0]
        or second[2] + clearance <= first[0]
        or first[3] + clearance <= second[1]
        or second[3] + clearance <= first[1]
    )


def _channel_paths(labels: list[str], root: Path) -> list[Path]:
    paths: list[Path] = []
    for label in labels:
        pieces = re.split(r"\s*(?:/|•|,)\s*", str(label).upper())
        for piece in filter(None, pieces):
            stem = CHANNEL_ALIASES.get(piece, piece.replace(" ", "_"))
            path = next((root / f"{stem}{ext}" for ext in (".png", ".jpg", ".jpeg", ".ico") if (root / f"{stem}{ext}").is_file()), None)
            if path and path not in paths:
                paths.append(path)
    return paths


def _paste_channels(layer: Image.Image, labels: list[str], root: Path, center: tuple[int, int], width: int, height: int) -> list[dict[str, Any]]:
    paths = _channel_paths(labels, root)
    if not paths:
        return []
    target_w = min(230, max(80, (width - 28 * (len(paths) - 1)) // len(paths)))
    icons = [_transparent_icon(path, (target_w, height)) for path in paths]
    total = sum(icon.width for icon in icons) + 28 * (len(icons) - 1)
    cursor = center[0] - total // 2
    placements = []
    for path, icon in zip(paths, icons):
        x, y = cursor, center[1] - icon.height // 2
        layer.alpha_composite(icon, (x, y))
        placements.append({"source": str(path), "box": [x, y, x + icon.width, y + icon.height]})
        cursor += icon.width + 28
    return placements


def prepare_background(raw: Path, output: Path) -> None:
    source = ImageOps.exif_transpose(Image.open(raw)).convert("RGB")
    image = ImageOps.fit(source, (W, H), method=Image.Resampling.LANCZOS)
    image = ImageEnhance.Brightness(image).enhance(0.88)
    image = ImageEnhance.Contrast(image).enhance(1.12)
    image = ImageEnhance.Color(image).enhance(1.18)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=70)).convert("RGBA")
    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shade)
    for y in range(H):
        alpha = max(0, int(155 * (1 - y / 900))) + max(0, int(180 * ((y - 1450) / 1110)))
        draw.line((0, y, W, y), fill=(0, 4, 10, min(alpha, 205)))
    image.alpha_composite(shade)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, "PNG", optimize=True)


def compose_poster(
    background: Path, output: Path, foreground_output: Path, *, kind: str,
    fixtures: list[dict[str, Any]], predictions: dict[str, dict[str, str]],
    logo_path: Path, channels_root: Path, crest_paths: dict[str, dict[str, Path]] | None = None,
    require_crests: bool = False,
) -> dict[str, Any]:
    base = Image.open(background).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    logo = _transparent_logo(logo_path, (260, 260))
    logo_xy = (W - logo.width - 54, 42)
    layer.alpha_composite(logo, logo_xy)
    placements: list[dict[str, Any]] = []
    crest_placements: list[dict[str, Any]] = []
    text_boxes: list[list[int]] = []

    if kind == "single":
        fixture = fixtures[0]
        home, away = str(fixture["home_team"]), str(fixture["away_team"])
        _text(draw, (80, 105), "PRÉ-JOGO", _font(92), GOLD, "lm")
        competition = str(fixture.get("competition") or "FUTEBOL")
        _text(draw, (80, 215), competition, _fit(draw, competition, 1420, 58, 34), WHITE, "lm")
        _text(draw, (W // 2, 410), str(fixture.get("schedule_date", "")), _font(58), GOLD)
        _text(draw, (W // 2, 500), str(fixture.get("kickoff_at_brt", "")), _font(112))
        _text(draw, (W // 2, 580), "HORÁRIO DE BRASÍLIA", _font(34), GOLD)
        placements = _paste_channels(layer, list(fixture.get("channels") or []), channels_root, (W // 2, 700), 1100, 120)
        fixture_crests = (crest_paths or {}).get(str(fixture.get("fixture_id")), {})
        if require_crests and set(fixture_crests) != {"home", "away"}:
            raise RuntimeError(f"both official crests are required for {fixture.get('fixture_id')}")
        for side, center_x in (("home", 570), ("away", W - 570)):
            crest_path = fixture_crests.get(side)
            if crest_path:
                box = _crest_disc(layer, crest_path, (center_x, 1000), 125)
                crest_placements.append({"side": side, "source": str(crest_path), "box": box})
        home_face = _fit(draw, home.upper(), 780, 76, 36)
        away_face = _fit(draw, away.upper(), 780, 76, 36)
        _text(draw, (570, 1270), home.upper(), home_face)
        _text(draw, (W - 570, 1270), away.upper(), away_face)
        _text(draw, (W // 2, 1000), "VS", _font(72), GOLD)
        text_boxes.extend([
            _text_box(draw, (570, 1270), home.upper(), home_face),
            _text_box(draw, (W - 570, 1270), away.upper(), away_face),
        ])
        prediction = predictions.get(str(fixture.get("fixture_id")), {})
        panel = (180, 1510, W - 180, 2440)
        draw.rounded_rectangle(panel, radius=34, fill=(0, 7, 16, 215), outline=GOLD, width=5)
        _text(draw, (W // 2, 1605), "PREVISÃO JAGUARTV", _font(62), GOLD)
        score = str(prediction.get("score") or "PLACAR EM ANÁLISE")
        _text(draw, (W // 2, 1755), score, _fit(draw, score, 1500, 112, 58))
        probabilities = str(prediction.get("probabilities") or "PROBABILIDADES EM ANÁLISE")
        probability_face = _fit(draw, probabilities, 1500, 64, 42)
        _text(draw, (W // 2, 1885), probabilities, probability_face)
        tactical = str(prediction.get("tactical") or "Análise tática baseada nas informações verificadas da partida.")
        tactical_face, tactical_lines = _fit_wrapped(draw, tactical, 1500, 58, 28, 4)
        line_height = min(76, 300 // max(1, len(tactical_lines)))
        for index, line in enumerate(tactical_lines):
            _text(draw, (W // 2, 2070 + index * line_height), line, tactical_face)
        content_boxes = [panel, [*logo_xy, logo_xy[0] + logo.width, logo_xy[1] + logo.height]]
    else:
        _text(draw, (80, 105), "AGENDA DE JOGOS", _font(86), GOLD, "lm")
        _text(draw, (80, 220), str(fixtures[0].get("schedule_date", "")), _font(54), WHITE, "lm")
        top, bottom = 390, 2440
        row_h = (bottom - top) // max(1, len(fixtures))
        for index, fixture in enumerate(sorted(fixtures, key=lambda item: str(item.get("kickoff_at_brt", "")))):
            y0, yc = top + index * row_h, top + index * row_h + row_h // 2
            draw.rounded_rectangle((70, y0 + 8, W - 70, y0 + row_h - 8), radius=22, fill=(0, 7, 16, 190), outline=(255, 255, 255, 75), width=2)
            _text(draw, (190, yc - 30), str(fixture.get("kickoff_at_brt", "")), _font(54), GOLD)
            home, away = str(fixture["home_team"]), str(fixture["away_team"])
            fixture_crests = (crest_paths or {}).get(str(fixture.get("fixture_id")), {})
            if require_crests and set(fixture_crests) != {"home", "away"}:
                raise RuntimeError(f"both official crests are required for {fixture.get('fixture_id')}")
            for side, center_x in (("home", 400), ("away", W - 260)):
                crest_path = fixture_crests.get(side)
                if crest_path:
                    box = _crest_disc(layer, crest_path, (center_x, yc - 26), 58)
                    crest_placements.append({"side": side, "source": str(crest_path), "box": box})
            home_face = _fit(draw, home, 430, 46, 24)
            away_face = _fit(draw, away, 400, 46, 24)
            _text(draw, (740, yc - 30), home, home_face)
            _text(draw, (1040, yc - 30), "VS", _font(34), GOLD)
            _text(draw, (1380, yc - 30), away, away_face)
            text_boxes.extend([
                _text_box(draw, (740, yc - 30), home, home_face),
                _text_box(draw, (1380, yc - 30), away, away_face),
            ])
            placements.extend(_paste_channels(layer, list(fixture.get("channels") or []), channels_root, (W // 2, yc + 70), 450, 74))
        content_boxes = [[70, top, W - 70, bottom], [*logo_xy, logo_xy[0] + logo.width, logo_xy[1] + logo.height]]

    foreground_output.parent.mkdir(parents=True, exist_ok=True)
    layer.save(foreground_output, "PNG", optimize=True)
    base.alpha_composite(layer)
    output.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(output, "PNG", optimize=True)
    checked_boxes = [*content_boxes, *text_boxes, *(item["box"] for item in placements), *(item["box"] for item in crest_placements)]
    crest_clearance = all(
        _separate(crest["box"], text_box, 42)
        for crest in crest_placements for text_box in text_boxes
    )
    return {
        "canvas": [W, H],
        "background": str(background),
        "foreground": str(foreground_output),
        "logo_source": str(logo_path),
        "logo_sha256": hashlib.sha256(logo_path.read_bytes()).hexdigest(),
        "logo_box": content_boxes[-1],
        "channel_icons": placements,
        "crests": crest_placements,
        "required_crest_count": 2 if kind == "single" else len(fixtures) * 2,
        "crest_policy": "required" if require_crests else "not_required_dry_run",
        "player_head_exclusion_zone": [0, 620, W, 1450],
        "team_name_boxes": text_boxes,
        "crest_text_clearance": crest_clearance,
        "content_boxes": content_boxes,
        "all_content_in_bounds": all(0 <= box[0] < box[2] <= W and 0 <= box[1] < box[3] <= H for box in checked_boxes),
    }
