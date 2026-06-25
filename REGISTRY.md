# REGISTRY — live memory-spine stages

> **Source of truth for what is *supposed* to be running.** Every row here is reconciled
> against runtime truth by `scripts/reconcile.py` (Homebase = authoritative). A row is GREEN
> only when ALL of these hold:
> - **(a) trigger exists & enabled** — Windows Task Scheduler entry / cron / systemd timer /
>   hook / Homebase post-receive. Remote triggers (a laptop `schtask:`/`hook:` seen from
>   Homebase) are verified *transitively* by a fresh heartbeat (c).
> - **(b) live Ops worker** — a `slug:` match in `ops/workers/*.yaml` that is **NOT** in
>   `ops/workers/archived/` (the live validator only globs top-level — an archived worker is
>   invisible = silent death).
> - **(c) heartbeat fresh + good exit_code** — beacon JSON within `cadence + grace`, `exit_code`
>   in {0, 2} (2 = warn). On Homebase, freshness uses the pushed beacon's **mtime** (receipt
>   time) to avoid laptop clock skew.
> - **(d)** every `TOMBSTONE.replaced_by` names a live REGISTRY row that itself passes (a)–(c).
>
> Adding/removing a stage = edit this file + `TOMBSTONE.md` in the same commit. The laptop
> pre-commit guard (`hooks/pre-commit-registry-guard.py`) is a fast **advisory** check only —
> bypassable ≥6 ways. The wall is the Homebase reconciler.

## `trigger_ref` grammar (parsed by reconcile.py)

| prefix | meaning | checked on Homebase | checked on laptop |
|--------|---------|---------------------|-------------------|
| `schtask:<TaskName>` | Windows Task Scheduler | remote → transitive via (c) | `schtasks` query (exists + not Disabled) |
| `cron:<marker>` | Homebase crontab line tagged `# ops-worker:<marker>` | `crontab -l` grep | remote → skip |
| `systemd:<unit>` | Homebase systemd timer | `systemctl is-enabled` | remote → skip |
| `hook:<name>` | Claude Code session hook (laptop) | remote → transitive via (c) | `hooks/<name>.py` exists |
| `none` | advisory stage, no enforced trigger | skip | skip |

## Live stages

| name | type | cadence | trigger_ref | ops_slug | heartbeat_path |
|------|------|---------|-------------|----------|----------------|
| memory-compiler-flush | event | per-session | hook:session-end | memory-compiler-flush | scripts/last-flush.beacon.json |
| claude-weekly-compile | scheduled | weekly Sun 8:30 | schtask:ClaudeWeeklyCompile | claude-weekly-compile | scripts/weekly-compile.beacon.json |
| claude-memory-dream | scheduled | weekly Sun 8:00 | schtask:ClaudeMemoryWeeklyLint | claude-memory-dream | scripts/memory-dream.beacon.json |
| claude-memory-reconciler | scheduled | daily | cron:claude-memory-reconciler | claude-memory-reconciler | /root/hestia/beacons/claude-memory-reconciler.json |
| retrieval-pull | event | per-session | hook:session-start | none | none |

> **Notes**
> - `heartbeat_path` is the **producer-local** path (laptop `scripts/*.beacon.json`). On Homebase
>   the reconciler resolves the pushed copy as `/root/hestia/beacons/<ops_slug>.json` (the
>   fleet-wide beacon-push naming convention), whose mtime is the authoritative receipt time.
> - `claude-memory-reconciler` is the reconciler itself; its row makes it self-proving. It is
>   wired as an **Ops critical-slug** (Phase 2) so it cannot be disabled/archived while the fleet
>   shows green (AC1b).
> - `retrieval-pull` is advisory (event/`none`): it writes `scripts/retrieval-pull.beacon.json`
>   at session start but has no Ops worker and is not gated.
