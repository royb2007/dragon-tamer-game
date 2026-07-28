---
name: dragon-tamer
description: Use this skill whenever working on The Young Dragon Tamer (מאלף הדרקונים הצעיר) project — any file in the dragon-tamer-game repo (game_engine.py, server.py, index.html), any mention of Dragon Tamer, HUB.md, CORE.md, STATUS.md, ASSETS.md, or Replit deployment for this project. Trigger on the start of any new session touching this codebase.
---

# The Young Dragon Tamer — session start

Before doing anything else this session:

1. Read `HUB.md`, `CORE.md`, and `STATUS.md` from the repo root (in that order). If any are missing, say so explicitly before proceeding — do not guess at their contents.
2. If the task involves assets (art, audio, printed materials, video), also check `ASSETS.md` so you don't duplicate something that already exists.

## Non-negotiable rules (apply even if CORE.md is somehow unavailable)

- Always ask clarifying questions before producing anything — on every subject, no exceptions.
- Present a bug list BEFORE making any changes. Wait for explicit sign-off.
- Deliver FULL files, never diffs or partial patches, never manual find-and-replace instructions.
- Verify file contents, not just filenames, before acting on them.
- NEVER rebuild anything from scratch. If something looks broken or missing, ask before acting.
- Respond in English unless the session is explicitly being run in Hebrew.
- Any shell command or CLI instruction goes in its own clearly marked, copy-paste-ready block.
- One action at a time. Confirm before moving to the next step.
- Give step-by-step, no-assumed-knowledge guides when asked for one.
- Never touch `.claude/`, `node_modules`, or deployment secrets without being asked.

## Credit discipline

This project intentionally spreads work across model tiers to control cost:
- **Opus / Fable** — reserved for engine bugs (`game_engine.py`) and multi-file reasoning only.
- **Sonnet** — everything else: UI (`index.html`), rules text, hub docs (HUB/CORE/STATUS/ASSETS), planning.
- If you notice a task that's routine (doc editing, planning, UI tweaks) is about to consume a premium-tier session, flag it — the user would rather switch models than spend the credits.

## After the session

Before ending a work session, offer to update `STATUS.md` with what changed, and `ASSETS.md` if anything new was produced (art, audio, documents, video). Don't update `CORE.md` unless something genuinely stable/architectural changed — it's meant to stay quiet.
