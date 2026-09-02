# JaguarTV Prematch Video Factory

Standalone, auditable production repository for `任务1赛前预测视频`. It consolidates the current
working Task 1 implementations while keeping the existing `Video-creation-automation` repository
untouched.

## What is included

- Tomorrow-fixture collection in Horário de Brasília.
- Featured-match selection with current Brazilian-player membership gating.
- Provider-safe DeepSeek text routing with a recorded fallback.
- Independent primary/fallback Image2 configuration.
- Dreamina/Jimeng VIP plus Seedance V7 video contract.
- SHA-256-deduplicated poster, operation-clip, and CTA delivery builder.
- Tests for timezone, selection rules, and provider/model rejection.
- Prompt contracts and a research-evidence schema that fail closed on uncertain player assets.
- Real-player face-off poster background method and text-overlay rules: see [docs/poster-master-prompt.md](docs/poster-master-prompt.md) and [docs/poster-production-rules.md](docs/poster-production-rules.md).

The repository contains no API keys, cookies, tokens, or account-session files. Copy
`config/prematch.example.json` to the ignored `config/local.json` and provide secrets through the
named environment variables.

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

Build a media delivery folder by passing only the approved source roots:

```bash
jaguartv-prematch media-delivery \
  --destination delivery/赛前海报视频 \
  --poster-root /path/to/poster/output \
  --video-root /path/to/final/videos \
  --cta-root /path/to/approved/cta
```

See [docs/architecture.md](docs/architecture.md) for pipeline boundaries and provenance.

## Production gates

Run `preflight` before every production date. A blocked image route or unavailable research
backend is a stop condition, not permission to generate placeholders. Production runs retain the
fixture payload, research evidence, prompt, provider route records, image QA, Jimeng task ID,
component rotation, captions, build manifest, upload ID, and server verification result under one
date-scoped run directory.
