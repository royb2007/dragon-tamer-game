# ASSETS — The Young Dragon Tamer / מאלף הדרקונים הצעיר

**Append-only log of what was made, where, and by what tool. Add new entries at the bottom — never delete or rewrite old ones.**

---

- **Late May/June 2026** — GitHub repo `royb2007/dragon-tamer-game` created (Replit). Snapshot only — not kept current since (see `HUB.md` §7).
- **Early July 2026** — Sound system: 14 Web Audio API sounds built directly in `index.html` (duel start/win, Queen Fury, portal, Time/Space Dragon, reveal, pick, round/game win, battle loss, arrange-hand shuffle, etc.). Tool: Claude.
- **Early July 2026** — Card back art (base64 JPEG) embedded across lobby/waiting/rules/round-end/avatars/piles. White dragon SVG icon replacing all 🐉 emoji. Tool: Claude, embedded directly in `index.html`.
- **July 8, 2026** — Full English + Hebrew rulebooks written and embedded in the in-game rules overlay with live SVG icons. Tool: Claude, in `index.html`.
- **Late June 2026** — `NEW_SESSION_PROMPT.md` and `DRAGON_TAMER_SKILL.md` created for session continuity. Tool: Claude. **Superseded July 27, 2026** by `HUB.md`/`CORE.md`/`STATUS.md`/`ASSETS.md` — kept for history, no longer the active handoff method.
- **July 18, 2026** — Printable A4 flyer (PDF) for the WhatsApp group: gold/navy on ivory, real QR code linking to the WhatsApp group invite, drawn gold diamond ornaments (font had no emoji support). Tool: Claude (reportlab + qrcode script). Delivered as final PDF with the real group link baked in.
- **July 18, 2026** — Standalone game QR code (PNG, linking to `dragon-tamer-game.replit.app`). Tool: Claude. Added to the flyer as a smaller second QR ("או שחקו ישירות:").
- **July 18, 2026** — Two email drafts (short/casual and longer/formal) for sending the flyer PDF to the group. Tool: Claude.
- **July 27, 2026** — `HUB.md` built with Opus/Fable (routing table, credit-discipline rules, file structure). Currently living in Replit alongside the game.
- **July 27, 2026** — `CORE.md` and `STATUS.md` built with Claude (Sonnet), synthesizing all prior project knowledge into the new hub structure.
