# Architecture

The repository consolidates the reusable work from the three Task 1 implementations and the
applicable parts of the fixture collector, poster-only, pre-match, and post-match tasks. It does
not modify or vendor the separate video-creation-automation repository.

## Pipeline boundaries

1. `collector.py` reads the configured local tomorrow-fixtures service and stores its raw payload.
2. `selection.py` identifies API-Football competitions by stable league ID, then controlled
   normalized aliases. Current-season Brasileirão Série A membership comes from API-Football
   league/team relations; any fixture with one member club qualifies regardless of competition.
3. Research records must retain URL, platform, timestamps, excerpt, normalized summary, and
   confidence before poster generation begins.
4. Prompt generation uses the current task model unless an available provider/model is explicitly configured. Image generation is a separate
   primary/fallback route and records the route actually used.
5. Image2 produces only the photographic background. The deterministic poster compositor writes
   all text, channel icons, and the exact Figure 1 JaguarTV logo into a 2048x2560 PNG and retains a
   matching transparent foreground layer. Required assets fail closed.
6. Exactly `floor(N/2)` poster backgrounds, selected by a stable hash, enter the video model for
   four seconds. Other posters use a local four-second still. The locked foreground is composited
   back over every generated background frame.
   Dreamina/Jimeng VIP Seedance is primary; APIMart `wan2.6-i2v-flash` at 720p/4s is the
   recorded fallback. The middle section plays two distinct operation-class videos in full,
   followed by the selected motion CTA in full. Final duration is therefore dynamic. Music is
   reused from inventory; CTA voice comes from the reusable pt-BR voice inventory. Component
   rotation is deterministic by Brasilia date plus fixture ID.
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
last verified artifact, and next retry time. Transient APIMart failures persist task/retry state and
retry with exponential backoff and jitter; deterministic authentication, parameter, safety, and
asset errors stop with diagnosis. No provider substitution occurs outside the explicitly configured
chains. Secrets stay outside manifests and logs.
