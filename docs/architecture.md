# Architecture

The repository consolidates the reusable work from the three Task 1 implementations and the
applicable parts of the fixture collector, poster-only, pre-match, and post-match tasks. It does
not modify or vendor the separate video-creation-automation repository.

## Pipeline boundaries

1. `collector.py` reads the configured local tomorrow-fixtures service and stores its raw payload.
2. `selection.py` applies featured-match rules. A five-major-league match qualifies through a
   Brazilian player only when current membership evidence has populated
   `verified_brazilian_players`.
3. Research records must retain URL, platform, timestamps, excerpt, normalized summary, and
   confidence before poster generation begins.
4. Prompt generation uses the configured DeepSeek provider. Image generation is a separate
   primary/fallback route and records the route actually used.
5. Poster jobs fail closed when current licensed player, crest, kit, channel-icon, or logo assets
   are missing. Schedule posters contain at most eight fixtures per page.
6. Video jobs use the V7 composition contract: 3-second hook, 3-second operation module,
   3-second main-interface module, and 3-second CTA. Component rotation is deterministic by
   Brasilia date plus fixture ID.
7. Upload is a separate final gate. Only validated packages are sent to Pending Review under
   `赛前预测`; the pipeline never approves or publishes them.

## Reused decisions

- Daily collector: structured fixture IDs, original source text, complete channel lists, and
  source freshness fields.
- Poster-only task: Image2 visual background plus deterministic information overlay and QA.
- Pre-match tasks: player asset gating, lower prediction box, readable pt-BR copy, and 4:5 output.
- Post-match task: reusable evidence schema, media validation, CTA audio inventory, and upload
  verification patterns only. Post-match wording and result semantics are not reused.
- V7 pack: composition script, operation modules, CTA assets, music, licenses, voiceovers, and
  build-manifest component accounting.

## Failure contract

Every failed phase reports the operation, provider or file, sanitized error, attempted checks,
last verified artifact, and then stops. No provider substitution occurs outside the explicitly
configured text and Image2 fallback chains. Secrets are read from environment variables or the
operator's existing authenticated tools and are excluded from manifests and logs.
