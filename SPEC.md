# Memory Spine — Spec

> **Status:** Active (revised per Codex r1, converged) · **Type:** Spec (solo / internal tooling)
> **Created:** 2026-06-25 · **Updated:** 2026-06-25 · **Owner:** Mr.TL
> **Spec vs PRD:** solo/internal non-trivial → one Spec (this doc). Synthesizes a 4-lens `/diverge` (2026-06-25) + Codex dual-brain cold-read (`C:\Dev\codex\dual_brain\requests\2026-06-25_1040_memory-spine-spec-coldread-r1\`). Ready to build Phases 0–4.

---

## 1. Problem  *(core)*

The Claude-Code memory system is half-dead and nobody noticed for ~10 weeks. Capture works (session flushes are current), but **synthesis stopped on 2026-04-14**: per-session auto-compile was disabled "replaced by a weekly rollup" that was **never wired as a scheduled task**. The knowledge wiki's `connections/` and index froze on that date; `mem.py` (the working Karpathy BM25 pull-retriever) is never called by anything; the old embedding-PUSH recall hook scored 0/230 and is dead. This is the third silent-death of a memory automation (lint and two Ops workers died the same way in the 05-15 laptop migration). The root failure is structural: **a pipeline stage that stops running emits no signal, so its death is invisible.** The system also only accumulates — it never consolidates — so it bloats (MEMORY.md is over its load budget) instead of compounding.

## 2. Goals  *(core)*
1. **Silent death is mechanically impossible** — every stage proves it ran; the *absence* of that proof raises an alarm, then a wall.
2. **The exact "replaced by X, X never wired" failure is structurally blocked** at commit time.
3. **Synthesis runs again and stays running**, on always-on infrastructure (survives the laptop being off).
4. **Recall actually happens at decision time** — the pull-retriever is wired into a real session moment, not just available.
5. **The corpus compounds, not bloats** — a scheduled consolidation pass decays/archives low-value entries and (optionally) abstracts patterns.
6. **Everything is wired to Ops and enforced by walls, not nudges.** $0-token for the enforcement spine; API spend only for optional weekly abstraction.
7. **"Done" = chaos-tested** — each enforcement stage is verified by deliberately breaking it and watching the alarm/wall fire, in the same session it's built.

## 3. Non-Goals / Out of Scope  *(core)*
- **No embeddings / vector DB.** Dense retrieval fails structurally on this small private-jargon corpus (the 0/230). Keyword/BM25 (Karpathy pull) only.
- **No replacement for git as the causal record.** Git history already holds "why" with perfect recall; the synthesis layer must not try to re-derive it. Capture stays cheap.
- **No multi-layer monitoring tower.** One external monitor-of-the-monitor + in-house Ops beacons. Resist the second-system urge to watch the watcher of the watcher.
- **No new always-on local daemon.** Laptop is often off; it only captures + pushes. Nothing critical depends on the laptop being awake.
- **Not solving generic team/product memory** — this is Mr.TL's single-host personal harness only.

## 4. Proposed Solution / How  *(core)*

A thin **capture → synthesize → retrieve** pipeline where every stage is **self-proving** and one **trial-balance invariant** makes silent retirement impossible.

### 4.1 Topology (who runs what)
- **Laptop (LOLA-001, intermittent):** capture only. Session hooks (sync-up / handoff / lights-out) flush session context to markdown and `git push`. Git is the transport — already wired.
- **Homebase (VPS, always-on):** owns synthesis, index rebuild, consolidation, and the canonical watchdog. A `post-receive` (or scheduled pull) picks up pushed flushes and runs the compile + BM25 index rebuild. Survives the laptop being off.

### 4.2 Enforcement (walls, layered cheapest-first)
1. **Heartbeat tokens** — every stage writes a dated "I ran" token on success (file mtime / beacon JSON). The watchdog checks *token age*, not job result. Absence of a fresh token within `cadence + grace` = suspected dead (liveness theory: you can only witness a silent death as the *absence* of an expected event in a time window).
2. **In-house Ops beacons** — each stage's beacon is a normal Ops worker (the existing `beacon_healthy` pattern that already watches 38 workers). This is the per-stage watcher, in-house.
3. **One external dead-man's switch** — guards **Ops itself** (the watcher-of-the-watcher your own code cannot fake-pass). If the whole VPS / Ops monitor dies, the external service pages. (Open Q on hosted vs self-host vs pure-beacon — see §6.)
4. **Runtime Registry Reconciliation (the keystone wall)** — `REGISTRY.md` declares every live stage (name | type [scheduled|event] | cadence | trigger ref | Ops worker slug | heartbeat path); `TOMBSTONE.md` records retirements (name | retired-date | replaced-by | approved-by). **The wall is NOT text-vs-text** (that was theater — it would not have caught the original death, which was a disabled call in a Python comment that touched no TOMBSTONE). The load-bearing invariant is **reconciliation against runtime truth** — every live `REGISTRY` row must prove all four:
   - (a) its **trigger actually exists and is enabled** — Windows Task Scheduler entry / cron / systemd timer / hook config / Homebase post-receive (not just "declared");
   - (b) a **live Ops worker exists** for it in `ops/workers/*.yaml` — **not** in `archived/` (the live validator only globs top-level; an archived worker is invisible and = silent death);
   - (c) a **heartbeat exists, is fresh, and has acceptable `exit_code`** (or an explicit first-run state);
   - (d) every `TOMBSTONE.replaced-by` names a live REGISTRY row that itself passes (a)–(c).
   **The reconciler itself is an Ops critical-slug** so it cannot be disabled/archived while the fleet shows green.
5. **Enforcement boundary — authoritative on Homebase, not the laptop.** A laptop pre-commit hook is a *fast advisory/local guard* for obvious registry/tombstone mistakes — useful, but bypassable (≥6 ways) so NOT the wall. The **authoritative reconciliation runs on Homebase** (receive-side or scheduled), comparing REGISTRY ↔ live Ops manifests ↔ scheduler entries ↔ heartbeat files. The laptop sync-up blocker only **reads** Homebase's red/green verdict — it never computes its own.
6. **Two liveness contracts (don't conflate laptop-off with stage-dead).** *Scheduled* stages: "heartbeat must appear every cadence + grace." *Event-driven* stages: "if input event X was received, output/heartbeat Y must appear within N." A legitimately-off laptop is expected staleness on capture; a missing Homebase output *after input arrived* is a real death. Prefer Homebase filesystem mtime / receipt-time for remote beacons over laptop wall-clock (avoids clock-skew false signals).
7. **Escalating gate = break-glass, not lockout.** Miss 1 → Telegram. Authoritative-red → sync-up **blocks** — but offers a *break-glass* bypass that **requires a reason string, writes an audit record (timestamp, machine, failing checks, reason), carries a short TTL (one session / 24h), and keeps showing red until actually fixed.** Mr.TL can work in an emergency; the bypass becomes evidence and cannot silently become the new normal. Not a one-keystroke skip; not an absolute lockout.

### 4.3 Retrieval (Karpathy pull, finally wired)
- Tiny always-loaded index (one-line summaries) is **pushed** at session start (already happens via MEMORY.md / SessionStart).
- Bulk wiki is **pulled** on demand via `mem.py search` (BM25, stdlib, zero-token). Wire `mem.py` into the sync-up flow so it's actually consulted, not just available.
- The `mem.py index` page-count doubles as a heartbeat: if it stops growing week-over-week, synthesis is silently dead — detectable with no new machinery.

### 4.4 Growth (consolidation, not just accumulation)
- A scheduled stdlib pass: **decay** every entry's freshness score, **boost** entries that were retrieved/accessed, **archive** (never delete — tombstone) entries below threshold. One pass does both decay and boost (the two-factor consolidation pattern) so the store stabilizes instead of bloating or eroding.
- **Optional, sandboxed, API-using** abstraction step: "what do these N survivors collectively reveal that no single entry states?" → a new higher-order note. This is the *only* place tokens are spent. It runs **isolated from the read path** (weak-model maintenance can emit malformed output and corrupt memory; retrieval must only ever read already-validated entries).

## 5. Alternatives Considered  *(core)*
- **Embeddings / vector recall (PUSH):** rejected — scored 0/230; private jargon is out-of-distribution for general embedding models. Structural, not tunable.
- **Do nothing — CLAUDE.md + `git log --grep` + grep (the Contrarian's "do less"):** has zero rot surface and is the honest floor. **Partially adopted:** we keep git as the causal record and don't build a competing synthesis store. Rejected as the *whole* answer because it doesn't solve recall-at-decision-time or compounding, and doesn't make silent death visible.
- **Full multi-layer enforcement tower:** rejected — every wall is a new rot surface (the Contrarian's strongest point; all 3 prior deaths were "more enforcement"). Mitigated by minimizing stages and making the keystone a *commit-time invariant* (can't rot — it runs on every commit) rather than a daemon.
- **Hard spec/commit gate everywhere:** rejected — Claude can bypass pre-commit hooks ≥6 ways (`--no-verify`, stash, MCP switch; GH issue #40117). See Open Q on server-side enforcement.

## 6. Open Questions  *(core — flag before building)*
- **OQ1 — External switch shape (Owner: Mr.TL):** hosted healthchecks.io free tier vs self-hosted vs *pure in-house Ops beacon* with the external layer guarding only Ops. Recommendation: in-house beacons for stages + one external ping guarding Ops. **Decide before Phase 2.**
- **OQ2 — RESOLVED (Codex r1):** Yes, server-side is mandatory. Laptop pre-commit = fast guard only; **Homebase owns the authoritative reconciliation**, sync-up reads its verdict. Folded into §4.2 points 4–5.
- **OQ3 — Pull wiring point:** wire `mem.py search` into sync-up as auto-run, or as an instruction the agent must follow? (Auto-run = wall; instruction = nudge.) Leaning auto-run a default query + surface results.
- **OQ4 — Consolidation cadence + thresholds:** decay rate, archive threshold, abstraction frequency (weekly?). Tune after observing one real pass.

## 7. Success Criteria / Acceptance  *(core — what enforcement gates on)*
- **AC1 (reconciliation, not text):** A live `REGISTRY` row whose **trigger is missing/disabled, OR whose Ops worker is archived/absent, OR whose heartbeat is stale/bad** is reported **RED by the Homebase reconciler** (proof: archive a worker or delete its task → reconciler goes red). And: a `TOMBSTONE.replaced-by` that doesn't name a live, passing REGISTRY row is rejected (proof: attempt it).
- **AC1b:** The reconciler is itself an Ops critical-slug — disabling/archiving it makes the Watchdog go red (proof: disable it, see Watchdog fire).
- **AC2:** Yanking/staling a stage's heartbeat raises a Telegram alert within one watchdog cycle (proof: chaos drill — yank a token, see the alert).
- **AC3 (break-glass, not lockout):** Authoritative-red blocks the next session-start; the break-glass bypass requires a reason string, writes an audit record, has a TTL, and the state stays red until fixed (proof: force red → block; bypass with reason → audit row written → still red).
- **AC4:** The weekly compile/index runs on Homebase on schedule and the wiki gains fresh articles + the BM25 index page-count increases (proof: run it, diff the wiki).
- **AC5:** `mem.py search` is invoked during sync-up and surfaces relevant pages (proof: sync-up output shows pulled results).
- **AC6:** Consolidation pass runs, archives ≥1 stale entry, and does NOT mutate the read-path index on failure (proof: feed malformed input, confirm graceful fail).
- **AC7:** Every stage is in `REGISTRY.md` with a live heartbeat (proof: trial-balance check passes green).

## 8. Dependencies  *(optional)*
- **Existing code:** `flush.py` (capture ✅), `compile.py` (synthesis — to re-wire), `dream.py` (groom ✅), `mem.py` (BM25 pull ✅), `lint.py`, Ops worker framework (`ops/workers/*.yaml`), session hooks (`~/.claude` sync-up/handoff/lights-out).
- **External:** healthchecks.io (or equivalent) — pending OQ1. Free tier. **Secret:** the ping UUID is a secret → Obsidian `Keys`, never in repo.
- **Infra:** Homebase VPS (cron/systemd + git bare repo), Telegram (Hermes, already wired for Ops alerts).
- **Runtime:** Python stdlib only for the spine. Optional: one Anthropic API call (subscription-covered) for §4.4 abstraction.

## 9. Security / Data  *(optional)*
- All data is Mr.TL's own memory/notes — no PII/third-party data. Egress is limited to: git push (already trusted) + one heartbeat ping (UUID only, no content) to the external switch. Attack surface is minimal; the UUID is the only secret and lives in Obsidian.

## 10. Testing & Rollout  *(optional — load-bearing here)*
- **"Done" is defined as chaos-tested, not built** (the deadline is the test, not the original sin). For each enforcement stage, the acceptance proof IS a deliberate-break drill (AC1–AC3, AC6).
- **Monthly chaos drill** (becomes an Ops worker): yank a heartbeat on purpose, confirm the alarm fires — proves the watchdog isn't itself dead.
- **Rollout order = the PIV chunks below.** Phase 0 (unfreeze) ships first as a pure-win, zero-risk warmup.

## 11. Metrics / Monitoring  *(optional)*
- Every scheduled stage registers with Ops (`/register-ops-worker`) — non-negotiable per harness rule.
- Watch: heartbeat freshness per stage, BM25 index page-count trend (synthesis liveness), MEMORY.md size vs budget, consolidation archive count, abstraction-pass cost.

---
<!-- ═══════════ LIVE SECTIONS ═══════════ -->

## Status Board

**To Do** *(PIV chunks — each ≈ one block, one commit, each self-proving)*
- [ ] **Phase 1 — Runtime Registry Reconciliation** (was "Trial Balance"): `REGISTRY.md` + `TOMBSTONE.md` + **Homebase authoritative reconciler** (registry ↔ scheduler/hook triggers ↔ live Ops workers ↔ heartbeats) + laptop pre-commit fast-guard + reconciler registered as Ops critical-slug. *(AC1, AC1b, AC7)*
- [ ] **Phase 2 — Heartbeats + dead-man's switch**: per-stage tokens (two liveness contracts: scheduled vs event-driven) + in-house Ops beacons + one external switch guarding Ops. *(AC2)*
- [ ] **Phase 3 — Break-glass gate**: sync-up reads Homebase red/green; blocks on red with reason-string + audited + TTL'd bypass. *(AC3)*
- [ ] **Phase 4 — Wire mem.py pull into sync-up**: auto-consulted at session start. *(AC5)*
- [ ] **Phase 5 — DEFERRED (Codex r1)**: consolidation (decay+boost+archive) + optional sandboxed LLM abstraction. **Parked until the reliability loop (0–4) survives ≥1 real missed/late cadence + ≥1 chaos drill.** Kept (not cut) — it's the stated "must compound over time" goal, just sequenced after the spine is proven. *(AC6)*
- [ ] **Chaos drill** registered as monthly Ops worker. *(AC2 ongoing)*

**Doing** *(claimed — /team build, 3 waves, orchestrated by Claude side-terminal 2026-06-25 12:40)*
- 🔄 **Phase 0 — Unfreeze** — DOING — worker `spine-phase0` (Wave 1). Compile task already registered (`ClaudeWeeklyCompile` Ready); running catch-up compile + verifying wiki/index growth. *(AC4)*
- 🔄 **Phase 1 — Runtime Registry Reconciliation** — DOING — worker `spine-phase1-keystone` (Wave 1). REGISTRY/TOMBSTONE + Homebase-authoritative reconciler + pre-commit advisory + Ops critical-slug. *(AC1/AC1b/AC7)*
- 🔄 **Phase 4 — Wire mem.py pull** — DOING — worker `spine-phase4-pull` (Wave 1). Auto-run BM25 pull in session-start hook (OQ3=auto-run wall). *(AC5)*
- ⏳ **Phase 2 — Heartbeats + dead-man** — Wave 2 (`spine-phase2-ops`), after Phase 1 contract lands. *(AC2)*
- ⏳ **Phase 3 — Break-glass gate** — Wave 2 (`spine-phase3-gate`), reads Phase 1 verdict. *(AC3)*
- ⏳ **Auditor / chaos drill** — Wave 3 (`spine-auditor`): chaos-drill AC1–AC7, confirm Codex's original-death pattern now surfaces RED. *(AC2 ongoing)*

**Done**
- [x] 4-lens `/diverge` synthesized (2026-06-25)
- [x] Spec scaffolded
- [x] Codex dual-brain cold-read (r1) — keystone flaw caught + spec revised, converged (`final.md`)

## Decisions (ADR log)

| Date | Decision | Why | Alternatives rejected |
|------|----------|-----|----------------------|
| 2026-06-25 | Karpathy BM25 pull retrieval; no embeddings | Dense retrieval is structurally OOD on small private-jargon corpus (0/230) | Vector/embedding PUSH recall |
| 2026-06-25 | Detect death by *absence of heartbeat*, not by checking job result | Silent death is a liveness violation — only witnessable as a missing expected event in a time window | Invariant/output checks alone (can't see a job that never ran) |
| 2026-06-25 | REGISTRY/TOMBSTONE trial-balance invariant, enforced at commit time | A commit-time invariant can't itself rot (runs every commit) and blocks the exact "replaced by X never wired" failure atomically | Daemon-based registry check (new rot surface) |
| 2026-06-25 | Escalating gate: miss1 Telegram → miss2 block session-start | One-person ops ignores alerts; a blocked session cannot be ignored — beats alert-fatigue | Alert-only (tuned out); hard-block-on-miss1 (too brittle) |
| 2026-06-25 | Homebase owns synthesis; laptop captures only; git is transport | Laptop is often off — nothing critical can depend on it being awake (two-node liveness asymmetry) | Local synthesis daemon |
| 2026-06-25 | "Done" = chaos-tested in-session, not "built" | All 3 prior deaths were declared done without observing failure; reliability must be witnessed | Build-and-declare (the original sin) |
| 2026-06-25 | Spine is $0 stdlib; API only for optional sandboxed abstraction | Cost discipline; weak-model maintenance can corrupt the store, so isolate it from the read path | API in the hot path |
| 2026-06-25 | **Keystone = runtime reconciliation, NOT text trial-balance** (Codex r1) | Original death was a disabled call in a Python comment touching no TOMBSTONE; text-vs-text would sit green while the system died identically. Registry must reconcile vs real triggers + live (non-archived) Ops workers + fresh heartbeats | REGISTRY↔TOMBSTONE text invariant as the wall (demoted to fast local guard) |
| 2026-06-25 | Homebase is authoritative for red/green; laptop pre-commit is advisory only (Codex r1) | Laptop hook is bypassable ≥6 ways and can't see Homebase runtime; the wall must live outside the agent's local write path | Laptop-side enforcement as the boundary |
| 2026-06-25 | Two liveness contracts (scheduled vs event-driven); prefer Homebase mtime over laptop clock (Codex r1) | Conflating laptop-off with stage-dead either cries wolf or hides real death; laptop clock skew corrupts age math | Single generic missed-heartbeat check |
| 2026-06-25 | Miss-gate = break-glass (reason+audit+TTL), not absolute lockout (Codex r1) | Absolute lockout is a footgun that locks Mr.TL out at the worst moment; bypass-as-evidence preserves the wall without trapping him | Hard lockout until manually cleared |
| 2026-06-25 | Reconciler itself is an Ops critical-slug (Codex r1) | Otherwise the watcher is just another worker that can be archived/disabled while the fleet stays green — the regress isn't closed | Reconciler as an ordinary worker |
| 2026-06-25 | Build Phases 0–4 now; defer Phase 5 until reliability loop proven (Codex r1) | Consolidation/abstraction add mutation surface + aren't needed to stop silent death; prove the spine first | Building all 6 phases in one pass |

## Notes / Scratch
- Full `/diverge` (4 lenses: academic, industry, contrarian, wildcard) lives in this session's transcript. Headline convergence: *absence-of-signal as the signal* + *external monitor-of-the-monitor* + *nudges can't bind, gates can*. Headline divergence: build-the-spine vs the-spine-is-the-disease → resolved to **less-but-unfakeable**.
- Contrarian's sharpest live risks to keep on the board: (a) every wall is new rot surface; (b) I can bypass my own hooks 6 ways → OQ2 server-side; (c) retrieval success ≠ utilization; (d) PKM automation 68% abandoned in 6mo → keep it minimal + self-proving.
- Next step: chunk confirmed above → Codex dual-brain cold-read (this doc + Code/Implementation Surface) → revise → build Phase 0 first.
