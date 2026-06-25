# SPEC — Workboard

> Auto-scaffolded for spec `SPEC.md`. Fill the Items from the spec.
> Standard: `~/.claude/rules/workboard.md`. CLAIM before working; commit the claim line first.


> **What this is:** the single shared board for this work. Multiple agents/terminals coordinate THROUGH this file (blackboard pattern). One board per spec/project.
> **Statuses:** `- [ ]` To-Do · `🔄 DOING` (claimed) · `- [x]` Done. Never delete an item — strike it `[x]` so history stays.
> **Golden rule — CLAIM BEFORE YOU WORK.** Before touching an item, add the STATUS claim line and **commit it immediately**. If an item already has a live `🔄 DOING` claim from another agent, skip it.

---

## How to use (every agent reads this first)
1. **Pick** the top unclaimed `- [ ]` item.
2. **Claim it:** add `- **STATUS:** 🔄 DOING — <agent/terminal>, <date+time> (<one-line note>)` under the item, then **commit that line right away** (this is the lease that stops collisions).
3. **Do the work.** Diagnose first; decide; execute.
4. **Close it:** flip `- [ ]` → `- [x]`, replace STATUS with `✅ DONE`, add a `- **RESOLVED:** <date> — <what was done>` line.
5. **Commit per item** (one item ≈ one commit) — keeps history clean and shrinks the edit-collision window.
6. If blocked, leave it `🔄 DOING` with a `- **BLOCKED:** <why / waiting on>` line so no one silently re-grabs it.

---

## Items

- [ ] **W1 — <short title>.**
  - **What:** <the task in one or two plain lines>
  - **STATUS:** _(unclaimed)_
  - **RESOLVED:** _(pending)_

- [ ] **W2 — <short title>.**
  - **What:** <…>
  - **STATUS:** _(unclaimed)_
  - **RESOLVED:** _(pending)_

---

## Done / no-action (so items aren't re-triaged)
<!-- move adjudicated or won't-fix items here with a one-line reason -->

## Definition of done
Every item is `[x]` with a RESOLVED line, each committed; nothing silently dropped.
