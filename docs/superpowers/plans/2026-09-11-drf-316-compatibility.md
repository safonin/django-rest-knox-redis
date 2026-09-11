# cfm-back Dependency Compatibility Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the local 0.2.0 package installable with the pinned cfm-back Django/DRF/Knox/django-redis stack without bypassing dependency resolution.

**Architecture:** Lower only the proven-compatible DRF, Knox, and django-redis runtime floors, retain their existing upper bounds, and add a dedicated compatibility row. Verify the exact cfm-back stack independently from the rolling current-version matrix before rebuilding local artifacts.

**Tech Stack:** Python, Django, Django REST Framework, django-rest-knox, django-redis, uv, pytest, Ruff, mypy, Hatchling, Twine.

---

## Chunk 1: Metadata and CI

### Task 1: Declare and document cfm-back dependency support

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [x] Change the runtime requirement to `djangorestframework>=3.16.1,<3.18`.
- [x] Change the Knox requirement to `django-rest-knox>=5.0.4,<6`.
- [x] Change the django-redis requirement to `django-redis>=6,<8`.
- [x] Update the documented compatibility range and changelog.
- [x] Regenerate `uv.lock` and verify the root package requirement records the new floor.

### Task 2: Add a lower-bound CI job

**Files:**
- Modify: `.github/workflows/tests.yml`

- [x] Add an explicit Django 6.0 / DRF 3.16.1 / Knox 5.0.4 / django-redis 6 job.
- [x] Keep the existing matrix on the newest allowed DRF.
- [x] Assert the exact DRF lower-bound version in the compatibility job.

## Chunk 2: Verification and package rebuild

### Task 3: Verify compatibility and quality gates

- [x] Resolve a temporary lower-bound environment and run the core test suite.
- [x] Run the full current suite and all static checks.
- [x] Run `uv lock --check` and `git diff --check`.

### Task 4: Rebuild local 0.2.0 artifacts

- [x] Build wheel and sdist into `dist/`.
- [x] Run `twine check`.
- [x] Inspect wheel metadata for DRF `>=3.16.1,<3.18` and the optional DMR extra.
- [x] Install the wheel in a clean temporary environment.
- [x] Report paths, checksums, and confirm no commit, push, or release occurred.
