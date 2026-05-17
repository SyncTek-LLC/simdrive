# Security Policy

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Email: **security@simdrive.dev**

> **Inbox status (2026-05-17):** the `security@simdrive.dev` domain is being
> provisioned. Until that inbox is live, reports forwarded to that address will
> route to the maintainer at `maurice.carrier@synctek.io`. You may write to the
> maintainer directly during this transition window. The handle will not change.

We acknowledge receipt within **2 business days**, provide a preliminary
assessment within **7 days**, and target a fix or mitigation within
**90 days** of report (coordinated disclosure). For actively exploited
issues, we will work with you on an accelerated timeline.

If you need encryption, please request our PGP key in your first email and
we will send it back from `security@simdrive.dev` (the key fingerprint will
be published here once the inbox is live).

## Supported Versions

SimDrive is in 1.0 alpha (`1.0.0aN`). During the alpha and beta windows,
**only the latest published `1.0.0aN` / `1.0.0bN` release receives security
fixes.** When 1.0.0 GA ships, the supported window becomes:

| Version line | Supported |
|--------------|-----------|
| 1.0.x        | Latest patch release only |
| 1.0.0aN / 1.0.0bN (pre-GA) | Latest published pre-release only |
| Pre-rebrand `specterqa-ios` package | **Not supported** — migrate to `simdrive` |

## Scope

Reports about any of the following are in scope:

- The published `simdrive` PyPI package (wheel + sdist).
- The MCP server binary (`simdrive` console script).
- The native HID helper (`simdrive-input`).
- The bundled WebDriverAgent fork (`simdrive/wda/`).
- The CI/release workflows under `.github/workflows/` insofar as they could
  produce a tainted artifact (e.g. publish-workflow tampering, lock-file
  poisoning).

Out of scope:

- Findings that require already-compromised local OS / Xcode / simulator runtime.
- Findings in user app code under test (we drive arbitrary apps; bugs in the
  app are the app author's responsibility).
- DoS via exhausting local simulator resources.

## Threat Model Summary

SimDrive ships across three deployment surfaces, each with a distinct trust
boundary:

**(1) PyPI package surface.** A compromised release would let an attacker run
arbitrary code on every dev machine that runs `pip install simdrive`. Mitigations
in this layer: PyPI Trusted Publishers (OIDC, no long-lived tokens — see
`.github/workflows/specterqa-ios-publish.yml`); a pinned `simdrive/requirements.lock`
to make dep-confusion attacks visible in PR diff; pip-audit nightly on the lock;
gitleaks blocking on every push to prevent accidentally checking in credentials
that would let an attacker masquerade as a maintainer. Publishing requires
human approval today (manual `git tag` push); roadmap item to require PyPI 2FA
on the maintainer account is tracked separately as a Chairman HITL item.

**(2) Local MCP server surface.** SimDrive runs on a developer's machine and
drives a local iOS simulator. It does NOT open a network port for inbound
traffic; it speaks MCP over stdio to its parent process (Claude Code, Claude
Desktop, etc.). The trust boundary is therefore "anything that can spawn the
parent MCP client can already control the simulator." Risks: an MCP client
that is itself compromised could exfiltrate screenshots and recordings — these
may contain PII or credentials from the app under test. Mitigation is the
redaction layer (see `simdrive/docs/REDACTION_SPEC.md`): SecureField masking
on screenshot capture, clipboard scrubbing during recording, and a default-on
opt-out flag for explicit unredacted runs (testing only).

**(3) Hosted future surface (not yet shipped).** A future hosted-runner
offering would terminate user-supplied API keys (BYOK), execute arbitrary
recordings on shared iOS simulators, and expose recording artifacts via
authenticated download URLs. Threat model for that layer will be drafted
ahead of the first hosted-runner GA and will include: tenant isolation
between simulator runs, ephemeral simulator snapshots reset between tenants,
audit log for every recording read, and a separate disclosure address. Until
that surface ships, this section is informational only.

## Related Documents

- `simdrive/docs/REDACTION_SPEC.md` — screenshot + recording redaction design
  (spec only as of W1; implementation tracked in W2 of INIT-2026-549).
- `.github/workflows/security.yml` — pip-audit + gitleaks CI.
- `.github/workflows/codeql.yml` — CodeQL static analysis.
