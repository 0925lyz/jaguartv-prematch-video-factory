# JaguarTV Prematch Video Factory

Standalone, auditable production repository for `任务1赛前预测视频`. It consolidates the current
working Task 1 implementations while keeping the existing `Video-creation-automation` repository
untouched.

## What is included

- Tomorrow-fixture collection in Horário de Brasília.
- Featured-match selection with current Brazilian-player membership gating.
- Current-task text routing with an optional explicitly configured provider/model fallback.
- Independent primary/fallback Image2 configuration.
- Dreamina/Jimeng VIP Seedance video generation with an explicit APIMart
  `wan2.6-i2v-flash` 720p/4s fallback.
- Image2 produces a clean background; exact text, channel icons, and the original Figure 1 logo are
  composited into a 2048x2560 PNG and saved as a locked foreground layer.
- Exactly half of each poster batch is selected deterministically for a background-only 4-second
  motion hook. Every unselected poster uses a local static 4-second hook. Two consecutive operation-class
  videos and the selected motion CTA then play in full, so final duration is content-driven.
- Repository-local operation, CTA, music, and CTA-voice inventories are discovered by media type and
  rotated independently through `runtime/media-rotation.json`; positions advance only after validation.
- Tests for timezone, selection rules, and provider/model rejection.
- Prompt contracts and a research-evidence schema that fail closed on uncertain player assets.
- Real-player face-off poster background method and text-overlay rules: see [docs/poster-master-prompt.md](docs/poster-master-prompt.md) and [docs/poster-production-rules.md](docs/poster-production-rules.md).
- Canonical Task 1 operator workflow: see [docs/task1-prematch-workflow.md](docs/task1-prematch-workflow.md).

The repository contains no API keys, cookies, tokens, or account-session files. Copy
`config/prematch.example.json` to the ignored `config/local.json` and provide secrets through the
named environment variables or macOS Keychain entries.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
cp config/prematch.example.json config/local.json
```

Verify text routing and collect tomorrow's fixtures:

```bash
jaguartv-prematch preflight --config config/local.json
jaguartv-prematch collect --config config/local.json --output runs/manual/phase1
```

Run the complete Task 1 workflow through the canonical WorkBuddy/Codex entrypoint. Use
`--dry-run` only for local pipeline verification; production mode still stops when current
research evidence or external integrations are unavailable.

```bash
scripts/task1_launcher.sh 1
scripts/task1_launcher.sh 2
scripts/task1_launcher.sh 3
scripts/task1_launcher.sh 1 --date today
scripts/task1_launcher.sh 1 --dry-run --fixtures-file examples/manual-fixtures.example.json
scripts/task1_launcher.sh 3 --skip-collect   # force a third same-day version from existing phase1/phase2
```

`jaguartv-prematch phase1` ... `phase5` remains available for debugging individual phases, but
daily production should not call `jaguartv-prematch run` directly because it bypasses the batch
style rotation, Lark sample exclusion, and Desktop delivery rules.

See [docs/architecture.md](docs/architecture.md) for pipeline boundaries and provenance.

## Production gates

Run `preflight` before every production date. A blocked image route or unavailable research
backend is a stop condition, not permission to generate placeholders. Production runs retain the
fixture payload, research evidence, prompt, provider route records, image QA, video task ID,
component filenames and cycle positions, captions, build manifest, upload ID, and server verification result under one
date-scoped run directory.

TikTok drafts use a short Brazilian Portuguese matchup hook, prediction and CTA, with exactly five
compact relevant hashtags. `#jaguartv` and `#iptv` remain mandatory. The caption manifest marks the
commercial-content disclosure and applicable AI-generated-content label as required at publication.
