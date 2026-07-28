# HUB — The Young Dragon Tamer / מאלף הדרקונים הצעיר

**This is the entry point. Read this first, then go where it sends you.**

Live game: `dragon-tamer-game.replit.app` · Replit user: `royb2007` · GitHub repo: `royb2007/dragon-tamer-game` (likely out of date — see §7)

Files in this hub:
- `HUB.md` — this file. Routing. Rarely changes.
- `CORE.md` — stable truth: architecture, terminology, rules of engagement, deployment. Changes monthly.
- `STATUS.md` — open bugs, awaiting live test, in-progress threads, next 3 steps. Changes every session.
- `ASSETS.md` — append-only log of what was made where.

---

## 1. Routing table — what am I working on?

| Task | Go to | Model / tool | What to bring | Notes |
|---|---|---|---|---|
| Engine bug, rules logic, duel math | Claude | Opus/Fable (complex) · Sonnet (routine) | `game_engine.py` attached + `STATUS.md` | Cheapest file to iterate on. Demand engine-simulation proof before delivery. |
| UI fix, layout, cards, sounds | Claude | Sonnet | `index.html` attached + `STATUS.md` | **Expensive file.** Batch several UI fixes into ONE delivery. |
| Frontend redesign (elliptical table) | Claude | Sonnet | Latest prototype | Design NOT finished. Finish design → test → only then implement into `index.html`. Two separate stages. |
| Server, ports, deployment config | Replit Agent | — | — | `server.py` is Agent territory. |
| Rules text (EN/HE) | Claude | Sonnet | The exact section + locked wording | Hebrew is verbatim AstRoy's. Never paraphrase it. |
| Deploy / restart / verify | Replit Shell | — | Commands in `CORE.md` | Never trust the Preview pane or Run button. |
| Hub docs, planning, structure | Claude | **Sonnet** | This file | Do NOT spend Opus/Fable credits on document work. |
| Business, trademark, POD, Kickstarter | ChatGPT | — | `prompts/business.md` + `STATUS.md` | Free tier fine. No Claude credits. |
| Card art, box art, visual direction | ChatGPT / Midjourney / Leonardo / Scenario | — | `prompts/game-design.md` | Medieval aesthetic. Box art must carry the new name before print. |
| Flyers, printables, layouts | Canva | — | — | Log the result in `ASSETS.md`. |
| YouTube scripting, devlog → video | ChatGPT | — | `prompts/youtube.md` + `STATUS.md` | Channel covers the game, AI topics, and personal perspective — three formats, not one. |
| Video editing | **CapCut** (subscription active) | — | — | Locked in. Revisit only if a specific need CapCut can't meet appears. |

---

## 2. Starting a new conversation

**Claude with the dragon-tamer skill installed:** paste `STATUS.md` only. The skill carries the rest.

**Claude without the skill, or any other model:** paste in this order:
1. `CORE.md`
2. `STATUS.md`
3. The relevant `prompts/*.md` if the task is business / design / video

Nothing else. Do not paste chat history.

**Trying a different model:** the files above are model-agnostic by design — the same paste works in ChatGPT, Gemini, or anything future. If a model's answer contradicts `CORE.md`, `CORE.md` wins: it is the record of decisions already made and tested.

---

## 3. Heavy build-once tools

Dashboards, PDF generators, flyer scripts, benchmark harnesses: these get their **own cheap conversation**, are built once, and are then never rebuilt — only their data file changes. They must never occupy an expensive session, and they cost nothing once they exist.

---

## 4. How to move a file between tools

**Replit → Claude:** open the file in Replit → download it → attach it to the chat as a real file attachment (not pasted text).

**Claude → Replit:** Claude delivers the COMPLETE patched file. In Replit: open the file → CTRL+A → CTRL+V → CTRL+S. Never apply diffs or hand-edit.

---

## 5. Rules of engagement (every AI tool, every session)

1. **Ask questions.** Always ask to clarify and guide before producing — on every subject, without exception.
2. Present the bug list BEFORE making any changes. Wait for sign-off.
3. Deliver FULL files, never diffs or partial patches.
4. Verify file CONTENTS, not just filenames.
5. Never rebuild anything from scratch. Ask before acting when uncertain.
6. Respond in English.
7. Shell commands in a clearly marked standalone block, copy-paste ready.
8. One action at a time; confirm before the next.
9. **Step-by-step beginner guides on request** — name every click, assume no prior knowledge, be patient.

---

## 6. Credit discipline

- Opus / Fable: engine bugs and multi-file reasoning only.
- Sonnet: everything else in Claude — UI, rules text, docs, planning, hub maintenance.
- ChatGPT / free tools: business, art direction, YouTube, brainstorming.
- Biggest single saving: batching `index.html` fixes instead of shipping them one at a time.

---

## 7. Where these files live

For now: **Replit**, alongside the game. Simple, one place, nothing new to learn.

GitHub (`royb2007/dragon-tamer-game`) holds a snapshot from around late May / early June 2026 and has not been updated since — everything since has gone Claude → Replit directly. Moving the hub to GitHub is a later, optional upgrade for version history and shareable links. Not needed today.

---

## 8. Session close-out

Update `STATUS.md` at the **start** of the next session, not the end of the last one — you'll be too tired, out of credits, or the tab will have closed. You're opening the file to paste it anyway; edit it then.
