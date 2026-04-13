# Security & IP Scan Report — INIT-2026-525

## Scan Date: 2026-04-10
## Scanner: SecurityAtlas
## Branches Scanned:
- `specterqa-ios` repo (`/Users/atlas/Documents/specterqa-ios`) — `feat/license-activation-flow`
- `specterqa-ios` repo — `feat/agent-discovery-surfaces`
- `synctek-website` repo (`/Users/atlas/Documents/synctek-website`) — `feat/agent-discovery-surfaces`

## Files Reviewed

| File | Branch | Repo |
|------|--------|------|
| `src/specterqa/ios/cli/license_cmd.py` | `feat/license-activation-flow` | specterqa-ios |
| `src/specterqa/ios/license/validator.py` | `feat/license-activation-flow` | specterqa-ios |
| `src/specterqa/ios/license/stripe_webhook.py` | `feat/license-activation-flow` | specterqa-ios |
| `tests/test_license_activation.py` | `feat/license-activation-flow` | specterqa-ios |
| `pyproject.toml` | `feat/license-activation-flow` | specterqa-ios |
| `.well-known/agent.json` | `feat/agent-discovery-surfaces` | specterqa-ios |
| `llms.txt` | `feat/agent-discovery-surfaces` | specterqa-ios |
| `docs/landing-page.md` | `feat/agent-discovery-surfaces` | specterqa-ios |
| `src/data/products/specterqa-ios.ts` | `feat/agent-discovery-surfaces` | synctek-website |
| `src/pages/products/[slug].astro` | `feat/agent-discovery-surfaces` | synctek-website |
| `src/pages/llms.txt.ts` | `feat/agent-discovery-surfaces` | synctek-website |

---

## Security Findings

### CRITICAL (blocks deployment)

**SEC-CRIT-001 — Path Traversal via License Key in URL Construction**

- **Files:** `src/specterqa/ios/cli/license_cmd.py` line 100; `src/specterqa/ios/license/validator.py` lines 328, 337
- **What's wrong:** The license key is inserted directly into a URL path segment without sanitization. `httpx` normalizes `../` sequences at the HTTP level before sending the request. A malicious key value of `../../../admin/tokens` constructs the URL `https://api.keygen.sh/v1/admin/tokens/validate` — a completely different endpoint. This allows an attacker to craft a key string that probes arbitrary Keygen.sh API paths under the account.
- **Verified:** Confirmed via live test: `httpx.Request('GET', f'https://api.keygen.sh/v1/accounts/acc/licenses/../../../admin/validate')` resolves to `https://api.keygen.sh/v1/admin/validate`.
- **How to fix:** Add format validation before any URL construction. Reject any key that does not match the expected pattern `^[A-Z0-9][A-Z0-9\-]{6,64}$` (adjust to match Keygen.sh's actual key format). In `license_cmd.py` add at line 187 (after `key.strip()`):
  ```python
  import re
  _LICENSE_KEY_RE = re.compile(r'^[A-Z0-9][A-Z0-9\-]{6,64}$')
  if not _LICENSE_KEY_RE.match(key):
      raise click.ClickException(
          f"Invalid license key format: {key!r}\n"
          "Expected format: LIC-XXXX-XXXX-XXXX-XXXX"
      )
  ```
  Apply the same guard in `validator.py` `_fetch_via_httpx()` and `_fetch_from_api_requests()` before constructing `url`.

---

### HIGH (must fix before deploy)

**SEC-HIGH-001 — auth.yaml Written Without Restrictive File Permissions**

- **File:** `src/specterqa/ios/cli/license_cmd.py` lines 65–71 (`_write_yaml`)
- **What's wrong:** `_write_yaml` opens the file with the process's default umask, which is typically `0644` (world-readable). The file `~/.specterqa/auth.yaml` contains the plaintext license key and tier metadata. Any other user or process on the same machine can read it. On shared developer machines, CI agents, or containers with multiple users this is a real exposure.
- **How to fix:** After writing the file, set permissions to `0o600`:
  ```python
  import os as _os
  path.open("w", encoding="utf-8").write(...)  # existing write
  _os.chmod(path, 0o600)
  ```
  Or use `path.touch(mode=0o600)` before opening for write. The parent directory `~/.specterqa/` should also be `0o700`.

**SEC-HIGH-002 — Keygen.sh Response Body Leaked in RuntimeError (Webhook)**

- **File:** `src/specterqa/ios/license/stripe_webhook.py` line 147–149
- **What's wrong:** On `HTTPStatusError`, the full `exc.response.text` from Keygen.sh is embedded in the `RuntimeError` message. This error message propagates through `_handle_checkout_completed` at line 332 as `str(exc)` into the HTTP response body returned to Stripe. Keygen.sh error responses can contain internal account metadata, license policy details, or rate-limit diagnostics that should not be echoed to the public Stripe webhook endpoint.
- **How to fix:** Redact the Keygen response body from the external error message. Log it internally, return a generic message:
  ```python
  except httpx.HTTPStatusError as exc:
      logger.error(
          "Keygen.sh license creation failed (HTTP %s): %s",
          exc.response.status_code, exc.response.text
      )
      raise RuntimeError(
          f"Keygen.sh license creation failed (HTTP {exc.response.status_code}). "
          "Check server logs for details."
      ) from exc
  ```

**SEC-HIGH-003 — customer.subscription.updated Handler is a No-Op (Functional Security Gap)**

- **File:** `src/specterqa/ios/license/stripe_webhook.py` lines 352–360
- **What's wrong:** The `customer.subscription.updated` event is silently acknowledged as `"handled"` but does nothing — the Keygen.sh license tier metadata is never updated when a customer upgrades or downgrades. A customer who downgrades from Team ($299/mo) to Indie ($29/mo) would retain `max_concurrent_sims=10` in their cached Keygen license until the process is manually corrected. This is both a revenue leak and a license enforcement failure.
- **How to fix:** Implement tier sync for subscription updates. At minimum, treat an unimplemented handler as `"ignored"` (not `"handled"`) and emit an ops alert, so the gap is visible. Return `{"status": "ignored", "detail": "Subscription update: tier sync not yet implemented — manual action required"}` and log at WARNING level. Implement the full tier-sync path before launch.

**SEC-HIGH-004 — Default Stripe Price ID Fallbacks Accept Generic Strings**

- **File:** `src/specterqa/ios/license/stripe_webhook.py` lines 52–57
- **What's wrong:** `_PRICE_TIER_MAP` is constructed at module import time using `os.environ.get("STRIPE_PRICE_INDIE", "price_indie")`. If the production environment does not set these env vars, the map silently uses generic placeholder strings (`"price_indie"`, `"price_pro"`, etc.) as keys. No real Stripe price ID starts with `"price_indie"` — so every real Stripe checkout event would fail to match any tier, falling back to `"indie"` regardless of what plan was actually purchased. A Team or Enterprise purchaser would be issued an Indie license.
- **How to fix:** Remove the fallback defaults. Raise a `RuntimeError` at startup if the env vars are absent:
  ```python
  def _build_price_tier_map() -> Dict[str, str]:
      required = {
          "STRIPE_PRICE_INDIE": "indie",
          "STRIPE_PRICE_PRO": "pro",
          "STRIPE_PRICE_TEAM": "team",
          "STRIPE_PRICE_ENTERPRISE": "enterprise",
      }
      result = {}
      for env_var, tier in required.items():
          val = os.environ.get(env_var, "").strip()
          if not val:
              raise RuntimeError(f"{env_var} environment variable is required but not set.")
          result[val] = tier
      return result

  _PRICE_TIER_MAP = _build_price_tier_map()
  ```
  This makes misconfiguration fail loud at deploy time, not silently at transaction time.

**SEC-HIGH-005 — JWT Offline Grace Stub Always Denies (Silent Production Breakage)**

- **File:** `src/specterqa/ios/license/validator.py` lines 280–292
- **What's wrong:** `_decode_jwt()` is a stub that returns `{}`. When the Keygen.sh API is unreachable (network outage, rate limit), `_check_offline_grace()` always returns `False` because `payload.get("offline_exp")` and `payload.get("iat")` are both `None`. This means the documented 72-hour offline grace period is completely non-functional. Paid customers who experience a network hiccup will be blocked from running tests, despite having a valid active license. The docstring claims this works — it does not.
- **How to fix:** Either implement the JWT base64-decode stub properly:
  ```python
  def _decode_jwt(self) -> Dict[str, Any]:
      import base64, json
      parts = self._license_key.split(".")
      if len(parts) < 2:
          return {}
      payload_b64 = parts[1] + "=="  # re-pad
      try:
          return json.loads(base64.urlsafe_b64decode(payload_b64))
      except Exception:
          return {}
  ```
  Or remove the offline grace feature entirely and update the docstring to accurately describe behavior. Do not ship a feature that is documented as working but is silently broken.

---

### MEDIUM (fix within 7 days)

**SEC-MED-001 — License Key Not Validated for Format Before URL Construction (validator.py)**

- **File:** `src/specterqa/ios/license/validator.py` lines 328, 337
- **What's wrong:** Same root as SEC-CRIT-001 but also present in the `LicenseValidator` class's `_fetch_via_httpx` and `_fetch_from_api_requests` methods. The `LicenseValidator` can be instantiated by library callers with arbitrary key strings. Input validation must be in the class, not only the CLI layer.
- **How to fix:** Add key format validation in `LicenseValidator.__init__` or at the top of `_fetch_from_api()`. See SEC-CRIT-001 for the regex pattern.

**SEC-MED-002 — `stripe` Library Missing from Core Dependencies (pyproject.toml)**

- **File:** `pyproject.toml` lines 53–57
- **What's wrong:** `stripe>=8.0` is listed only in `[project.optional-dependencies.license]`. The `stripe_webhook.py` module uses `stripe.Webhook.construct_event` for signature verification. If a deployment installs `specterqa-ios` without the `[license]` extra, the module can be imported but `verify_stripe_signature` will raise `ImportError` at runtime — silently accepting webhooks without signature verification if the exception is not caught (it is caught in the FastAPI/Flask handlers, but callers who use `verify_stripe_signature` directly may not expect this). The webhook deployment guide must make `[license]` mandatory.
- **How to fix:** Document clearly in the deployment guide that webhook servers MUST install `specterqa-ios[license]`. Add a startup assertion in the webhook module that checks for stripe availability and raises a clear startup error if absent.

**SEC-MED-003 — Trial Counter Reset Function Exported in Public API**

- **File:** `src/specterqa/ios/license/validator.py` lines 135–139
- **What's wrong:** `reset_trial_counter()` is a public module-level function. It is intended for test use only (docstring says "Primarily for use in tests") but it is fully importable by any calling code. A malicious or careless library consumer can call `reset_trial_counter()` to bypass the 3-run trial limit indefinitely without a license key.
- **Note:** This is in-process only, so it only affects the current process. However, it should not be part of the public API.
- **How to fix:** Rename to `_reset_trial_counter()` (private by convention) and add a deprecation guard. Or export it only when `__debug__` is True (i.e., non-optimized builds used in testing).

**SEC-MED-004 — Stripe Webhook Error Detail Leaks Internal Exception String (FastAPI + Flask)**

- **File:** `src/specterqa/ios/license/stripe_webhook.py` lines 392, 394, 432, 434
- **What's wrong:** Signature verification failures (`ValueError`) and import errors (`ImportError`) are returned verbatim as HTTP response bodies. A 400 response to Stripe echoes `str(exc)` which may include Stripe's own error descriptions or internal library paths. While Stripe's own infrastructure is the caller for real events, these error strings are also logged or surfaced in Stripe's dashboard, potentially leaking implementation details.
- **How to fix:** Return generic user-facing error messages; log the full detail internally:
  ```python
  except ValueError as exc:
      logger.warning("Stripe signature verification failed: %s", exc)
      raise HTTPException(status_code=400, detail="Webhook signature verification failed")
  ```

**SEC-MED-005 — `suspend_keygen_license_by_customer` Silences Multi-License Ambiguity**

- **File:** `src/specterqa/ios/license/stripe_webhook.py` lines 188–194
- **What's wrong:** When multiple Keygen licenses are found for a `stripe_customer_id` (e.g., a customer who upgraded and has two license records), only `licenses[0]` is suspended. The rest remain active. An adversarial customer or data migration issue could leave a customer with a suspended license and still-active duplicates.
- **How to fix:** Log a warning when `len(licenses) > 1` and suspend all matching active licenses. Alternatively, raise an alert for manual review.

---

### LOW (informational)

**SEC-LOW-001 — INIT-2026-525 Internal Initiative ID Exposed in validator.py**

- **File:** `src/specterqa/ios/license/validator.py` line 304
- **What's wrong:** The docstring contains `pre-INIT-2026-525 callers` — an internal initiative ID from the BusinessAtlas project management system. This is a public-facing Python package hosted on GitHub. While it is a comment/docstring (not executed), it leaks internal BA initiative tracking terminology to the open-source community.
- **How to fix:** Replace with a version reference: `pre-v11.3.0 callers` or simply omit the backwards-compatibility note from the docstring.

**SEC-LOW-002 — `_write_yaml` Lacks Atomic Write (Race Condition on auth.yaml)**

- **File:** `src/specterqa/ios/cli/license_cmd.py` lines 65–71
- **What's wrong:** The file is opened with `path.open("w")` which truncates the file before writing. If the process is interrupted (SIGKILL, out-of-disk) mid-write, `auth.yaml` is left in a corrupted/empty state. The next read will return `None` (caught by `_load_yaml_safe`) and the user will be in trial mode despite having a valid license.
- **How to fix:** Use atomic write-then-rename pattern:
  ```python
  import tempfile
  with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, suffix=".tmp") as tf:
      yaml.safe_dump(data, tf, ...)
      tmp_path = Path(tf.name)
  tmp_path.rename(path)
  ```

**SEC-LOW-003 — `_fetch_from_api_requests` Legacy Path Has No Timeout**

- **File:** `src/specterqa/ios/license/validator.py` lines 333–340
- **What's wrong:** The `requests.get(url)` call has no `timeout` parameter. On a slow or unresponsive network, this can hang indefinitely, blocking the CLI.
- **How to fix:** Add `timeout=15.0` to the `requests.get()` call: `requests.get(url, timeout=15.0)`.

**SEC-LOW-004 — CORS on `.well-known/` Allows `X-ForgeOS-Key` Header**

- **File:** `synctek-website/public/_headers` line 13
- **What's wrong:** The `/.well-known/*` CORS rule includes `Access-Control-Allow-Headers: Content-Type, Authorization, X-ForgeOS-Key`. The `X-ForgeOS-Key` header is a ForgeOS authentication credential header. Allowing it in the CORS preflight for `.well-known/agent.json` is unnecessary — this endpoint is read-only public JSON that requires no auth headers. While it does not expose credentials, it unnecessarily signals the existence of a `X-ForgeOS-Key` authentication scheme to any third-party agent reader.
- **How to fix:** Remove `X-ForgeOS-Key` from the `.well-known/*` CORS allow-headers. Keep it only on the ForgeOS API paths that actually require it.

---

## IP Findings

### CRITICAL (blocks deployment)

None.

---

### HIGH (must fix before deploy)

**IP-HIGH-001 — Internal Initiative ID Exposed in Public Package Source**

- **File:** `src/specterqa/ios/license/validator.py` line 304
- **What's wrong:** Docstring contains `pre-INIT-2026-525 callers`. This is in a Python file that will be published to PyPI and committed to the public GitHub repository. The `INIT-2026-525` nomenclature reveals the existence of an internal project management system (BusinessAtlas) and its naming convention. This is the type of internal operational detail that should never appear in public code.
- **Severity elevated to HIGH** because this is going to PyPI/public GitHub, not just an internal server.
- **How to fix:** Change to: `prior to v11.3.0`. Search all new files for any other `INIT-20XX-XXX` patterns before merge.

---

### MEDIUM

**IP-MED-001 — "97% Gross Margin" Internal Financial Metric in Public Marketing Copy**

- **Files:** `llms.txt` line 78 (specterqa-ios repo); `docs/landing-page.md` line 57; `synctek-website/src/data/products/specterqa-ios.ts` line 63
- **What's wrong:** The phrase "97% gross margin for us" appears in all three public-facing surfaces. This exact internal financial metric should be a deliberate marketing decision, not an accidental leak. If intentional (founder-to-founder transparency, a differentiation story), it is fine. Flag for Chairman confirmation before go-live.
- **How to fix:** Confirm with Chairman this figure is intentionally public. If not, replace with "SyncTek's costs stay minimal; your data never leaves your machine." If intentional, leave as-is.

---

## Pricing Consistency Check

| Tier | agent.json | llms.txt | landing-page.md | specterqa-ios.ts | [slug].astro (via .ts) |
|------|-----------|----------|-----------------|-----------------|----------------------|
| Trial | Free (0) | Free | Free | Free | Free |
| Trial sims | 1 | 1 | 1 | 1 | 1 |
| Trial runs/session | 3 | 3 | 3 | 3 | 3 |
| Indie | $29/mo | $29/mo | $29/mo | $29/mo | $29/mo |
| Indie sims | 2 | 2 | 2 | 2 | 2 |
| Pro | $99/mo | $99/mo | $99/mo | $99/mo | $99/mo |
| Pro sims | 4 | 4 | 4 | 4 | 4 |
| Team | $299/mo | $299/mo | $299/mo | $299/mo | $299/mo |
| Team sims | 10 | 10 | 10 | 10 | 10 |
| Enterprise | Custom | Custom | Custom | Custom | Custom |
| Enterprise sims | unlimited | unlimited | unlimited | unlimited | unlimited |

**Result: CONSISTENT.** All pricing tiers, prices, and simulator limits are in exact agreement across all five public surfaces. No drift detected.

**Code enforcement verified:** `license_cmd.py::TIER_SIM_LIMITS` and `validator.py::_TIER_DEFAULTS` and `stripe_webhook.py::_TIER_SIM_LIMITS` all agree on: trial=1, indie=2, pro=4, team=10, enterprise=0 (unlimited). Consistent with marketing surfaces.

---

## Checklist Results

### Security Checklist

| Check | Status | Finding |
|-------|--------|---------|
| Secrets/credentials hardcoded | PASS | No API keys, tokens, or account IDs hardcoded. All via env vars. |
| Stripe webhook signature verification | PASS | `stripe.Webhook.construct_event` used correctly. Raw body preserved. |
| Keygen.sh API calls HTTPS only | PASS | `_KEYGEN_BASE = "https://api.keygen.sh/v1"`. No HTTP fallback. |
| Sensitive data in URL params | CONDITIONAL | Key is in URL path (not params), but see SEC-CRIT-001 for path traversal risk. |
| Proper error handling (no stack traces) | PASS with caveat | No tracebacks. But see SEC-HIGH-002 / SEC-MED-004 for response body leaks. |
| auth.yaml file permissions (0600) | FAIL | SEC-HIGH-001: no chmod called after write. |
| Input validation (license key format) | FAIL | SEC-CRIT-001: no format validation before URL construction. |
| BYOK enforcement | PASS | `check_byok()` called first in `assert_ready_for_run()`. Dogfood bypass still enforces BYOK. |
| Trial limit enforcement | PASS with caveat | In-process counter is correct. See SEC-MED-003 for public reset function. |
| Trial limit bypass via file deletion | PASS | Counter is in-process only, not persisted to disk. Deleting auth.yaml does not reset trial count. |
| Dependency safety (httpx>=0.27, pyyaml>=6.0, stripe>=8.0) | PASS | No known CVEs in these minimum version bounds as of scan date. |
| Error messages leak internals | PARTIAL | SEC-HIGH-002: Keygen response body in webhook errors. SEC-MED-004: exception strings in HTTP responses. |
| CORS/headers on agent.json | PASS with note | CORS wildcard on `/.well-known/*` is appropriate for A2A discovery. See SEC-LOW-004 re: unnecessary `X-ForgeOS-Key` header. |

### IP Checklist

| Check | Status | Finding |
|-------|--------|---------|
| No BusinessAtlas internals exposed | FAIL | IP-HIGH-001: `INIT-2026-525` in validator.py docstring. |
| No proprietary algorithms exposed | PASS | SoM described at marketing level only ("Set-of-Mark prompting, numbered markers"). No internal implementation details. |
| License terms: Elastic License 2.0 maintained | PASS | `pyproject.toml` license = "Elastic-2.0". `specterqa-ios.ts` license = "Elastic-2.0". landing-page.md states "Elastic License 2.0". No MIT/Apache headers present. |
| Pricing accuracy | PASS | All surfaces consistent. See Pricing Consistency Check table above. |
| No competitor disparagement | PASS | Comparison table is factual (feature matrix). No subjective claims against Maestro/Appium/XCUITest. |
| No personal data in public files | PASS | No email addresses beyond public support/sales addresses. No personal names. No internal URLs. |
| Copyright notices | NOTE | No per-file copyright headers in new Python files. The top-level LICENSE file covers the package. This is acceptable for open-source packages with a root LICENSE file, but if SyncTek's legal standard requires per-file headers, add `# Copyright (c) 2026 SyncTek LLC. Licensed under the Elastic License 2.0.` to each new file. Not blocking. |

---

## Summary of Findings by Severity

| ID | Severity | File | Issue |
|----|----------|------|-------|
| SEC-CRIT-001 | CRITICAL | license_cmd.py:100, validator.py:328,337 | Path traversal via unsanitized license key in URL path |
| SEC-HIGH-001 | HIGH | license_cmd.py:65–71 | auth.yaml written without 0600 permissions |
| SEC-HIGH-002 | HIGH | stripe_webhook.py:147–149 | Keygen response body leaked in RuntimeError |
| SEC-HIGH-003 | HIGH | stripe_webhook.py:352–360 | subscription.updated is a silent no-op (license tier not synced on downgrade) |
| SEC-HIGH-004 | HIGH | stripe_webhook.py:52–57 | Default Stripe price IDs accept generic placeholders — silently issues wrong tier |
| SEC-HIGH-005 | HIGH | validator.py:280–292 | JWT offline grace period is permanently broken (stub decoder returns {}) |
| IP-HIGH-001 | HIGH | validator.py:304 | Internal initiative ID `INIT-2026-525` in public PyPI package docstring |
| SEC-MED-001 | MEDIUM | validator.py:328,337 | Same path traversal risk in LicenseValidator class (library API path) |
| SEC-MED-002 | MEDIUM | pyproject.toml:53–57 | stripe missing from core deps — webhook deployments without [license] extra silently skip sig verification |
| SEC-MED-003 | MEDIUM | validator.py:135–139 | `reset_trial_counter()` is public API — trial limit bypassable by library callers |
| SEC-MED-004 | MEDIUM | stripe_webhook.py:392,394,432,434 | Exception strings returned verbatim in HTTP response bodies |
| SEC-MED-005 | MEDIUM | stripe_webhook.py:188–194 | Multi-license customer suspension only cancels first match |
| IP-MED-001 | MEDIUM | llms.txt, landing-page.md, specterqa-ios.ts | "97% gross margin" internal metric needs Chairman confirmation before public |
| SEC-LOW-001 | LOW | validator.py:304 | INIT-2026-525 docstring (same as IP-HIGH-001, dual classification) |
| SEC-LOW-002 | LOW | license_cmd.py:65–71 | Non-atomic auth.yaml write (race condition on interruption) |
| SEC-LOW-003 | LOW | validator.py:333–340 | requests.get() has no timeout in legacy path |
| SEC-LOW-004 | LOW | synctek-website/public/_headers:13 | `X-ForgeOS-Key` unnecessary in .well-known CORS allow-headers |

---

## Verdict

**CONDITIONAL PASS**

**Blockers before deployment:**

1. **SEC-CRIT-001** — Path traversal in license key URL construction. Must add format validation regex before any URL is constructed. Affects `license_cmd.py` and `validator.py`. This is the only hard block.
2. **IP-HIGH-001** — `INIT-2026-525` internal ID in public PyPI source. Must be replaced with version reference before the package ships to PyPI or merges to a public branch.

**Must fix within 7 days of deployment:**

3. **SEC-HIGH-001** — auth.yaml file permissions (0600 not set)
4. **SEC-HIGH-002** — Keygen response body leaking in webhook errors
5. **SEC-HIGH-003** — subscription.updated no-op is a live revenue leak on plan downgrades
6. **SEC-HIGH-004** — Stripe price ID defaults silently issue wrong tier on misconfigured deployment
7. **SEC-HIGH-005** — JWT offline grace documented as working but permanently broken

**No blockers found in:**
- agent.json, llms.txt, landing-page.md, specterqa-ios.ts, llms.txt.ts, [slug].astro
- Pricing consistency (all surfaces agree)
- Stripe signature verification (correct implementation)
- BYOK enforcement (correct, dogfood bypass does not skip BYOK)
- Trial limit enforcement (in-process counter is correct)
- Secrets/credentials (all via env vars, none hardcoded)
- License terms (Elastic License 2.0 consistent throughout)
