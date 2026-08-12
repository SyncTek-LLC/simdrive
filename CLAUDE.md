# SimDrive — agent notes

SimDrive is an MCP-native iOS bug-repro toolkit for AI agents: reproduce and
validate iOS bugs in ~60 seconds, with deterministic record & replay.

> **Part of Heka.** SimDrive is a bundled **capability** of Heka — a self-hosted,
> local-first governance harness for AI-native software development (layers:
> harness core · ForgeOS governance · SimDrive capability). For the full product
> map, positioning, rules of engagement, and current state, see **`HEKA.md`** in
> the [harness repo](https://github.com/SyncTek-LLC/harness) (and its
> `heka-workspace.yml` repo map).

## Orientation
- Package source: `src/specterqa/ios` (mid-rename to `simdrive`).
- Tests: `simdrive/tests/` and `tests/` (pytest; run with a Python 3.11+ that has the deps).
- Licensing validates fully **offline** (Ed25519); nothing phones home by default.
- Conventional commits (`feat:/fix:/docs:/…`). `main` is **PR-only** (a server-side governance hook blocks direct pushes).
