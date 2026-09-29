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
| per-session-auto-compile | 2026-04-14 | memory-compiler-flush | Mr.TL |
| claude-weekly-compile | 2026-09-28 | memory-compiler-flush | Mr.TL |
| claude-memory-dream | 2026-09-28 | memory-compiler-flush | Mr.TL |

> **per-session-auto-compile** was the original silent death: `session-start.py` /
> `flush.py` disabled the per-session compile in a code comment that pointed to a "weekly
> rollup" which was never scheduled. This row forces `claude-weekly-compile` to prove it is
> live (trigger + non-archived Ops worker + fresh heartbeat) or the reconciler goes RED.

> **claude-weekly-compile / claude-memory-dream** retired 2026-09-28 (plans/memory-compiler-fix.md in the
> workspace): the weekly LLM concept compile ran 11 weeks behind under the quota cap. Session notes are still
> written by the flush and `mem.py search` covers the daily notes directly, so the flush is the live successor.
> Scheduled tasks disabled, Ops workers archived, session-start overdue hook removed. Mr.TL: "lets fix the memory
> compiler right now".
