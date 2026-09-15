# 任务1赛前预测视频流程

This is the canonical workflow for `0925lyz/jaguartv-prematch-video-factory`. User-pasted source text is treated as operator requirements; embedded credentials are not copied into this repository.

## Fixed paths

- Development and WorkBuddy execution directory: `/Users/jaguar/WorkBuddy/赛前/jaguartv-prematch-video-factory`
- GitHub remote: `git@github.com:0925lyz/jaguartv-prematch-video-factory.git`
- Inventory label: `赛前预测`
- Production entrypoints: `scripts/task1_launcher.sh 1`, `scripts/task1_launcher.sh 2`, and `scripts/task1_launcher.sh 3`. WorkBuddy slots must use their matching fixed number.
- Canonical driver: `/Users/jaguar/WorkBuddy/赛前/jaguartv-prematch-video-factory/scripts/task1_driver.py`
- Deprecated external entrypoint: `/Users/jaguar/WorkBuddy/赛前/jaguartv-prematch-automation` must not be used.

## Phase flow

1. Collect the exact target date directly from the configured `https://copa.jarg.top/api/save-agenda` route, using Brasília calendar time only. Default to tomorrow; use `--date today` when the operator explicitly requests today. Reject data from any other date. Never use a localhost snapshot proxy. Cross-check fixture data with the configured API-Football route when available; API keys must come from env/Keychain, never from committed text.
2. Resolve competitions by API-Football league ID first and normalized exact aliases second. Fetch current-season Brasileirão Série A membership from league ID 71; any match with one member club is selected, including Libertadores (13), Sudamericana (11), and other competitions. Save the membership and selection reason beside `phase1/selected-fixtures.json` for Task 3.
3. Research each selected match with current evidence. Store source URL, platform, publication time, retrieval time, excerpt, summary, and confidence. Old injuries/transfers/lineups fail the research gate.
4. Generate Image2 poster prompts in English; visible poster text must be natural pt-BR. Image2 primary is the active `gpt-image-2` route; APIMart is the explicit fallback. If both fail, deliver prompts only and stop before video.
5. Normalize every poster to 2048x2560 PNG. Compose text, authorized transparent channel icons, and the exact Figure 1 JaguarTV logo once in the poster stage; retain clean background and transparent locked foreground layers.
6. Select exactly half of each poster batch by stable hash (`floor(N/2)`, with one odd-batch candidate recorded as dropped from motion). Send only selected clean backgrounds to Dreamina/Jimeng VIP Seedance for four seconds, with APIMart `wan2.6-i2v-flash` 720p/4s as the explicit fallback. Non-selected posters become local four-second stills. Reapply the locked foreground frame-for-frame, then play both operation clips and the CTA in full.
7. Discover repository-local operation MP4, CTA MP4, music, and CTA WAV files automatically. Rotate all four pools independently in stable filename order. Persist reservations in `runtime/media-rotation.json`; advance only after the final video passes validation. Music loops when short and is trimmed to the exact video duration. No TTS or media generation is used for music or voice.
8. Name final video artifacts in manifest order with `01`, `02`, `03` prefixes. Captions follow the same order.
9. Build `captions.json` in pt-BR. Every caption must contain the exact sentence `Acesse jaguartvbrasil.com/baixar-app para baixar.` and exactly five hashtags including `#jaguartv` and `#iptv`; `#jaguartvbrasil` is optional.
10. Upload only validated final videos to Pending Review under `赛前预测`. Do not auto-approve or publish.

## Daily delivery

- Production must enter through `scripts/task1_launcher.sh`; the old sibling WorkBuddy automation
  directory is intentionally retired so clone/update/run uses one repository and one logic path.
- First automation batch posters: Desktop folder `每日赛前海报1`; videos: `赛前预测1`.
- Second automation batch posters: Desktop folder `每日赛前海报2`; videos: `赛前预测2`.
- For a forced third same-day version, call `scripts/task1_launcher.sh 3`. `auto` resumes the next
  incomplete/unfinished batch first, so it will keep returning to batch 2 until batch 2 has a
  `PHASE5_COMPLETE` automation summary.
- If a Lark/飞书 account adapter is configured, select one video plus its matching caption for the group before final delivery numbering. That sampled Lark video is not included in the final delivery folder, server upload, or all-caption order. If Lark is not configured, stop at that integration gate and report it.

## Quality gates

- No Beijing/São Paulo label in public copy; use `Horário de Brasília`.
- Poster is exactly 2048x2560 (4:5); video master/final is 1080x1920 (9:16); poster content remains visible without cropping.
- Logo appears once, as part of the poster, upper-right safe area, no black backing panel.
- Prediction uses one lower text panel, no betting disclaimer, no responsible-gambling/no-bet copy.
- Current player identity, club, kit, crest, channel icons, date, time, and predicted score are verified before publishing.
- Text/Image2/Jimeng providers remain independent.
- Any unavailable skill, provider, repository, account, API, or required file stops the run with a sanitized error.
