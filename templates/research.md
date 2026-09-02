# Per-Fixture Research Contract

Research exactly one selected fixture. Use current official clubs, competitions, broadcasters,
verified journalists, and reputable sports media. Use agent-reach's active read-only backend for
each platform. Do not carry an injury, transfer, lineup, kit, or tactical report forward without a
fresh source supporting its current status.

Return JSON containing current team and player information, recent scorelines, current-season
squads, tactics, matchups, probable lineups, absences, injuries, suspensions, licensed player
images, current crests, current kits, and relevant flags.

Every factual record must contain:

- `source_url`
- `platform`
- `publication_time`
- `retrieval_time`
- `source_excerpt`
- `normalized_summary`
- `confidence`

For each proposed poster player, explicitly record identity, current club, current season kit,
expected participation, source license, retrieval date, asset hash, and local asset path. A missing
or uncertain player asset blocks that single-poster job.
