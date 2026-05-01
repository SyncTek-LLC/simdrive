# SpecterQA — Pricing

**Production copy** for `synctek.io/products/specterqa/pricing/`.
Version: 1.0 (matches `specterqa-ios 17.0.0a1` and the v1.0 sim-only roadmap).

---

## Hero

**SpecterQA Engine is free, MIT, forever.**

The 29 MCP tools, vision-first observe, real UITouch HID injection, and record/replay are open source and stay open source. Pro, Team, and Enterprise add hosted infrastructure, real-device input, and the things organizations need that individuals do not. Pick the tier that matches the work; switch when the work changes.

**Primary CTA:** [ Start free with the Engine → ](https://pypi.org/project/specterqa-ios/)
**Secondary CTA:** [ Talk to us about Enterprise → ](mailto:maurice.carrier@synctek.io)

---

## Tiers

| | **Engine** | **Pro** | **Team** | **Enterprise** |
|---|---|---|---|---|
| **Price** | Free | $49 / month / seat | $249 / month (5 seats) | Sales-led, $5,000 – $15,000 / year |
| **License** | MIT | Commercial | Commercial | Commercial |
| **MCP tools** | 29 | 29 | 29 | 29 |
| **Local simulator** | Yes | Yes | Yes | Yes |
| **Record / replay** | Yes (local) | Yes + hosted archive | Yes + shared corpus | Yes + on-prem storage |
| **SSIM-trend dashboards** | — | Yes | Yes | Yes |
| **Multi-sim parallelism** | 1 sim | 4 sims | 10 sims | Unlimited |
| **Real-device input (WDA)** | — | — | Yes | Yes |
| **CI integrations** | DIY | — | Yes (`--specterqa` PR-gate) | Yes |
| **Slack / Linear hooks** | — | — | Yes | Yes |
| **Priority support** | Community | Yes (24h SLA) | Yes (24h SLA) | Yes (custom SLA) |
| **SOC 2 / RBAC / SSO** | — | — | — | Yes |
| **Audit logs** | — | — | — | Yes |
| **Best for** | Individual developers, OSS contributors, hobbyist agent builders | Solo iOS engineers gating their own PRs | iOS teams of 2 – 10 with a real CI suite | Regulated industries, large iOS orgs, on-prem requirements |

---

## Engine — Free, MIT, forever

The same code Palace iOS migrated to in 5 days. 29 MCP tools, real UITouch on iOS 26, vision-first observe with stable_id replay, the universal2 native HID helper in the wheel. One install line:

```
pip install specterqa-ios
```

Use it for your own apps, your team's apps, your client's apps, your agent's iOS testing loop. Commercial use is permitted under MIT — you do not owe SyncTek anything for using the Engine in a paid project.

**Best for:** individual developers driving an iOS sim from Claude / Cursor / Cline. Agent builders shipping iOS automation as a feature. Open-source contributors. The Engine is the product surface; everything else on this page is infrastructure on top.

---

## Pro — $49 / month / seat

Everything in the Engine, plus the things you stop wanting to build yourself the second you have a real test suite:

- **Hosted replay archive.** Recordings persist across machines. Your laptop dies, your replays do not.
- **SSIM-trend dashboards.** Per-journey similarity over time, with regression alerts when the curve breaks. Catches the slow visual drift you would miss in any single run.
- **4-sim parallelism.** Run 4 simulators in parallel under one Pro seat. The license check lifts the multi-sim guard the Engine ships with.
- **Priority support.** 24-hour SLA on email. The maintainer (Maurice) reads every Pro ticket directly.
- **Signed wheels.** Notarized native HID helper, signed Python wheel — required by some corporate environments.

**Best for:** the iOS engineer who is the entire iOS QA function. One seat, one inbox, replay-gated PRs, dashboards that catch what the eye misses.

[ Start a Pro trial → ](#)

---

## Team — $249 / month, 5 seats included

Everything in Pro, for everyone on the team:

- **Shared journey corpus.** A `.specterqa/` directory hosted by us — your whole team writes to the same canonical journeys. New hire gets the full regression suite on their first `specterqa-ios pull`.
- **CI integrations.** The `--specterqa` PR-gate flag in our reference scripts. One YAML block in your CI to fail PRs on SSIM drift.
- **Real-device input via WebDriverAgent.** This is the v0.3 roadmap item — it ships in the Team tier, not the Engine. Your CI farm runs against actual iPhones with real UITouch.
- **Slack and Linear hooks.** Replay failures land in the right channel with the right context. No webhook code to write.
- **24-hour SLA on email and Slack Connect.**

Additional seats: **$39 / month / seat** beyond the 5 included.

**Best for:** iOS teams of 2 to 10 with a real CI pipeline. The shared corpus is the unlock — once a team is writing to the same journey set, the per-engineer flake-debugging budget collapses.

[ Start a Team trial → ](#)

---

## Enterprise — Sales-led, $5,000 – $15,000 / year

Everything in Team, plus the things your security review will ask for:

- **SOC 2 Type II.** In progress; expected complete Q4 2026.
- **RBAC.** Per-project read/write/admin roles, mapped to SSO groups.
- **SSO.** SAML 2.0 / OIDC. Okta, Azure AD, Google Workspace.
- **Audit logs.** Every replay, every recording, every Pro/Team action — exportable to your SIEM.
- **On-prem replay storage.** Recordings stay inside your VPC. We never see them.
- **Custom SLA.** 4-hour, 1-hour, or 24/7 — priced accordingly.

**Best for:** regulated industries (finance, healthcare, public sector), iOS organizations of 25+ engineers, and any company with a Vendor Risk Management questionnaire that requires the items above.

[ Talk to us about Enterprise → ](mailto:maurice.carrier@synctek.io)

---

## Compare to alternatives

We respect every tool on this list. The cell entries are factual as of 2026-04-29, not editorial.

| | **SpecterQA Pro** | **Maestro Cloud** | **BrowserStack App Live** | **Sauce Labs Real Device** |
|---|---|---|---|---|
| **Entry price** | $49 / month / seat | $99 / month | $199 / month | $1,000+ / month team minimum |
| **MCP-native** | Yes | No | No | No |
| **Local simulator (no cloud round-trip)** | Yes | Yes | No | No |
| **Real UITouch on iOS 26** | Yes | Partial | N/A (real device only) | N/A (real device only) |
| **Vision-first observe** | Yes | No | No | No |
| **Real-device input** | Team tier ($249 / month) | Yes | Yes | Yes |
| **Cross-platform (Android)** | No (iOS-deep by design) | Yes | Yes | Yes |
| **Open core** | Yes (Engine is MIT) | Partial | No | No |

If your work is iOS-deep, MCP-native, and agent-driven — this list resolves to one tool. If your work is cross-platform and human-driven, Maestro is the right answer; we will say so.

---

## FAQ

**Can I use the Engine commercially?**
Yes. The Engine is MIT-licensed. You can ship products, services, agents, and SaaS that use SpecterQA Engine without paying SyncTek anything. The paid tiers are about hosted infrastructure and real-device input — not about lifting a license restriction on the Engine itself.

**When does real-device input ship in Team?**
Real-device input via WebDriverAgent is on the v1.1 roadmap (3 to 4 weeks after v1.0 stable). v1.0 ships sim-only. Team-tier subscribers get real-device input automatically when v1.1 lands; no upcharge.

**What if my team grows past 5 seats?**
Additional Team seats are $39 / month / seat. There is no hard cap; we have priced it linearly so you do not have to renegotiate at 6 or 11 or 26 seats. If you cross 25 seats, talk to us about Enterprise — the per-seat math gets favorable, and you probably want SSO and audit logs by then anyway.

**Is the Engine going to stop being free?**
No. The Engine staying free, MIT, and feature-complete on the simulator path is the model. Pro and Team add hosted infrastructure that we run; Enterprise adds compliance work that we do. None of those are reasons to take features out of the Engine.

**Do I need an Anthropic API key to use SpecterQA?**
You need one to drive an iOS sim from Claude — but that is your relationship with Anthropic, not with us. SpecterQA does not call Claude on your behalf and does not bill for Claude tokens. Bring your own key (or your own agent — SpecterQA is MCP-native and works with any MCP-compatible client).

---

## Primary CTA

[ **Start free with the Engine →** ](https://pypi.org/project/specterqa-ios/)

`pip install specterqa-ios` — 29 tools, MIT, no signup.

## Secondary CTA

[ Talk to us about Enterprise → ](mailto:maurice.carrier@synctek.io)
