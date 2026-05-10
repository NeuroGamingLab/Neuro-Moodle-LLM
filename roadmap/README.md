# roadmap/

Implementation outlines and work-package briefs for this project, kept in
version control alongside the security and ML reviews that motivate them.

Sister folder of [`../audit-reports/`](../audit-reports/) (security) and
[`../ml-enhancement-reviews/`](../ml-enhancement-reviews/) (ML systems).
**Same filing conventions, different lens** — this folder turns
*recommendations* into *plans*.

## Conventions

- **Filename:** `YYYY-MM-DD-<scope>-plan.md` (e.g.
  `2026-05-09-strategic-improvements-plan.md`).
- **Scope** is usually the bundle of work being planned (a release, a
  quarter, a single strategic bet).
- **One plan per file.** Never edit a past plan to "fix" reality —
  write a new plan and reference the old one in its *Scope* section.
- Each section that describes a deliverable should follow the same
  template:

  ```text
  Goal       → one-sentence outcome
  Components → the moving parts
  Phases     → table (phase, outcome, duration)
  Artifacts  → tables, files, dashboards produced
  Risks      → what can go wrong, with mitigations
  Done       → measurable acceptance criteria
  Effort    → engineer-weeks estimate
  ```

  Consistency across plans makes them comparable and skimmable.

## What goes here vs other folders

| Folder | Lens | Owns the question |
|--------|------|-------------------|
| `audit-reports/` | Security | "What can break or leak?" |
| `ml-enhancement-reviews/` | ML systems | "What can be smarter, faster, or more reliable?" |
| `roadmap/` | Delivery / planning | "How are we actually going to build this, in what order, and when is it done?" |
| `instructions.txt` + `README.md` | Product / architecture | "What is the system supposed to do?" |

Plans **must** respect findings in `audit-reports/` and recommendations in
`ml-enhancement-reviews/`. If a plan deliberately defers or contradicts one,
call it out in the plan's *Risks* section with a reference to the source
document.

## Re-plan triggers

Open a fresh plan (and add a new file here) when any of:

- A `audit-reports/` finding requires a new work-package
- A `ml-enhancement-reviews/` Strategic Improvement is committed to
- **Synthetic publish / Moodle authoring / quiz-eval** surfaces ship or change materially
- Project objectives in `instructions.txt` change
- A planned milestone slips materially or is descoped
- A new external constraint lands (budget, regulatory scope, deadline)

## Status hygiene

Plans here are **decision records**, not living tickets. Track day-to-day
execution in your issue tracker; update plans only when the *plan itself*
changes (new phase added, milestone re-scoped, item killed). When superseded,
add a one-line note at the top of the old plan pointing to its replacement.

---

*Architecture and design: **Dang-Tue Hoang** — AI Engineer.*
