# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-23

### Added

- Optional `dmr = ["django-modern-rest>=0.12"]` extra.
- Public synchronous DMR adapters `KnoxRedisSyncAuth` and
  `KnoxDatabaseSyncAuth`, including Knox header semantics, DMR error mapping,
  request attributes, `WWW-Authenticate: Token`, and OpenAPI `apiKey` security.
- Typed, reloadable `MAX_TOKEN_CACHE_TTL` setting, defaulting to 300 seconds.
- PEP 561 `py.typed` marker and a portable uv lock file.
- Regression coverage for full HTTP login/logout/logout-all flows, ORM and
  cascade deletion, invalidation failures, expiry, inactive/deleted users,
  SQL/Redis operation counts, Redis outages, AUTO_REFRESH, DMR chains, and
  OpenAPI.

### Changed

- Cache hits now return the real, database-backed Knox `AuthToken`; the cache
  no longer returns a model-like object that lacks `delete()`.
- Every cache hit authoritatively checks the exact token and user row in one
  joined SQL query. A stale Redis entry can no longer authorize a revoked
  token.
- Cache entries use an absolute bounded expiry that never exceeds the Knox token
  expiry. Non-expiring Knox tokens use `MAX_TOKEN_CACHE_TTL`.
- Token deletion uses fail-closed `pre_delete` invalidation plus a best-effort
  `on_commit` cleanup. Logout, logout-all, ORM/admin deletion, and user cascades
  share the same policy.
- `AUTO_REFRESH=True` bypasses Redis and delegates renewal completely to Knox,
  preserving `MIN_REFRESH_INTERVAL` and `AUTO_REFRESH_MAX_TTL`.
- Supported runtime matrix is Python 3.10–3.14, Django 5.2/6.0, DRF
  3.16.1–3.17, django-rest-knox 5.0.4–5.1, and django-redis 6–7. Django 6.0
  requires Python 3.12+.
- CI now uses only valid Python/Django combinations, asserts resolved package
  versions, tests the DMR lower bound and current resolver, and validates built
  artifacts before a future publish job can run.
- README performance claims without a reproducible benchmark were removed and
  replaced by regression-tested total SQL and Redis operation counts.
- Package metadata, author email, badges, repository/changelog URLs, and
  internal version were aligned for 0.2.0.

### Fixed

- Full Knox `LogoutView` after a cache hit no longer raises `AttributeError`.
- Redis invalidation failure can no longer leave a database-revoked token
  usable from an unbounded entry.
- Cache-hit AUTO_REFRESH no longer silently skips Knox renewal semantics.
- Short-lived tokens no longer shorten the user-token index TTL below the
  lifetime of other bounded cached entries.

### Security

- Revocation fails closed when Redis cannot confirm pre-delete invalidation.
- Post-commit cleanup failure is contained by database-authoritative cache hits
  and the configured maximum cache TTL.

## [0.1.3] - 2026-02-10

### Changed

- Aligned the package version published from project metadata.

## [0.1.2] - 2026-02-10

### Changed

- Updated the release workflow configuration.

## [0.1.1] - 2026-02-10

### Changed

- Corrected repository project URLs.

## [0.1.0] - 2026-02-04

### Added

- Initial Redis caching layer, Knox authentication class, login/logout views,
  ORM invalidation signal, and test suite.

[Unreleased]: https://github.com/safonin/django-rest-knox-redis/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/safonin/django-rest-knox-redis/compare/v0.1.3...v0.2.0
[0.1.3]: https://github.com/safonin/django-rest-knox-redis/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/safonin/django-rest-knox-redis/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/safonin/django-rest-knox-redis/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/safonin/django-rest-knox-redis/releases/tag/v0.1.0
