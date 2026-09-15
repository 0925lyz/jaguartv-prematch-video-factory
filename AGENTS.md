# JaguarTV Prematch Factory Routing

These rules apply to every task and automation in this repository.

## Text model routing

- Use the large model selected for the current execution task by default.
- A run may explicitly configure any available text model, including hy3, hy4, DeepSeek, or GPT routes.
- Do not hard-code stale model names such as `gpt-5.5` or `gpt-5.6-sol` in prompts, scripts, configuration, task creation, or automation prompts.
- Verify an explicitly configured model before treating it as available.
- Keep `provider_id` and `model_id` as separate fields in records and manifests.
- Do not place credentials, tokens, cookies, or account-session files in this repository.

## Media generation

- Image generation uses the configured active `gpt-image-2` route first and APIMart second.
- A fallback must be recorded; it must never be silent.
- The upper-right JaguarTV logo must be baked into the poster itself exactly once. Video compositing must not add a second logo overlay.
- Image2 generates only the clean 4:5 background. Text, predictions, team information, channel icons,
  and the exact Figure 1 logo are composited deterministically into a 2048x2560 PNG foreground.
- Video generation uses the authenticated Dreamina/Jimeng VIP Seedance route first. If it is
  unavailable, use only the configured APIMart `wan2.6-i2v-flash` fallback at 720p for 4 seconds
  and record both provider attempts.
- Select exactly half of each poster batch deterministically for background-only motion; odd batches
  drop one deterministic motion candidate. Every remaining poster uses a local static 4-second hook.
- Generate exactly the opening 4-second poster hook. Play each selected operation-class video
  and motion CTA in full; the final duration is their actual combined duration, not a fixed 12 seconds.
  Middle clips, CTA, music, and voiceover must come from existing authorized inventory.
- Discover the four repository-local media pools automatically and rotate operation, CTA, music,
  and voice independently. Persist positions and advance them only after final validation. Never
  call TTS or another generation service for music or CTA voice.
- Text, image, and video provider selections are independent.

## Execution entrypoint

- Production and WorkBuddy runs must use `scripts/task1_launcher.sh <1|2|3>` from this repository; each WorkBuddy slot owns its matching fixed batch number.
- The default target is tomorrow in Brasília time. An explicit operator request for today must pass `--date today`; a concrete date must pass `--date YYYYMMDD`. Never substitute another day.
- Fixture collection must use the configured `https://copa.jarg.top/api/save-agenda` route. Never start, probe, or consume a temporary localhost fixture proxy.
- `scripts/task1_driver.py` is the only Task 1 batch driver. Do not call or recreate the old sibling
  `/Users/jaguar/WorkBuddy/赛前/jaguartv-prematch-automation` entrypoint.
- The package CLI may be used for isolated phase debugging only; daily production must not call
  `jaguartv-prematch run` directly.

## Publish copy

- Write concise pt-BR copy in a natural Brazilian football voice: matchup hook, editorial prediction,
  one short tactical point when available, Jaguar TV prompt, and the fixed download sentence.
- TikTok copy uses exactly five relevant hashtags. Keep `#jaguartv` and `#iptv`; prefer compact team
  and popular competition tags for the other three. Never slice a long phrase into a meaningless tag.
- Do not claim `transmissão liberada`, `sem travamentos`, a free trial, or another offer unless that
  exact claim is verified for the run.
- TikTok publishing must enable the commercial-content disclosure and the AI-generated-content label
  when the realistic poster or hook is AI-generated. Caption manifests record both requirements.

## Production safety

- Stop at the first unavailable required integration and report the sanitized error.
- Do not substitute missing player assets with an unapproved player or a playerless design.
- Do not upload a file until all media validation checks pass.
- Upload only to Pending Review under the exact inventory label `赛前预测`.
