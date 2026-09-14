# DMR 0.15 Compatibility Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the promised `django-modern-rest>=0.12` compatibility on DMR 0.15 and unblock the 0.2.0 release.

**Architecture:** Implement DMR's new public authentication challenge property on the shared Knox adapter. Keep runtime error translation and OpenAPI generation unchanged, and verify the additive implementation on old and current DMR versions.

**Tech Stack:** Python, Django, django-modern-rest, DRF, pytest, uv, GitHub Actions.

---

## Chunk 1: Compatibility implementation

### Task 1: Add the DMR 0.15 challenge contract

**Files:**
- Modify: `tests/test_dmr.py`
- Modify: `knox_redis/dmr.py`
- Modify: `CHANGELOG.md`

- [x] Add a regression test asserting both public adapters advertise `Token`.
- [x] Run the focused test on DMR 0.15 and confirm it fails through the inherited abstract property.
- [x] Implement `www_authenticate_challenge` as a property returning `Token`.
- [x] Run the complete DMR suite on 0.15.
- [x] Document DMR 0.15 compatibility in the 0.2.0 changelog.

## Chunk 2: Release verification

### Task 2: Verify supported DMR versions and release gates

**Files:**
- Modify: `uv.lock` if the current resolver selects DMR 0.15.

- [x] Run the DMR suite against 0.12.0, 0.14.0, and 0.15.0.
- [x] Run the full suite with real Redis, Ruff, mypy, Django checks, lock check, and diff check.
- [x] Build and inspect wheel/sdist.
- [ ] Commit and push the compatibility fix.
- [ ] Wait for a fully green GitHub Actions run before creating `v0.2.0`.
