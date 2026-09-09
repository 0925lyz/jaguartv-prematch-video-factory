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
2. Select featured matches only: Brasileirão Série A/B, Boca Juniors, River Plate, the configured major clubs, and major-league teams with verified current Brazilian-player membership. Save `phase1/selected-fixtures.json` so Task 3 post-match can consume the same fixture IDs later.
3. Research each selected match with current evidence. Store source URL, platform, publication time, retrieval time, excerpt, summary, and confidence. Old injuries/transfers/lineups fail the research gate.
4. Generate Image2 poster prompts in English; visible poster text must be natural pt-BR. Image2 primary is the active `gpt-image-2` route; APIMart is the explicit fallback. If both fail, deliver prompts only and stop before video.
5. Bake the exact JaguarTV logo into the poster itself once, in the upper-right safe area. Do not let Image2, Dreamina, or the video compositor redraw or add a second logo. The right-top area should contain only the logo.
6. Generate exactly the first 4-second dynamic poster hook with Dreamina/Jimeng VIP Seedance. If that route is unavailable, use the configured APIMart `wan2.6-i2v-flash` fallback with `720p` and `duration: 4`, recording both attempts. Play both selected operation clips and the selected motion CTA in full; final duration is not fixed at 12 seconds.
7. Rotate downloader/search/interface clips, CTA, music, and voice deterministically. Same-day final videos must not reuse the same full component combination.
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
- The media delivery command can also build the consolidated `赛前海报视频` folder with `赛前海报`, `操作类`, and `cta` subfolders.
- If a Lark/飞书 account adapter is configured, select one video plus its matching caption for the group before final delivery numbering. That sampled Lark video is not included in the final delivery folder, server upload, or all-caption order. If Lark is not configured, stop at that integration gate and report it.

## Quality gates

- No Beijing/São Paulo label in public copy; use `Horário de Brasília`.
- Poster is 4:5; video master/final is 9:16; full poster remains visible without cropping.
- Logo appears once, as part of the poster, upper-right safe area, no black backing panel.
- Prediction uses one lower text panel, no betting disclaimer, no responsible-gambling/no-bet copy.
- Current player identity, club, kit, crest, channel icons, date, time, and predicted score are verified before publishing.
- Text/Image2/Jimeng providers remain independent.
- Any unavailable skill, provider, repository, account, API, or required file stops the run with a sanitized error.
