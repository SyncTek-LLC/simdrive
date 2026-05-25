# SimDrive Cloud API — Railway Deployment

Private API for SimDrive replay archive + license management.
Hosted at `cloud.simdrive.dev` (confirm with maintainer before first production deploy).

## Prerequisites

- Railway account + `railway` CLI installed (`npm i -g @railway/cli`)
- Cloudflare R2 bucket created: `simdrive-cloud-prod`
- Ed25519 keypair generated (one-time; see §3)

---

## 1. One-time keypair generation

The same keypair is used by the client for offline verification and by the server to sign licenses.
Run this **once** on a secure machine and save the output immediately.

```bash
python3 - <<'EOF'
from simdrive.license.keypair import generate_keypair
sk, vk = generate_keypair()
print(f"SIMDRIVE_LICENSE_PRIVATE_KEY={sk.encode().hex()}")
print(f"SIMDRIVE_PUBLIC_KEY_HEX={vk.encode().hex()}")
EOF
```

- `SIMDRIVE_LICENSE_PRIVATE_KEY` → Railway env var (secret; never share)
- `SIMDRIVE_PUBLIC_KEY_HEX` → `simdrive/src/simdrive/license/public_key.py` constant (baked into client)

---

## 2. Cloudflare R2 setup

1. Log in to Cloudflare dashboard → R2 → Create Bucket → name: `simdrive-cloud-prod`
2. R2 → Manage R2 API Tokens → Create API Token
   - Permissions: Object Read & Write
   - Scope: Specific bucket → `simdrive-cloud-prod`
3. Save `Access Key ID` and `Secret Access Key` immediately (shown once)

---

## 3. Railway project setup

```bash
# Link Railway project
railway login
railway link   # select or create the simdrive-cloud project

# Set all env vars (replace values with real secrets)
railway variables set SIMDRIVE_PUBLIC_KEY_HEX=<hex>
railway variables set SIMDRIVE_LICENSE_PRIVATE_KEY=<hex>
railway variables set R2_ACCOUNT_ID=<cloudflare-account-id>
railway variables set R2_ACCESS_KEY_ID=<r2-key-id>
railway variables set R2_SECRET_ACCESS_KEY=<r2-secret>
railway variables set R2_BUCKET=simdrive-cloud-prod
railway variables set SIMDRIVE_DATABASE_URL=<railway-postgres-url>
```

---

## 4. Database

Railway Postgres plugin recommended for production.

1. Railway dashboard → Add Plugin → PostgreSQL
2. Copy the `DATABASE_URL` it provides
3. Set: `railway variables set SIMDRIVE_DATABASE_URL=<postgres-url>`

Tables are created automatically on first startup via `init_db()`.

---

## 5. Deploy

```bash
# From the repo root (simdrive/ subdir of specterqa-ios)
cd simdrive/
railway up
```

Railway detects Python via `pyproject.toml`, runs `pip install -e '.[cloud]'`,
then starts: `uvicorn simdrive.cloud.app:app --host 0.0.0.0 --port $PORT`

---

## 6. Healthcheck

Railway probes `GET /health` after deploy. Expected response:

```json
{
  "status": "ok",
  "version": "1.0.0a1",
  "db_reachable": true,
  "storage_backend": "r2client"
}
```

Deploy fails if healthcheck returns non-200 within 30 seconds.

---

## 7. Custom domain

1. Railway dashboard → Settings → Domains → Add Custom Domain
2. Add: `cloud.simdrive.dev`
3. Follow CNAME DNS instructions (pointed at Railway's ingress)
4. Railway handles TLS provisioning automatically

---

## 8. Env-var checklist (pre-launch)

| Variable | Required | Notes |
|---|---|---|
| `SIMDRIVE_PUBLIC_KEY_HEX` | Yes | Must match `public_key.py` in client |
| `SIMDRIVE_LICENSE_PRIVATE_KEY` | Yes | Never share; only Railway sees it |
| `R2_ACCOUNT_ID` | Yes | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | Yes | R2 API token key ID |
| `R2_SECRET_ACCESS_KEY` | Yes | R2 API token secret |
| `R2_BUCKET` | Yes | `simdrive-cloud-prod` |
| `SIMDRIVE_DATABASE_URL` | Yes (prod) | Railway Postgres connection string |
| `PORT` | Injected | Railway sets this automatically; do not override |

---

## 9. Privacy policy gate

**The privacy policy must be live at `simdrive.dev/privacy` before the first
design-partner upload.** This is a hard gate per engineering spec §3.
Do not promote the Cloud API to design partners until this is confirmed.

---

## Punted to launch prep

- Stripe webhook signature verification (currently naive — no `stripe-signature` header check)
- R2 lifecycle rules for tier-based retention (90d Solo, 1y Pro/Team) — set in Cloudflare dashboard
- Over-quota email alerts at 80% usage — deferred to 1.1
- Account purge self-serve UI — manual via support email in 1.0
