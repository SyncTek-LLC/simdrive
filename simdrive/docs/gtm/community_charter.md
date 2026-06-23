# SpecterQA Community Charter

**For:** the SpecterQA Slack or Discord, when we launch one (target: ~D+30 if signal warrants).
**Audience:** iOS engineers, agent builders, MCP-curious developers, and the 1-to-50 humans who will be in the early server.

The community is a side-effect of the product, not the other way around. We launch a server when there are enough people in motion that having a place for them to talk is faster than email. Until then, GitHub Issues + Discussions + the maintainer inbox is the right channel. This document is the playbook for when the bar gets crossed.

---

## What this is

A place for SpecterQA users to learn from each other, share journeys, discuss MCP-driven iOS testing, and contribute to a public corpus of iOS regression journeys.

## What this is not

A support helpdesk. SpecterQA Pro and Team customers get email support against an SLA — that channel is `support@simdrive.dev`. Free Engine users get community support here, on the same terms as everyone else: a maintainer or a fellow user might answer; no one is on the hook.

---

## Channel structure

| Channel | Purpose | Typical post |
|---|---|---|
| `#announcements` | Read-only. Releases, breaking changes, security advisories | "v1.0.0 stable shipped — release notes at [LINK]" |
| `#help` | User-to-user help. Public so the answer is searchable | "type_text returns ok but no characters land — what am I missing?" |
| `#show-and-tell` | What you built. Journey YAMLs, agent loops, CI configs | "Reader page-forward journey — repo link below" |
| `#cloud-beta` | Cloud-tier subscribers and beta-graduates only. Hosted-infrastructure topics | "SSIM-trend dashboard rolled out — feedback?" |
| `#off-topic` | Anything else iOS, testing, or agent-adjacent | "Anyone else watching the iOS 27 betas for HID API drift?" |
| `#contributors` | OSS contributors. Roadmap discussions, PR coordination | "Drafting the network-monitoring tool — design doc at [LINK]" |

We start with these six. New channels open by maintainer decision when traffic in an existing channel forces a split (e.g., `#help-replays` separating from `#help` once replay-specific traffic exceeds a third of the parent channel).

---

## Code of conduct

We follow the [Contributor Covenant 2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). The short version: be the kind of engineer you want to work with. Disagreement is fine; rudeness is not. Reports of harassment go to `maurice.carrier@synctek.io` and are read within 24 hours. Maintainers can suspend accounts; suspensions are documented in `#announcements` only when they affect the wider community (e.g., a contributor losing commit access).

---

## Posting guidelines

1. **Search before you ask.** A 30-second search of the channel often surfaces the answer faster than the round-trip on a new question.
2. **Format code as code.** Triple-backtick blocks. Include the version (`specterqa-ios --version`) and the actual command line.
3. **Lead with the failure mode, not the goal.** "type_text returns ok but no characters appear" is faster to answer than "I am trying to test my login flow." Save the goal for the second message.
4. **One question per thread.** If you have three problems, three threads.
5. **Threads over walls of message.** Keep main-channel chronology readable.

---

## How help requests work

Use this template in `#help`:

```
**Version:** [output of `specterqa-ios --version`]
**Sim / device:** [iPhone 17 Pro / iOS 26.3 / etc]
**What I ran:**
[the command line]
**What I expected:**
[one sentence]
**What happened:**
[the output, in a code block]
**What I have already tried:**
[a list, even if "nothing yet"]
```

**Expected response time:** community-best-effort. Most questions get an answer within 24 hours; some sit longer. If a question is unanswered after 72 hours, the maintainer (Maurice) will read it on the weekly community sweep. **There is no SLA on free-tier support** — this is not a guarantee, it is a community.

If your question is time-critical and you are a Pro / Team / Enterprise customer, use `support@simdrive.dev` instead. That channel is read against an SLA. The community is faster on average; the SLA channel is faster in the worst case.

---

## How power-users get involved

The biggest ongoing contribution is **journeys**. SpecterQA's value compounds when users share canonical iOS regression journeys — the equivalent of a public xUnit test corpus, but for iOS UI flows.

Three contribution paths:

1. **Public journey corpus.** Open a PR against `simdrive/journeys/` with your YAML, a one-paragraph description of the flow, and the iOS version it was recorded against. Approved journeys ship in the next release; the contributor gets a credit in `CONTRIBUTORS.md` and (optionally) a `#contributors` role.
2. **Tool contributions.** The 29-tool surface is open. New tools land via the standard PR process. Read `CONTRIBUTING.md` and open a draft PR before doing significant work — the maintainer will tell you if it conflicts with anything in flight.
3. **Documentation.** The `docs/` tree always has gaps. Doc PRs get the same review attention as code PRs.

Contributors who land three or more merged PRs get nominated for the `contributors` role, which is a soft commit-bit equivalent: read access to private design docs, a vote in roadmap-priority polls, and a recurring slot on the monthly office-hours call (when we start one).

---

## Office hours

Once the community is large enough — likely D+60 or so — we will start a monthly 30-minute office-hours call. Open agenda, public, recorded, posted in `#announcements` afterward. Until then, the maintainer's calendar opens via `cal.com/synctek` for 1:1s with anyone who needs a deep dive.

---

## Signal and noise

We will keep the server small for as long as we can. Growth-for-growth's-sake is not the goal. If the signal-to-noise ratio degrades — too many questions that the FAQ would answer, too many off-topic posts crowding the help channels — we will throttle invites and tighten moderation rather than dilute the experience.

The agentic-first frame applies here too: distribution is not a Discord with 5,000 idle members; it is a Discord with 200 people who actually use the tool and write back when they hit something interesting.

---

**Charter version 1.0** — 2026-04-29. Maintainer: Maurice Carrier, SyncTek LLC.
