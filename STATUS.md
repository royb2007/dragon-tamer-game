# STATUS — The Young Dragon Tamer / מאלף הדרקונים הצעיר

**Active state. Changes every session. For stable truth, see `CORE.md`. For routing, see `HUB.md`.**

Last updated: July 27, 2026

---

## 🐛 Open bugs / known issues

- **Server OOM crashes (exit 137) under gameplay load.** Old issue, partial mitigations in place (websockets==12.0 pin, room cleanup loop). Durable fix is a paid Reserved VM (already provisioned, ~$20/mo) — not yet confirmed as fully resolving it.
- **Live gameplay UI is English-only** even when Hebrew rules are selected (lobby fields, banners, hand label). Bigger UI decision, not yet greenlit — would mean translating the actual UI, a separate large piece of work from the rulebook translation (which is done).
- **Stray server files** still in the Replit tree (server_original.py, server_ws.py, server_ws2.py, server_ws_260526_1257.py, all_fixes210526.py, all_code.txt). Not urgent — just confirm main.py/start.sh reference the correct server.py.

## 📜 Rules-text gaps (documentation only, engine is correct)

- No plain-language card-ranking summary exists — current rules are all narrative.
- Victory section doesn't explicitly state only Dragon-type cards count toward the win condition.
- Queen's vulnerability to a same-suit Wizard/Dragon (she loses outright regardless of dominance) is confirmed real engine behavior but isn't documented anywhere in the rules text. Offered to add this — not yet confirmed by AstRoy.

## ⏳ Awaiting live test (fixed and verified in simulation, not yet confirmed by AstRoy in an actual game)

- Time Dragon "back"-claim success-flag fix (July 18) — server now sends an explicit `success` boolean so the Chronicle log can't show contradictory messages.
- Wizard-stolen-Joker fix (July 19) — jokers now correctly adopt the strongest dragon's value even when stolen via a Wizard's portal, instead of silently keeping Wizard identity with no Tamer to inherit from.
- Card display fixes (July 17): hand overlap spacing, Princess/Ace showing true numeric value, dominant Queen engine value bump (13.5→13.8), muted autoplay music behavior, Time Dragon pile visual sync on steal.
- Element icon corner move (July 18): element icon now sits diagonally opposite the rank number instead of sharing its corner.
- Lobby defaults change (July 23): page now loads with 2 decks / 6 players / 10 dragons by default; 8/10/12-dragon options only show when 2 decks is selected.

## 🚧 In-progress threads

- **Frontend redesign — elliptical table.** ~17 interactive prototypes explored, converging on a vertical ellipse (portrait) / horizontal ellipse (landscape), with AstRoy's own seat always anchored. Design not finished — next steps are: finish the design → test it → only then integrate into `index.html`. Two separate stages, not to be rushed together.
- **Hub system build (this project).** `HUB.md` done, `CORE.md` done, `STATUS.md` (this file) in progress, `ASSETS.md` not yet started.
- **Card face art.** Medieval aesthetic direction still pending; AI art tools explored (Midjourney, Leonardo.ai, Scenario) but no direction locked in yet. Box art must carry the current game name before anything goes to print.

## 🚀 Commercial / outstanding

- Trademark registration (מאלף הדרקונים הצעיר / The Young Dragon Tamer) at ILPO — not yet filed; TM attorney clearance advised first.
- 20-second action timer — not implemented.
- Opening draw-for-lead ceremony display — deferred, not implemented.

---

## Next steps (proposed — confirm or reprioritize)

1. Finish `ASSETS.md` to complete the hub (this session).
2. Get live confirmation on the July 17-23 fixes above — several rounds of unconfirmed fixes are stacking up, worth a dedicated playtest pass before adding anything new.
3. Decide on card art direction, since it's blocking both box art and the print/Kickstarter path.
