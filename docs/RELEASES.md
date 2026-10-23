# Releases: versions, signed images, installing and upgrading

For the OPD Saathi team (cutting releases) and hospital IT (installing them). Phase 7, tasks
7.14.1 to 7.14.6.

## Versions

- Semantic versions in the `VERSION` file: `MAJOR.MINOR.PATCH`. A patch fixes; a minor release
  adds without breaking an installation; a major release needs the release notes read first.
- `1.0.0` is cut only after the go-live gate (`docs/GO_LIVE_CHECKLIST.md`); the `0.x` releases
  are for staging and pilots with synthetic data.
- Every release has a section in `CHANGELOG.md`: first in plain language for hospital staff and
  administrators, then for IT, then the technical detail. A release cannot be cut without it.
- The running version is shown by `https://<host>/api/healthz` (and in Settings → System).

## Cutting a release (the team)

1. On `main`, with CI green: set `VERSION`, move the "Unreleased" notes in `CHANGELOG.md` under
   `## [X.Y.Z]`, merge that through a pull request.
2. Tag the merge commit and push the tag:

   ```sh
   git tag -s vX.Y.Z -m "OPD Saathi X.Y.Z" && git push origin vX.Y.Z
   ```

3. The release workflow (`.github/workflows/release.yml`) builds the API, web and PostgreSQL
   images once, scans them (a fixable high or critical finding stops the release), pushes them
   to `ghcr.io/shashi1543-07/opd-saathi-*` by digest, signs them with cosign (keyless: the
   signature names this repository's release workflow and the tag), attaches each SBOM as a
   signed attestation, and publishes the GitHub release with:
   - `docker-compose.images.yml`: the exact images of this release, by digest;
   - the SBOMs (`opd-saathi-*.cdx.json`);
   - `opd-saathi-X.Y.Z-images.tar.gz`: every image the stack runs, for an offline install;
   - `SHA256SUMS` and its signature `SHA256SUMS.sigstore.json`.
4. Deploy it to staging, run the restore drill and the alert drill there, then announce it.

## Hotfixes

Urgent fixes for a released version go on a branch `release/X.Y` cut from its tag; the fix is
made there first (with its tests), released as `X.Y.(Z+1)`, then merged or cherry-picked into
`main`. Never release from a branch that has not passed CI.

## Checking a release before installing (hospital IT)

With cosign installed (`https://docs.sigstore.dev/cosign/system_config/installation/`):

```sh
cosign verify ghcr.io/shashi1543-07/opd-saathi-api@sha256:<digest> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/Shashi1543-07/OPD-Sathi/\.github/workflows/release\.yml@refs/tags/v'
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --certificate-identity-regexp '^https://github\.com/Shashi1543-07/OPD-Sathi/\.github/workflows/release\.yml@refs/tags/v'
sha256sum -c SHA256SUMS
```

`setup-hospital.sh --release-images` verifies the image signatures itself when cosign is
installed and refuses an image whose signature does not verify.

## Installing or upgrading from a release

```sh
cd /opt/opd-saathi
sudo git fetch --tags && sudo git checkout vX.Y.Z          # scripts and configuration
# download docker-compose.images.yml from the release page into /opt/opd-saathi
sudo ./scripts/setup-hospital.sh --host <name> --release-images docker-compose.images.yml
```

The script takes a full backup before migrating, applies the migrations, starts the stack and
checks it; the boot unit then uses the same images. **Without internet:** copy the release's
`.tar.gz` to the server, check it against `SHA256SUMS`, `docker load < opd-saathi-X.Y.Z-images.tar.gz`,
then run the same command.

## Rolling back

Check out the previous tag, download its `docker-compose.images.yml`, and run the setup script
again with it. Migrations follow expand, migrate, contract (`CONTRIBUTING.md`), so the previous
release runs on the upgraded database; a release whose migration cannot be reversed says so in
its notes, and then rolling back means restoring the backup taken before the upgrade
(`docs/runbooks/restore.md`).

## Staging (task 7.14.1)

A production-like server with synthetic data only, installed exactly as a hospital's (same
script, overlay and monitoring) with a self-signed or staging certificate
(`setup-hospital.sh --self-signed`). Every release goes to staging first; training, the alert
drill, the restore drill and upgrade rehearsals happen there. Until a staging server is
provisioned, CI's production smoke test is the stand-in: it starts the production stack from
scratch on every change, drives it through HTTPS, checks monitoring, backs up and restores.
