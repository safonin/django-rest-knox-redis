# DMR 0.15 Compatibility Design

## Context

django-modern-rest 0.15.0 added the abstract
`www_authenticate_challenge` property to its authentication base class. The
Knox adapters already emit `WWW-Authenticate: Token` in runtime errors and
OpenAPI responses, but DMR 0.15 calls the new property while constructing
endpoint metadata. The inherited abstract implementation raises
`NotImplementedError`, causing the rolling DMR CI jobs to fail before release.

## Decision

Implement `www_authenticate_challenge` on the shared Knox DMR adapter and return
the Knox scheme name `Token`. The property is additive and harmless on DMR
0.12–0.14, so the public extra remains `django-modern-rest>=0.12` without an
artificial upper bound or version branches.

Add a direct regression assertion for the property. Test the DMR suite against
0.12.0, 0.14.0, and 0.15.0, regenerate the development lock to 0.15 when the
resolver permits it, and rerun all release checks.

## Rejected alternatives

- Pinning DMR below 0.15 would leave current users unsupported and contradict
  the open-ended `dmr` extra.
- Replacing DMR response-spec generation would copy framework behavior and
  create a larger compatibility surface than the new public property requires.

## Release gate

Push the compatibility fix and wait for the complete GitHub Actions matrix.
Create `v0.2.0` and publish the GitHub Release only after that run is green.
