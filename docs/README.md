# MangaTrax Documentation

This folder contains the authoritative design, release and validation documents. The root
[`README.md`](../README.md) is the user/operator guide; [`CHANGELOG.md`](../CHANGELOG.md) tracks
releases.

## Authoritative documents

| Document | Purpose |
|---|---|
| [APP_DESIGN_SPEC.md](APP_DESIGN_SPEC.md) | Complete application design specification v1.0 — architecture, data model, workflows, every feature/working, known issues |
| [FEATURE_REGISTRY.md](FEATURE_REGISTRY.md) | Flat registry of all 457 checkable behaviour claims (the consensus voting sheet) |
| [DESIGN_CONSENSUS_LOG.md](DESIGN_CONSENSUS_LOG.md) | Five-expert consensus rounds, disagreements, resolutions and final sign-off |
| [UI_ENHANCEMENT_SPEC.md](UI_ENHANCEMENT_SPEC.md) | Approved UI enhancement design v1.0 (desktop + mobile) |
| [UI_ENHANCEMENT_SUMMARY.md](UI_ENHANCEMENT_SUMMARY.md) | User-facing summary of the first UI release |

## Working material

| Path | Contents |
|---|---|
| [`design/spec-parts/`](design/spec-parts/) | Modular domain specifications (00 overview, 01 releases, 02 series, 03 downloads, 04 platform, 05 UI). `APP_DESIGN_SPEC.md` is the consolidated authoritative version. |
| [`design/BRAND_IDENTITY.md`](design/BRAND_IDENTITY.md) | Sumi Ink & Hanko visual identity — palette, typography, logo/favicon, card/spine rules and constraint mapping |
| [`design/CORRECTIONS_ROUND1.md`](design/CORRECTIONS_ROUND1.md) | Record of the round-1 corrections applied between v0.9 and v0.10 |
| [`design/reviews/`](design/reviews/) | Expert review evidence for each specification consensus round |
| [`ui/DESIGN_VALIDATION_LOG.md`](ui/DESIGN_VALIDATION_LOG.md) | UI design rounds, overlapping aspect assignments and final sign-off |
| [`ui/IMPLEMENTATION_REVIEW_1.md`](ui/IMPLEMENTATION_REVIEW_1.md), [2](ui/IMPLEMENTATION_REVIEW_2.md), [3](ui/IMPLEMENTATION_REVIEW_3.md) | Independent QA passes over the UI implementation |
| [`ui/reviews/`](ui/reviews/) | Expert UI-design review evidence |

## Archive

[`archive/`](archive/) holds historical material that is **not authoritative**:

* `archive/v1.0/` — immutable copy of the v1.0 specification, registry and consensus log.
* `archive/spec-drafts/` — v0.9–v0.11 working drafts kept only for audit history.
* `archive/development/` — superseded development documents (`PROJECT.md`,
  `FEATURE_RECOMMENDATIONS.md`, `TEST_INFRA.md`, `TEST_READY.md`, `TORBOX_INTEGRATION_PLAN.md`).
  The current test entry point is `tests/run_tests.py`; current behaviour is in the app spec, not
  these historical plans.

When a document and the code disagree, the code wins; when an archived document and an
authoritative document disagree, the authoritative document wins.
