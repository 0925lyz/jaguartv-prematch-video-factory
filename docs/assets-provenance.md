# Bundled Asset Provenance

The production media inventories were replaced from the operator-supplied local folders on
2026-09-15. No media was downloaded or generated during the replacement.

- `assets/brand/jaguartv-logo.png`: supplied JaguarTV Figure 1 logo.
- `assets/channels/`: official broadcast-channel icons from the existing `image2数据库` inventory.
- `assets/video/operation/`: every MP4 from the supplied `中间操作类素材` folder.
- `assets/video/cta/motion/`: every MP4 from the supplied `最后CTA素材` folder.
- `assets/audio/music/`: every MP3/M4A audio file from the supplied `音频/背景音乐` folder.
- `assets/audio/voiceover/`: every WAV from the supplied `音频/CTA口播` folder.

Runtime discovery ignores README, JSON transcript, manifest, and static-image files. Production
does not call TTS or another generation service for music or CTA voice.

Player photographs, club crests, and current-season kits are intentionally not bulk-vendored into
Git. They remain in the licensed `image2数据库`, and each production run must copy only validated
assets into its ignored run directory while retaining source and license evidence.
