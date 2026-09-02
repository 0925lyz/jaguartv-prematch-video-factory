# Bundled Asset Provenance

The following assets were copied from operator-supplied, already-authorized project resources on
2026-09-02. No assets were downloaded during repository consolidation.

- `assets/brand/jaguartv-logo.png`: supplied JaguarTV Figure 1 logo.
- `assets/channels/`: official broadcast-channel icons from the existing `image2数据库` inventory.
- `assets/video/operation/`: approved operation and main-interface modules from
  `jaguartv-v7-pack`.
- `assets/video/cta/`: approved CTA motion and static inventory from `jaguartv-v7-pack`.
- `assets/audio/music/`: V7 music inventory; license/source rows are retained in
  `docs/music-licenses.csv`.
- `assets/audio/voiceover/`: existing authorized local WAVs plus the reusable APIMart nova and
  shimmer pt-BR CTA voices. Daily production rotates this inventory and does not regenerate it.

`docs/v7-asset-manifest.csv` retains the source pack's asset records. The generated
`docs/bundled-assets.sha256` file provides repository-level content hashes.

Player photographs, club crests, and current-season kits are intentionally not bulk-vendored into
Git. They remain in the licensed `image2数据库`, and each production run must copy only validated
assets into its ignored run directory while retaining source and license evidence.
