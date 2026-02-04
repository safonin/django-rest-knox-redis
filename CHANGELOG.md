# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2024-XX-XX

### Added

- Initial release
- `TokenAuthentication` class with Redis caching layer
- `TokenCache` class for Redis operations via django-redis
- `LoginView`, `LogoutView`, `LogoutAllView` with automatic cache invalidation
- Signal handlers for cache invalidation on direct ORM token deletion
- Graceful fallback to database when Redis is unavailable
- Comprehensive test suite with 19 tests
- Full documentation with performance comparisons

### Features

- **Redis-first authentication**: Check Redis cache before database lookup
- **Automatic caching**: Cache tokens on first database lookup
- **Cache invalidation**: Automatic invalidation on logout/logoutall/token deletion
- **User index**: Efficient bulk token deletion for `logoutall` operations
- **Configurable**: Customizable cache alias, key prefix, and enable/disable toggle
- **Django-redis integration**: Uses Django's cache framework for Redis connectivity

[Unreleased]: https://github.com/yourusername/django-rest-knox-redis/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/yourusername/django-rest-knox-redis/releases/tag/v0.1.0
