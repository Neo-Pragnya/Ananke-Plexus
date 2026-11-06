---
title: Security findings
description: How a scanner finding is recorded, triaged, fixed or excepted, and verified for Ananke Plexus, Dev Guru and Dex Trove.
---

# Security findings

This is how the three packages handle a finding from Black Duck, a package firewall or our own scans. It follows
the security remediation plan. **Black Duck and your repository firewall decide what's quarantined.** Our scans
corroborate it. A clean local scan is never a reason to bypass a quarantine.

## What every release produces

The release gate (`scripts/scan_dist.py`) runs on every pull request and every release. It's the same file in all
three repositories. Its report is attached to the release run as `dist-scan-<tag>`:

| File | What it is |
| --- | --- |
| `summary.md` | each check's verdict, what's degraded, exceptions due for review, provenance and package maturity |
| `findings.json` | one record per finding: check, category, where, whether it ships, disposition, owner, review date |
| `triage.md` | each licence name in our files that isn't our licence, with the reason to give Black Duck |
| `sbom-*.cdx.json` | a CycloneDX SBOM of everything the wheel resolves to, which Black Duck can import and compare |
| `scan.json` | everything above, machine-readable, with the commit, each artifact's sha256 and every tool's version |

## Triage: which rule caused it

Don't read "quarantined" as "has a CVE". First find out which rule fired.

| Category | What our gate checks | What to do |
| --- | --- | --- |
| Malware, suspicious behaviour | GuardDog rules, malware signatures (keychain dumps, browser stores, pipe-to-shell, encoded PowerShell, exec of decoded data, webhooks, hidden Unicode), Bandit HIGH | Stop the release. Remove it or fix it. Accept only with a reviewed reason, limited to that file |
| Vulnerability (CVE, BDSA) | pip-audit, Grype and osv-scanner on the resolved tree, and on the oldest versions each range allows | Raise the direct dependency, and update the lock file with `uv lock`. Never edit a lock file by hand |
| Licence | every resolved dependency's licence and what pulls it in; bundled code against `License-Expression` | Prefer a permissive alternative. Otherwise accept it with a reason, and ship the notice |
| False identification | copyleft names in our own files (`triage.md`); our metadata (version 2.4, licence classifier) | Give the Black Duck administrator the file, the reason and the evidence. Ask them to correct that match only |
| Package age | dependencies under 14 days old (reported) | Install with `uv pip install ... --exclude-newer-package NAME=DATE` until they mature |
| Cause unclear | | Keep it blocked. Ask the Black Duck administrator for the exact rule (below) |

## Exceptions

Every accepted finding lives in `.scan-dist.toml`. It has a reason, and a `[review]` owner and `review_by` date,
which an entry can override. **The gate blocks a release once a review date has passed,** and lists anything due
within 30 days. An exception is limited to the files reviewed, so the same rule anywhere else still blocks. Changes to
`.scan-dist.toml`, the gate and the workflows need the owner's review (`.github/CODEOWNERS`).

## A degraded scan isn't a pass

If a second CVE scanner or the SBOM writer is missing, or Grype's database is more than 5 days old, the scan is
**degraded**. Locally that's INCOMPLETE. In CI, which runs with `--require-all`, it fails. CI installs every scanner at a
pinned version and checks its sha256.

## Asking the Black Duck administrator

When a package is quarantined and the cause isn't clear, send this. Fill in the parts in angle brackets:

> **Package:** `<name>==<version>`, sha256 `<from summary.md, Provenance>`, built from commit `<commit>`.
>
> **Please send:**
> - the exact policy rule and violation name;
> - the matched component, version and origin, with the match type (manifest, binary, signature or snippet) and the
>   file it matched;
> - any CVE or BDSA ids.
>
> **Our evidence:** the release's scan report (`dist-scan-<tag>`), with its CycloneDX SBOM, licence inventory and
> `triage.md`. It lists files that name a licence the package isn't under, with the reason for each.
>
> **If it's a false identification,** please correct that match with the status the evidence supports. Please don't use a
> broad exception.

Record the answer and the change against the finding in `findings.json`. Then rescan the rebuilt artifact, and close
the finding only when Black Duck shows the component corrected, updated, or under an approved exception with an expiry
date.

## Before a push

`pre-commit install --hook-type pre-push` adds a hook that runs osv-scanner on the lock files and Semgrep's
security-audit and secrets rules. It gives early feedback, but you can skip it: CI is what enforces the rules.
