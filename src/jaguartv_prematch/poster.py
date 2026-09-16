from __future__ import annotations

import hashlib
import re
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


def _crest_image(path: Path, size: tuple[int, int]) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    bbox = image.getbbox()
    if bbox:
        image = image.crop(bbox)
    scale = min(size[0] / max(1, image.width), size[1] / max(1, image.height))
    if scale != 1:
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return image


def ensure_crest(url: str, path: Path) -> Path:
    """Download a team crest once; resume-friendly and fail-hard per production safety."""
    import time

    import requests

    if path.is_file() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            path.write_bytes(response.content)
            return path
        except Exception as error:  # noqa: BLE001 - retried below, then raised
            last_error = error
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"crest download failed for {url}: {last_error}")


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
    logo_path: Path, channels_root: Path,
    crest_paths: dict[str, Path | None] | None = None,
) -> dict[str, Any]:
    base = Image.open(background).convert("RGBA")
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    logo = _transparent_logo(logo_path, (260, 260))
    logo_xy = (W - logo.width - 54, 42)
    layer.alpha_composite(logo, logo_xy)
    placements: list[dict[str, Any]] = []
    crest_boxes: list[dict[str, Any]] = []

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
        crest_sources = crest_paths or {}
        for side, center_x in (("home", 570), ("away", W - 570)):
            crest_file = crest_sources.get(side)
            if not crest_file:
                continue
            crest = _crest_image(Path(crest_file), (260, 260))
            crest_xy = (center_x - crest.width // 2, 940 - crest.height // 2)
            layer.alpha_composite(crest, crest_xy)
            crest_boxes.append({
                "side": side,
                "source": str(crest_file),
                "box": [crest_xy[0], crest_xy[1], crest_xy[0] + crest.width, crest_xy[1] + crest.height],
            })
        _text(draw, (570, 1160), home.upper(), _fit(draw, home.upper(), 780, 76, 36))
        _text(draw, (W - 570, 1160), away.upper(), _fit(draw, away.upper(), 780, 76, 36))
        _text(draw, (W // 2, 1160), "VS", _font(72), GOLD)
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
            _text(draw, (760, yc - 28), home, _fit(draw, home, 660, 46, 24))
            _text(draw, (1080, yc - 28), "VS", _font(34), GOLD)
            _text(draw, (1400, yc - 28), away, _fit(draw, away, 600, 46, 24))
            placements.extend(_paste_channels(layer, list(fixture.get("channels") or []), channels_root, (1700, yc + 60), 450, 74))
        content_boxes = [[70, top, W - 70, bottom], [*logo_xy, logo_xy[0] + logo.width, logo_xy[1] + logo.height]]

    foreground_output.parent.mkdir(parents=True, exist_ok=True)
    layer.save(foreground_output, "PNG", optimize=True)
    base.alpha_composite(layer)
    output.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(output, "PNG", optimize=True)
    checked_boxes = [*content_boxes, *(item["box"] for item in placements), *(box["box"] for box in crest_boxes)]
    return {
        "canvas": [W, H],
        "background": str(background),
        "foreground": str(foreground_output),
        "logo_source": str(logo_path),
        "logo_sha256": hashlib.sha256(logo_path.read_bytes()).hexdigest(),
        "logo_box": content_boxes[-1],
        "channel_icons": placements,
        "crests": crest_boxes,
        "content_boxes": content_boxes,
        "all_content_in_bounds": all(0 <= box[0] < box[2] <= W and 0 <= box[1] < box[3] <= H for box in checked_boxes),
    }
