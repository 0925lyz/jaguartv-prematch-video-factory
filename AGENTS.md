# JaguarTV Prematch Factory Routing

These rules apply to every task and automation in this repository.

## Text generation

- Use `deepseek-v4-flash` through the configured DeepSeek provider.
- Fall back to `deepseek-v4-pro` through that same provider only after a recorded failure.
- Validate the selected model before each production run.
- Keep `provider_id` and `model_id` as separate fields in records and manifests.
- Do not place credentials, tokens, cookies, or account-session files in this repository.

## Media generation

- Image generation uses the configured active `gpt-image-2` route first and APIMart second.
- A fallback must be recorded; it must never be silent.
- Video generation uses only the authenticated Dreamina/Jimeng VIP account and configured
  Seedance model.
- Text, image, and video provider selections are independent.

## Production safety

- Stop at the first unavailable required integration and report the sanitized error.
- Do not substitute missing player assets with an unapproved player or a playerless design.
- Do not upload a file until all media validation checks pass.
- Upload only to Pending Review under the exact inventory label `赛前预测`.
