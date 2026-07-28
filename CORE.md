# CORE — The Young Dragon Tamer / מאלף הדרקונים הצעיר

**Stable truth. Changes rarely (monthly, not per-session). For what's open right now, see `STATUS.md`. For routing, see `HUB.md`.**

---

## 1. What this is

A multiplayer card game, originally designed in Hebrew as a physical game, converted to a digital multiplayer version deployed on Replit. Renamed July 8, 2026 from "Dragon Tamer" / "The Young Tamer" to its current, final name:

- **English:** The Young Dragon Tamer
- **Hebrew:** מאלף הדרקונים הצעיר

Live at `dragon-tamer-game.replit.app`. Replit user: `royb2007`. GitHub: `royb2007/dragon-tamer-game` (a snapshot from late May/June 2026 — not kept current; see HUB.md §7).

AstRoy owns both game design and full-stack implementation. Initial playtest target: ~50 friends. Long-term goal: financial return via a monetization ladder — trademark registration (ILPO) → print-on-demand validation → Kickstarter or publisher pitch, using the digital version as demo → exit optional later.

---

## 2. Architecture

| File | Role | Owner |
|---|---|---|
| `game_engine.py` | Game rules/logic (Python) | Claude — full engine authority, writes/fixes directly, tests before delivery |
| `server.py` | WebSocket server | Replit Agent territory |
| `index.html` | Frontend (vanilla JS) | Claude, full-file delivery |

Single port serves HTTP + WebSocket together. No frameworks — vanilla stack throughout.

**Deployment:**
- Dev: `bash watchdog.sh &`, wait for "listening", use the real `.replit.dev` dev URL — never trust the Preview pane or Run button (both show stale/false "crashed" status even when the server is healthy).
- Production port is `8080` (Replit-injected, expected — not a bug). Dev/local default is `int(os.getenv('PORT', 5000))`.
- Full restart procedure: `pkill -f watchdog.sh; pkill -f python; sleep 2; bash watchdog.sh` → wait for "listening" confirmation.
- Reserved VM deployment stuck on "Failed"/"healthcheck fail": `kill 1` in Shell (restarts container) → wait ~20-30s → Publishing tab → Advanced configuration → Publish button there (not the main Republish button).
- Recurring friction: Replit cache needs shell verification + hard refreshes; CSS selector mismatches fail silently — verify via a fresh browser tab on the real dev URL, not the embedded Preview.

---

## 3. Game rules (stable core)

- **Deck:** 54 cards (4 suits × 13 + 2 Jokers). Optional 2-deck mode (108 cards) for up to 10 players.
- **Default lobby state:** 2 decks, 6 players, 10 dragons to win (the 8/10/12-dragon options only appear when 2 decks is selected).
- **Elements (suit mapping):** Hearts=Fire 🔥, Clubs=Water 💧, Diamonds=Air, Spades=Earth. Dominant element grants +0.5 to effective rank.
- **Win condition:** collect a set number of Dragons. `WIN_DRAGONS` configurable 4–12 (default 5 for 1 deck, 10 for 2 decks).
- **Battles per campaign (`max_steps`):** 4 (1 deck), 5 (2 decks — capped down from 6 on July 7 for human pacing).
- **Round flow:** Leader declares element + battle count → players arrange hands → reveal → duel resolves → standings update.

### Special cards
- **Dragon** (Ace + 2 Jokers): the win-condition card.
- **Tamer** (2): base unit.
- **King** (13).
- **Queen** (12) + **Fury**: dominant Queen = effective 13.8 (beats any King, auto-beats non-dominant Queens); Queen Fury prize goes directly to the Queen owner's Main Pile, pinned rightmost/bottom; a Queen is beaten outright by a Wizard/Dragon of her own suit regardless of dominance (separate rule from Queen-vs-King).
- **Princess** (Jack).
- **Wizard** (9): dominant-element Wizard opens a blind portal into a chosen rival's Main Pile (top card), best-of-two represents, spoils go to the battle winner. Wizard inherits power from a same-element Tamer only when a matching-suit Tamer actually exists somewhere in the battle.
- **Time Dragon / Space Dragon** (Jokers): adopt the rank of the strongest regular Dragon on the field (including dominant bonus); weaker dragons excluded; duel settles it.

### Hebrew terminology (verbatim — never paraphrase)
- קרב = battle, יסוד = element, מערכה = campaign
- קופה ראשית = Main Pile, קופת קרב = Battle Pile, קופת צבירה = Accumulation Pile

Both English and Hebrew rulebooks are fully written and embedded in the in-game rules overlay (live SVG icons). Hebrew wording throughout is AstRoy's verbatim — never paraphrase it.

---

## 4. AI opponents — 19 strategies

Yaniv (Aggressive), Chen (Balanced), Itzik (Conservative), Hadas (Hoarder), Yotam (Adaptive), Meital (AntiDragon), Oren (Diplomat), Shir (Bluffer), Gil (Avenger), Dana (Maximalist), Amit (Minimalist), Noa (Opportunist), Alon (Purist), Lior (DragonHunter), Oded (Warden), Shahar (Raider), Zohar (Spearhead), Moti (Scholar), Idan (Gambler).

Offensive strategies (Aggressive, Maximalist, Bluffer, Opportunist, Raider; DragonHunter when dragonless) declare their own Wizard's element to arm the portal.

---

## 5. Rules of engagement (every AI tool, every session)

1. Always ask clarifying questions before producing — on every subject, no exceptions.
2. Present the bug list before making any changes. Wait for sign-off.
3. Deliver FULL files, never diffs or partial patches.
4. Verify file contents, not just filenames.
5. Never rebuild anything from scratch. Ask before acting when uncertain.
6. Respond in English (unless the working session is explicitly in Hebrew).
7. Shell commands go in a clearly marked, standalone, copy-paste-ready block.
8. One action at a time; confirm before the next.
9. Give step-by-step beginner guides on request — assume no prior knowledge, be patient.
10. Requires explicit confirmation before any irreversible action.

**File transfer convention:** Replit → Claude: download the file, attach as a real file (not pasted text). Claude → Replit: deliver the complete patched file, then CTRL+A → CTRL+V → CTRL+S in the Replit editor. Never diffs, never hand-edits.

**Credit discipline:** Opus/Fable reserved for engine bugs and multi-file reasoning only. Sonnet handles everything else in Claude — UI, rules text, docs, hub maintenance. ChatGPT/free tools handle business, art direction, YouTube. See `HUB.md` §1 for the full routing table.

---

## 6. Commercial context

Monetization ladder: trademark (מאלף הדרקונים הצעיר / The Young Dragon Tamer, at ILPO) → print-on-demand validation → Kickstarter or publisher pitch → exit optional. Card/box art still pending a medieval aesthetic direction (Midjourney/Leonardo.ai/Scenario explored). Box art must carry the current name before print.

A YouTube channel is planned covering the game's development, AI topics, and AstRoy's own process/perspective — multiple video formats, not just a devlog. CapCut is the current video-editing tool (active subscription), but this isn't locked in — open to switching to a different editor if it gives better results or a more comfortable workflow, even at a cost of up to $30.
