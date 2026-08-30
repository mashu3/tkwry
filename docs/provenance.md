# Release provenance

How to **verify** tkwry wheels and sdists from [GitHub
Releases](https://github.com/mashu3/tkwry/releases). PyPI publishes the same
artifacts via Trusted Publishing after the release workflow passes quality
gates (see [CHANGELOG](../CHANGELOG.md)).

| Topic | Doc |
|-------|-----|
| Install / wheels | [README — Installation](../README.md#-installation) |
| App bundling | [Packaging notes](packaging.md) |

## SHA-256 checksums

Every GitHub Release includes `SHA256SUMS` next to the `.whl` and `.tar.gz`
files. The file is generated on the release runner from the exact bytes uploaded
to the release.

**Verify** (Linux / macOS):

```bash
cd dist
sha256sum -c SHA256SUMS
```

**Verify** (Windows PowerShell):

```powershell
Get-Content SHA256SUMS | ForEach-Object {
  $parts = $_ -split '\s+', 2
  $hash = $parts[0]
  $file = $parts[1]
  $actual = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLower()
  if ($actual -ne $hash) { throw "checksum mismatch: $file" }
}
Write-Host "checksums OK"
```

Compare against the checksums on the release you downloaded — not an older
tag.

## Build attestations

The release workflow attests each wheel and sdist with [GitHub artifact
attestations](https://docs.github.com/en/actions/security-guides/using-artifact-attestations-to-establish-provenance-for-builds)
(SLSA build provenance). View attestations on the release page or with the
GitHub CLI:

```bash
gh attestation verify dist/tkwry-*.whl -R mashu3/tkwry
```

Attestations bind artifacts to the public `release.yml` workflow on tag push.
They do **not** replace checksum verification when you mirror files outside
GitHub.

## Maintainer notes (optional `cargo audit`)

Rust dependency advisories are **not** a 0.1.x CI gate. Before tagging, you may
run:

```bash
cargo install cargo-audit --locked
cargo audit
```

Record any unfixed advisories in the release discussion or CHANGELOG if they
affect shipped wheels. Re-run after ``Cargo.lock`` or wry crate bumps.

## Related

- [Packaging notes](packaging.md)
- [CHANGELOG](../CHANGELOG.md)
