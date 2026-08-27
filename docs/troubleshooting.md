# SpecterQA iOS — Troubleshooting Guide (superseded)

> **STATUS: SUPERSEDED.** This guide describes the retired `ios_*`-prefixed
> AX-backend tool surface (`ios_elements`, `ios_tap`, `ios_start_session`,
> `ios_dismiss_springboard_alert`, `ios_app_state`, `backend="ax"`) and a
> `v13.2.0+` version line that predates the `simdrive` rebrand. None of those
> tool names exist in the shipped `simdrive` MCP surface (`session_start`,
> `observe`, `tap`, `dismiss_first_launch_alerts`, `pre_grant_permissions`,
> `app_state`, ...); the underlying fixes it describes may or may not still
> apply to the current implementation and have not been re-verified against
> it. Kept only as historical scaffolding; do not cite or follow the specific
> tool calls below. For current troubleshooting, see `README.md` and
> `simdrive/docs/RECOVERY.md`.
