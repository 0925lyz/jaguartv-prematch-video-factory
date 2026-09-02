# Single-Match Image2 Prompt Contract

Produce one complete English Image2 prompt. The generated poster itself must contain only natural
Brazilian Portuguese.

- Final format is 4:5 portrait.
- Place the supplied JaguarTV logo unchanged in the upper-right corner.
- Show the verified competition, teams, date, kickoff time, and every broadcast channel.
- Label all public time as Horário de Brasília.
- Use the supplied official icon for every listed channel.
- Use current crests, current-season kits, and two validated licensed high-resolution player
  photographs, normally one from each team.
- If selection depends on a current Brazilian player at a major European-league club, feature that
  player when expected participation is supported.
- Do not cover heads, faces, eyes, or identifying facial features.
- Use one lower prediction box only. It starts with `PALPITE`, contains a predicted score and a
  short factual prediction, and remains readable as an unopened small-video cover.
- Do not add betting disclaimers or responsible-gambling copy.
- Prefer face-to-face stars, controlled crest collision, or recognizable polished chibi stars.
- Do not invent player identity, club, kit, crest, channel, date, time, or source asset.

After Image2 returns a visual background, compose factual text, logo, and channel icons
deterministically, then validate the final 4:5 file.
