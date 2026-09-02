# Single-Match Poster — Image2 Background + Deterministic Overlay Template (Detailed)

Produce one finished 4:5 PNG pre-match poster per fixture, following the real-star face-off
method. Generate a clean cinematic background with Image2, then overlay exact Brazilian
Portuguese copy, crests, channel icons and the JaguarTV logo deterministically. Full rulebook:
`docs/poster-production-rules.md`; master kit: `docs/poster-master-prompt.md`.

## 1. Per-fixture data

| field | example |
|---|---|
| home / away | CHELSEA / BRIGHTON |
| time | 10H00 (Horário de Brasília, 24h) |
| date | 30 AGO 2026 (never `HOJE`) |
| competition_main / stage | PREMIER LEAGUE / RODADA 2 |
| venue | STAMFORD BRIDGE |
| channels | ESPN, DISNEY+ |
| crest ids | Chelsea=363, Brighton=331 (ESPN CDN `a.espncdn.com/i/teamlogos/soccer/500/{id}.png`) |
| players home/away | two biggest stars from the predicted starting XI, current squad |
| kits | current official kits |
| prediction | 2–1 (display only, no betting advice) |
| probabilities | CHELSEA 47% • EMPATE 30% • BRIGHTON 23% |
| takeaway | one short tactical clause, pt-BR |

## 2. Image2 background prompt (fill the braces, do not add text/graphics)

```text
Use case: ads-marketing
Asset type: JaguarTV pre-match single-game football prediction poster, 4:5 portrait PNG, final 2048x2560.
Primary request: Create one premium Image2-generated background for {HOME} vs {AWAY}, {COMP} {STAGE}, {DATE} at {TIME} Brasília Time, broadcast on {CHANNELS}.
Input-image roles: the exact JaguarTV logo, official home crest and official away crest will be overlaid deterministically; reference posters in image2数据库/参考海报 are style references only, not layouts to copy.
Image2 background only: pure cinematic football photography background, absolutely no readable text, no digits, no typographic shapes, no pseudo-words, no UI panels, no banners, no scoreboards, no logos, no crests, no sponsor marks, no watermark. Do not write competition, time, date, team names, channels, scores or percentages anywhere.
Scene: {STYLE}. Left side: photorealistic likeness of {P_HOME_A} and {P_HOME_B} in {HOME_SHORT} current official kit ({KIT_HOME}); right side: photorealistic likeness of {P_AWAY_A} and {P_AWAY_B} in {AWAY_SHORT} current official kit ({KIT_AWAY}). Both players are recognisable professional footballers in their latest official jerseys, facing each other. Do not place any crest, text, badge or graphic on or above their heads, faces, hair or shoulders.
Composition: frame the two players waist-up at the extreme left and extreme right edges, small heads. Heads and faces must sit entirely within the outer 20% of image width on each side (left: x 0–20%; right: x 80–100%) and between 36% and 50% of image height. The central vertical band (x 20%–80%) between 35% and 65% of height must contain only clean stadium atmosphere (pitch, stands, floodlights, haze, smoke). No face, head, hair, shoulder, arm, hand or body part may enter the central band, the top overlay area (top 34%) or the lower overlay area (below 66%). Never center a player behind the VS zone or the lower prediction panel. Keep heads small and pressed into their outer corner, leaving ≥18% of image width of clean atmosphere between each head's inner edge and the centre line.
Layout reserve zones: leave clean negative space in the top area, the central band (for VS + both crests), a mid-lower strip (for channel logos), and the lower third (for one prediction panel).
Deterministic visible pt-BR copy to be overlaid later (do NOT generate): "{COMP}"; "{STAGE}"; "{HOME}"; "VS"; "{AWAY}"; "{DATE}"; "{TIME}"; "HORÁRIO DE BRASÍLIA"; "CANAIS"; "PREVISÃO JAGUARTV"; "PREVISÃO DE PLACAR: {PREDICTION}"; "{PROBABILITIES}"; "{TAKEAWAY}".
Negative constraints: no generated text, no background writing, no black translucent boxes outside the single bottom prediction panel, no betting advice, no odds, no 18+, no disclaimer, no responsible-gambling copy, no Downloader footer, no Chinese, no English explanatory body text, no invented slogans, no multiple prediction boxes, no crest above/on a player's head, no text over a player's face/body.
QA criteria: correct match mapping, official crests beside VS, exact date incl. year ({DATE}), exact {TIME} Brasília time, exact {CHANNELS}, one continuous prediction panel, no prohibited betting/disclaimer copy, exact JaguarTV logo upper-right, 4:5 PNG, nonblank, readable on mobile.
```

## 3. Deterministic overlay (canvas 2048×2560)

1. Enhance raw background: brightness ×1.08, contrast ×1.14, color ×1.32, unsharp (1.0, 90).
2. Add top/bottom shade, 36px home/away color side bars, gold glow lines (y=292, y=596, centre column 810→1400).
3. JaguarTV logo upper-right (≤150px, 48px margins), never distorted.
4. Centered header: competition (y=118), stage (y=224, gold), date `{DATE}` (y=384, gold), time (y=508, largest), `HORÁRIO DE BRASÍLIA` (y=612), `CANAIS` (y=690, gold).
5. Channel row centered at y=772 (spacing 430 for 2, 340 for 3): official channel icon (≤225×92) with brand-color glow, brand-colored channel lettering below (y=856).
6. Central crest zone: crest centres x=670 / W−670, y=1115, radius 156, white discs with club-color rings, centre `VS` (gold); team names y=1335 (≤560 wide); venue y=1455 (gold).
7. One prediction panel `(410, 1704, W−410, 2236)`, dark navy + gold border, enlarged type (thumbnail-legible): `PREVISÃO JAGUARTV` 46px gold, divider y=1810, `PREVISÃO DE PLACAR: {prediction}` 74px→min 50 (≤1150), probabilities 60px→min 42 (≤1180), takeaway 42px→min 28 (≤1140).
8. Export PNG (optimize), 2048×2560.

Never place any crest, letter, panel or glow over a player's head/face/shoulders; no text boxes
outside the single prediction panel; no betting/disclaimer/18+ copy; no `HOJE`; all time in
Horário de Brasília.

## 4. Output and QA

- Filename: `{HOME}_{AWAY}_{YYMMDD}_Poster.png` (uppercase, no accents, spaces → `_`).
- Also save a clean 2048×2560 `*_Background.png` (same enhancement, no overlays).
- QA: PNG 2048×2560, face detection clear of overlay zones, OCR contains expected pt-BR copy and
  no banned strings (`HOJE`, betting, 18+), date includes year, channels correct.
