# TOMBSTONE — retired memory-spine stages

> **Every retirement is recorded here, not just deleted from `REGISTRY.md`.** The reconciler
> enforces invariant **(d)**: a `replaced_by` MUST name a live `REGISTRY` row that itself passes
> the runtime checks (a)–(c). A retirement that points at a dead/absent/archived replacement is
> reported RED — this is the exact "replaced by X, X never wired" failure (2026-04-14) made
> structurally impossible.
>
> Use `replaced_by = none` only for an intentional, permanent removal with no successor.

## Retirements

| name | retired_date | replaced_by | approved_by |
|------|--------------|-------------|-------------|
| per-session-auto-compile | 2026-04-14 | claude-weekly-compile | Mr.TL |

> **per-session-auto-compile** was the original silent death: `session-start.py` /
> `flush.py` disabled the per-session compile in a code comment that pointed to a "weekly
> rollup" which was never scheduled. This row forces `claude-weekly-compile` to prove it is
> live (trigger + non-archived Ops worker + fresh heartbeat) or the reconciler goes RED.
