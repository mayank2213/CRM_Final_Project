# GitHub issue drafts

The connected GitHub integration returned `403 Resource not accessible by integration` when creating issues in `mayank2213/CRM_Final_Project`. These are the exact issue bodies prepared for posting once issue-write access is available.

## Issue 1 — Mayank

**Title:** `[Mayank] CRM foundation, auth, schema, companies/contacts, and shared UI shell`

**Scope:** Flask scaffold; multi-user auth and server-side visibility helpers; users, companies, contacts, pipeline stages, deals, deal stage history, and activities schema; seed command; company/contact CRUD; shared navigation, Bootstrap Icons-only UI, documented 60:30:10 palette, and one reusable Add/Edit modal per object.

**Acceptance:** Fresh seed works; protected routes require login; reps only receive owned records from backend queries; managers see all; company/contact links work; one modal handles add/edit; handoff routes and models are documented.

## Issue 2 — Uday

**Title:** `[Uday] CRM pipeline, stage-history audit, activities, dashboard, and integration tests`

**Scope:** Deal CRUD; pipeline board; stage transitions from board and direct edit; stage-history timeline; deal/contact activities; dashboard summaries; search/filter and validation; acceptance tests for visibility and audit history.

**Acceptance:** Moving a deal through stages creates ordered audit rows; board and detail paths behave consistently; activities persist newest-first; direct URL access respects visibility; seed data spans all stages; shared palette, Bootstrap Icons, and Add/Edit modal pattern are preserved.

## Integration follow-up

After both issues are complete, open an integration issue/PR to merge schema assumptions, run the full acceptance checklist, and document the diff-review correction for the common failure where stage history is written only from the board path and not direct deal edits.
