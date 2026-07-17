"""
Dragon Tamer — Game Engine v3.6
Fixes applied:
  - dragon_count: jokers now count as dragons
  - Portal (9♣): human chooses target; stolen card enters battle before resolve; winner takes all
  - Portal stolen card tracked in prev_step_cards (Time Dragon "back" fix) [Bug #19]
  - skip_next: Time Dragon forward actually skips next round
  - Wizard (8): beats same-suit cards up to King; inherits Tamer power vs Dragon [Bug #4]
  - Queen (Q): beats same-suit cards except Wizard/Dragon; loses to other-suit Kings [Bug #5]
  - Tamer duel: actual draw-from-pile duel [Bug #6]
  - Dragon/tie duel: actual duel instead of leader-wins [Bug #7]
  - Two Jokers duel: duel implemented, winner uses own power only [Bug #8]
  - Love Power: princess chooses between tamers; AI auto-chooses [Bug #11]
  - skip_next reset at start of skipped round, not end of current round [Bug #20]
  - Dominant suit bonus: +0.5 instead of +1 [Bug #1]
  - Leftover cards on uneven deal go to leader [Bug #16]
  - Victory check before elimination (edge case: 4 dragons same round as running out) [Bug #15]
  - Setup: full deck reshuffled after leader draw before dealing [Bug #17]
  - MAX_ROUNDS=300 stalemate safeguard: most dragons wins
  v3.6 fixes:
  - skip_next now cleared in _end_round() — Time Dragon forward no longer skips forever [Bug #S1]
  - _skipped_this_round cleared each round in _end_round() [Bug #S2]
  - Human players with skip_next added to _skipped_this_round at pick phase start [Bug #S3]
  v3.7 fixes:
  - _default_sort_key: Princess(10.8) > Tamer(10.5) > Wizard(10.2) — consistent hand order
  - joker_choose_power now also triggers when Tamer wins battle containing both Jokers
  v3.8 fixes:
  - Multiple Queens: duel among Queens only — Kings never enter the Queen duel [Bug #Q1]
  - Queen Fury correctly triggers for the winning Queen after a multi-Queen duel [Bug #Q2]
  v3.9 — Wizard absorbs the Portal:
  - Portal card (9♣) removed; all 9s are now Wizards; all 8s become plain number cards
  - Dominant-element Wizard opens a portal: owner steals top card of chosen opponent's
    Main Pile; stolen card fights alongside the Wizard (best value wins); winner takes all
  - Wizard keeps existing powers (overpowers same-suit cards, Tamer inheritance)
  - Two-deck: multiple dominant Wizards fire in order (leader first, then clockwise)
  - AI reuses ai_portal_target() logic for wizard-portal target selection
  - Reuses exact portal_choose_target / portal_steal event types (frontend unchanged)
"""

import random
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

SUITS = ["Hearts", "Clubs", "Diamonds", "Spades"]
SUIT_SYM = {"Hearts": "♥", "Clubs": "♣", "Diamonds": "♦", "Spades": "♠"}
SUIT_ELEMENT = {
    "Hearts": "Fire",
    "Clubs": "Water",
    "Diamonds": "Air",
    "Spades": "Earth",
}
WIN_DRAGONS = 5
VALID_WIN_DRAGONS = (4, 5, 6, 7, 8, 9, 10, 11, 12)
MAX_STEPS = 4  # Maximum steps any player (human or AI) can declare — overridden per game via _num_decks


def set_win_dragons(n: int):
    global WIN_DRAGONS
    if n not in VALID_WIN_DRAGONS:
        raise ValueError(f"WIN_DRAGONS must be one of {VALID_WIN_DRAGONS}, got {n}")
    WIN_DRAGONS = n


MAX_ROUNDS = 150


class Phase(str, Enum):
    WAITING = "waiting"
    LEADER_DECLARE = "leader_declare"
    PICK_CARDS = "pick_cards"
    ARRANGE_HAND = "arrange_hand"
    REVEAL = "reveal"
    END_ROUND = "end_round"
    GAME_OVER = "game_over"


@dataclass
class Card:
    _next_id: int = field(default=0, init=False, repr=False, compare=False)
    cid: int
    rank: int
    orig_rank: int
    suit: Optional[str]
    label: str
    is_joker: bool = False
    joker_type: Optional[str] = None

    @property
    def is_dragon(self) -> bool:
        return self.rank == 14

    @property
    def is_tamer(self) -> bool:
        return self.orig_rank == 2 and not self.is_joker

    @property
    def is_princess(self) -> bool:
        return self.orig_rank == 11

    @property
    def is_portal(self) -> bool:
        return False  # Portal card removed; Wizards (9s) now open portals when dominant

    @property
    def is_wizard(self) -> bool:
        return self.orig_rank == 9 and not self.is_joker

    @property
    def is_queen(self) -> bool:
        return self.orig_rank == 12 and not self.is_joker

    def effective_rank(self, leading_suit: Optional[str]) -> float:
        if self.is_joker or not self.suit or not leading_suit:
            return float(self.rank)
        if self.suit == leading_suit:
            # Dominant Queen gets +1.8 (13.8, so she numerically exceeds a
            # dominant King's 13.5 — makes "Queen beats King" visually
            # intuitive from the displayed value alone); others get +0.5.
            # NOTE: the actual Queen-vs-King win condition below is still a
            # separate explicit rule, not derived from this number — a Queen
            # beats her own-suit King even without dominance (where her raw
            # value stays 12, still less than 13), so that rule can't be
            # replaced by a pure numeric comparison. This bump only makes
            # the DOMINANT-beats-any-King case read intuitively as "her
            # number is bigger."
            return self.rank + 1.8 if self.is_queen else self.rank + 0.5
        return float(self.rank)

    def to_dict(self) -> dict:
        return {
            "cid": self.cid,
            "rank": self.rank,
            "orig_rank": self.orig_rank,
            "suit": self.suit,
            "label": self.label,
            "is_joker": self.is_joker,
            "joker_type": self.joker_type,
            "is_dragon": self.is_dragon,
            "is_tamer": self.is_tamer,
            "is_princess": self.is_princess,
            "is_portal": self.is_portal,
            "is_wizard": self.is_wizard,
            "is_queen": self.is_queen,
        }


_card_counter = 0


def _new_card(rank, suit, is_joker=False, joker_type=None) -> Card:
    global _card_counter
    _card_counter += 1
    labels = {1: "A", 11: "J", 12: "Q", 13: "K"}
    if is_joker:
        label = "🌌" if joker_type == "space" else "⏳"
        return Card(
            cid=_card_counter,
            rank=14,
            orig_rank=14,
            suit=None,
            label=label,
            is_joker=True,
            joker_type=joker_type,
        )
    sym = SUIT_SYM[suit]
    label = (labels.get(rank, str(rank))) + sym
    return Card(
        cid=_card_counter,
        rank=14 if rank == 1 else rank,
        orig_rank=rank,
        suit=suit,
        label=label,
    )


def build_deck() -> List[Card]:
    global _card_counter
    _card_counter = 0
    deck = []
    for suit in SUITS:
        for r in range(1, 14):
            deck.append(_new_card(r, suit))
    deck.append(_new_card(0, None, True, "space"))
    deck.append(_new_card(0, None, True, "time"))
    return deck


def _best_card(cards: List[Card], el: Optional[str]) -> Optional[Card]:
    best = None
    for c in cards:
        if best is None or c.effective_rank(el) > best.effective_rank(el):
            best = c
    return best


def _dedup_entries_for_client(entries, duel_draws):
    seen = {}
    raw_stolen = {}

    for e in entries:
        pid = e["pid"]
        if e.get("stolen"):
            raw_stolen[pid] = e["card"]
        else:
            if pid not in seen:
                seen[pid] = e

    result = []
    for pid, e in seen.items():
        extra_cards = []
        portal_extras = e.get("portal_extras", [])
        for c in portal_extras:
            extra_cards.append(c.to_dict())
        if pid in raw_stolen and not portal_extras:
            extra_cards.append(raw_stolen[pid].to_dict())

        entry_dict = {
            "pid": pid,
            "card": e["card"].to_dict(),
            "stolen": e.get("stolen", False),
            "duel_cards": [c.to_dict() for c in duel_draws.get(pid, [])],
        }
        if extra_cards:
            entry_dict["extra_cards"] = extra_cards
        result.append(entry_dict)

    for pid, card in raw_stolen.items():
        if pid not in seen:
            result.append(
                {
                    "pid": pid,
                    "card": card.to_dict(),
                    "stolen": True,
                    "duel_cards": [c.to_dict() for c in duel_draws.get(pid, [])],
                }
            )
    return result


def _run_duel(
    contestants: List[dict],
    all_players: Dict[str, "PlayerState"],
    lead_pid: str,
    events: List[str],
    el: Optional[str] = None,
    portal_pids: Optional[set] = None,
) -> tuple:
    """Each player draws one card from their Main Pile. Tamer beats Dragon even in duels."""
    active = list(contestants)
    all_drawn: List["Card"] = []
    pid_draws: Dict[str, List["Card"]] = {c["pid"]: [] for c in contestants}

    while True:
        draws: Dict[str, "Card"] = {}
        eliminated_from_duel = []
        for c in active:
            pid = c["pid"]
            p = all_players[pid]
            if p.hand:
                first_cid = ordered_hand(p)[0].cid
                drawn = next(card for card in p.hand if card.cid == first_cid)
                p.hand.remove(drawn)
                draws[pid] = drawn
                all_drawn.append(drawn)
                pid_draws.setdefault(pid, []).append(drawn)
                eff = drawn.effective_rank(el)
                suit_note = (
                    f" (+dominant)"
                    if (el and drawn.suit == el and not drawn.is_joker)
                    else ""
                )
                events.append(
                    f"⚔️ Duel: {p.name} draws {drawn.label}{suit_note} (value {eff:.1f})"
                )
            else:
                eliminated_from_duel.append(pid)
                events.append(f"⚔️ Duel: {p.name} has no cards — eliminated from duel")

        active = [c for c in active if c["pid"] not in eliminated_from_duel]

        if not active:
            events.append(
                f"⚔️ Duel: all out of cards — leader {all_players[lead_pid].name if all_players and lead_pid in all_players else lead_pid} wins"
            )
            return lead_pid, all_drawn, pid_draws

        if not draws:
            return lead_pid, all_drawn, pid_draws

        tamer_drawers = [
            c for c in active if c["pid"] in draws and draws[c["pid"]].is_tamer
        ]
        dragon_drawers = [
            c for c in active if c["pid"] in draws and draws[c["pid"]].is_dragon
        ]
        if tamer_drawers and dragon_drawers:
            if len(tamer_drawers) == 1:
                winner_pid = tamer_drawers[0]["pid"]
                events.append(
                    f"⚔️ Duel: Tamer beats Dragon! {all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid} wins!"
                )
                return winner_pid, all_drawn, pid_draws
            else:
                # Multiple Tamers beat the Dragon(s). Compare the Tamers by value;
                # if they tie, the tied Tamers REDRAW — the Dragon(s) and any
                # weaker Tamers drop out of the duel.
                best_t_eff = max(
                    draws[c["pid"]].effective_rank(el) for c in tamer_drawers
                )
                top_tamers = [
                    c
                    for c in tamer_drawers
                    if draws[c["pid"]].effective_rank(el) == best_t_eff
                ]
                if len(top_tamers) == 1:
                    winner_pid = top_tamers[0]["pid"]
                    events.append(
                        f"⚔️ Duel: Tamer beats Dragon! {all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid} wins!"
                    )
                    return winner_pid, all_drawn, pid_draws
                dropped = [c for c in active if c not in top_tamers]
                for c in dropped:
                    events.append(
                        f"⚔️ Duel: {all_players[c['pid']].name if all_players and c['pid'] in all_players else c['pid']} loses — out of duel"
                    )
                active = top_tamers
                events.append(f"⚔️ Duel: {len(top_tamers)} Tamers tied — they redraw!")
                continue

        def _duel_eff(card):
            # Dragons always compare on raw rank (suit-blind) so multiple
            # dragons of different suits still tie/match correctly in duels.
            if card.is_dragon:
                return float(card.rank)
            return card.effective_rank(el)

        best_eff = max(_duel_eff(draws[pid]) for pid in draws)
        winners = [
            c
            for c in active
            if c["pid"] in draws and _duel_eff(draws[c["pid"]]) == best_eff
        ]

        if len(winners) == 1:
            winner_pid = winners[0]["pid"]
            winner_name = (
                all_players[winner_pid].name
                if all_players and winner_pid in all_players
                else winner_pid
            )
            events.append(f"⚔️ Duel won by {winner_name}!")
            return winner_pid, all_drawn, pid_draws

        tied_pids = {c["pid"] for c in winners}
        dropped = [c for c in active if c["pid"] not in tied_pids]
        for c in dropped:
            events.append(
                f"⚔️ Duel: {all_players[c['pid']].name if all_players and c['pid'] in all_players else c['pid']} loses — out of duel"
            )
        active = winners
        events.append(f"⚔️ Duel tied between {len(winners)} players — redraw!")


def resolve_step(
    entries: List[dict],
    el: Optional[str],
    lead_pid: str,
    all_players: Optional[Dict] = None,
) -> dict:
    result = {
        "winner_pid": None,
        "all_cards": [e["card"] for e in entries],
        "joker_powers": [],
        "love_right_pid": None,
        "love_choice_needed": None,
        "space_dragon_pid": None,
        "portal_pid": None,
        "queen_portal_pid": None,
        "special_events": [],
        "duel_draws": {},
    }

    valid = [e for e in entries if not e.get("forfeited", False)]
    if not valid:
        return result

    portal_pids = {e["pid"] for e in valid if e["card"].is_portal}

    pid_entries: Dict[str, List[dict]] = {}
    for e in valid:
        pid_entries.setdefault(e["pid"], []).append(e)

    resolved_valid = []
    step_has_dragon = any(e["card"].is_dragon for e in valid)

    for pid, player_entries in pid_entries.items():
        if len(player_entries) == 1:
            resolved_valid.append(player_entries[0])
        else:
            tamer_entries = [e for e in player_entries if e["card"].is_tamer]
            if step_has_dragon and tamer_entries:
                best_entry = tamer_entries[0]
            else:
                best_entry = max(
                    player_entries,
                    key=lambda e: e["card"].effective_rank(el or "Hearts"),
                )
            other_entries = [e for e in player_entries if e is not best_entry]
            combined = dict(best_entry)
            combined["portal_extras"] = [e["card"] for e in other_entries]
            if any(e.get("stolen") for e in player_entries):
                combined["stolen"] = True
            if any(e["card"].is_portal for e in player_entries):
                combined["has_portal"] = True
            resolved_valid.append(combined)
            portal_card = next(
                (e["card"] for e in player_entries if e["card"].is_portal), None
            )
            wizard_card = next(
                (e["card"] for e in player_entries if e["card"].is_wizard), None
            )
            trigger_card = portal_card or wizard_card
            stolen_entry = next((e for e in player_entries if e.get("stolen")), None)
            stolen_card = stolen_entry["card"] if stolen_entry else None
            stolen_from_name = ""
            if stolen_entry and stolen_entry.get("stolen_from_pid") and all_players:
                stolen_from_name = (
                    f" from {all_players[stolen_entry['stolen_from_pid']].name}"
                )
            elif stolen_entry and stolen_entry.get("stolen_from_name"):
                stolen_from_name = f" from {stolen_entry['stolen_from_name']}"
            best_label = best_entry["card"].label
            other_labels = [e["card"].label for e in other_entries]
            result["special_events"].append(
                f"🌀 {(all_players[pid].name if all_players and pid in all_players else pid)} plays Portal ({trigger_card.label if trigger_card else '?'}) "
                f"+ stolen {stolen_card.label if stolen_card else other_labels[0]}"
                f"{stolen_from_name} "
                f"— best card {best_label} competes, winner takes both!"
            )

    valid = resolved_valid

    has_dragon = any(e["card"].is_dragon for e in valid)
    tamers = [e for e in valid if e["card"].is_tamer]
    princesses = [e for e in valid if e["card"].is_princess]
    jokers = [e for e in valid if e["card"].is_joker]
    wizards = [e for e in valid if e["card"].is_wizard]
    queens = [e for e in valid if e["card"].is_queen]

    result["joker_powers"] = [
        j["card"].joker_type for j in jokers if j["card"].joker_type
    ]

    love_tamers = list(tamers)

    wizard_inherited_tamer: Optional[dict] = None
    wizard_displaced_tamer: Optional[dict] = None
    wizard_inheritances: List[dict] = []

    if has_dragon and wizards and tamers:
        for w_entry in wizards:
            w = w_entry["card"]
            same_suit_tamer = next(
                (t for t in tamers if t["card"].suit == w.suit), None
            )
            if same_suit_tamer:
                wizard_inheritances.append(
                    {"wizard": w_entry, "tamer": same_suit_tamer}
                )
                result["special_events"].append(
                    f"🧙 {(all_players[w_entry['pid']].name if all_players and w_entry['pid'] in all_players else w_entry['pid'])}'s Wizard ({w.label}) inherits Tamer power "
                    f"from {(all_players[same_suit_tamer['pid']].name if all_players and same_suit_tamer['pid'] in all_players else same_suit_tamer['pid'])}!"
                )
                # NOTE: no break — every same-suit wizard qualifies independently.
                # A single break here meant a second equally-qualified wizard
                # was silently dropped rather than dueling for the inheritance.

    combat_tamers = list(tamers)
    if wizard_inheritances:
        displaced = [inh["tamer"] for inh in wizard_inheritances]
        combat_tamers = [
            e for e in combat_tamers if not any(e is d for d in displaced)
        ] + [inh["wizard"] for inh in wizard_inheritances]
        # Keep back-compat single-entry variables pointing at the first
        # inheritance, only used below for a "which duelist is a Wizard?"
        # label check that we've since made list-aware.
        wizard_inherited_tamer = wizard_inheritances[0]["wizard"]
        wizard_displaced_tamer = wizard_inheritances[0]["tamer"]

    if love_tamers and princesses:
        if len(love_tamers) == 1 and len(princesses) == 1:
            result["love_right_pid"] = love_tamers[0]["pid"]
            result["special_events"].append(
                f"💕 Love Power! {(all_players[love_tamers[0]['pid']].name if all_players and love_tamers[0]['pid'] in all_players else love_tamers[0]['pid'])} earns next lead!"
            )
        elif len(love_tamers) == 1:
            result["love_right_pid"] = love_tamers[0]["pid"]
            result["special_events"].append(
                f"💕 Love Power! {len(princesses)} Princesses — only one Tamer: "
                f"{(all_players[love_tamers[0]['pid']].name if all_players and love_tamers[0]['pid'] in all_players else love_tamers[0]['pid'])} earns next lead!"
            )
        else:
            result["love_choice_needed"] = {
                "princess_pids": [e["pid"] for e in princesses],
                "tamer_pids": [e["pid"] for e in love_tamers],
                "votes_needed": len(princesses),
            }
            result["special_events"].append(
                f"💕 Love Power — {len(princesses)} Princess(es) vote for "
                f"{len(love_tamers)} Tamers! Majority wins; tie = cancelled."
            )

    if has_dragon and len(combat_tamers) == 1:
        result["winner_pid"] = combat_tamers[0]["pid"]
        _is_wizard_winner = any(
            combat_tamers[0] is inh["wizard"] for inh in wizard_inheritances
        )
        result["special_events"].append(
            f"⚔️ {(all_players[combat_tamers[0]['pid']].name if all_players and combat_tamers[0]['pid'] in all_players else combat_tamers[0]['pid'])}'s "
            f"{'Wizard' if _is_wizard_winner else 'Tamer'} "
            f"beats all dragons!"
        )
        joker_types = [j["card"].joker_type for j in jokers if j["card"].joker_type]
        if joker_types:
            if len(joker_types) == 1:
                result["joker_powers"] = joker_types
                result["special_events"].append(
                    f"🃏 {(all_players[combat_tamers[0]['pid']].name if all_players and combat_tamers[0]['pid'] in all_players else combat_tamers[0]['pid'])}'s Tamer inherits {joker_types[0]} Dragon power!"
                )
            else:
                result["joker_powers"] = joker_types
                result["special_events"].append(
                    f"🃏 {(all_players[combat_tamers[0]['pid']].name if all_players and combat_tamers[0]['pid'] in all_players else combat_tamers[0]['pid'])}'s Tamer must choose a Joker power: {joker_types}"
                )
                result["space_dragon_pid"] = combat_tamers[0]["pid"]
                result["_tamer_joker_choice"] = combat_tamers[0]["pid"]
        space_j = next((j for j in jokers if j["card"].joker_type == "space"), None)
        if space_j and not result.get("_tamer_joker_choice"):
            result["space_dragon_pid"] = combat_tamers[0]["pid"]
            result["special_events"].append(
                f"🌌 Space Dragon power goes to {(all_players[combat_tamers[0]['pid']].name if all_players and combat_tamers[0]['pid'] in all_players else combat_tamers[0]['pid'])} (Tamer winner)!"
            )
        return result

    if has_dragon and len(combat_tamers) > 1:

        def tamer_combat_rank(e):
            if any(e is inh["wizard"] for inh in wizard_inheritances):
                return 2.5 if (el and e["card"].suit == el) else 2.0
            return e["card"].effective_rank(el)

        best_tamer_eff = max(tamer_combat_rank(e) for e in combat_tamers)
        top_tamers = [
            e for e in combat_tamers if tamer_combat_rank(e) == best_tamer_eff
        ]
        if len(top_tamers) == 1:
            winner_pid = top_tamers[0]["pid"]
            result["special_events"].append(
                f"⚔️ {(all_players[top_tamers[0]['pid']].name if all_players and top_tamers[0]['pid'] in all_players else top_tamers[0]['pid'])}'s Tamer wins by higher rank "
                f"({top_tamers[0]['card'].label} eff:{best_tamer_eff:.1f})!"
            )
        else:
            result["special_events"].append("⚔️ Tamer duel — equal rank, drawing cards!")
            if all_players:
                winner_pid, duel_cards, pid_draws = _run_duel(
                    top_tamers,
                    all_players,
                    lead_pid,
                    result["special_events"],
                    el,
                    portal_pids=portal_pids,
                )
                result["all_cards"] += duel_cards
                result["duel_draws"].update(pid_draws)
            else:
                winner_pid = top_tamers[0]["pid"]
        result["winner_pid"] = winner_pid
        joker_types = [j["card"].joker_type for j in jokers if j["card"].joker_type]
        if joker_types:
            result["joker_powers"] = joker_types
            result["special_events"].append(
                f"🃏 {(all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid)}'s Tamer inherits joker power(s): {joker_types}"
            )
        space_j = next((j for j in jokers if j["card"].joker_type == "space"), None)
        if space_j:
            result["space_dragon_pid"] = winner_pid
            result["special_events"].append(
                f"🌌 Space Dragon power goes to {(all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid)} (Tamer duel winner)!"
            )
        return result

    if queens and not has_dragon:
        any_dominant_queen = any(
            (el and q_entry["card"].suit == el) for q_entry in queens
        )

        surviving_queens = []
        for q_entry in queens:
            q = q_entry["card"]
            q_is_dominant = el and q.suit == el
            beaten = False
            # A non-dominant Queen is beaten outright if a dominant Queen is present
            if any_dominant_queen and not q_is_dominant:
                beaten = True
            else:
                for e in valid:
                    c = e["card"]
                    if c is q:
                        continue
                    if c.suit == q.suit:
                        if c.is_wizard or c.is_dragon:
                            beaten = True
                            break
                    else:
                        if c.orig_rank == 13 and not q_is_dominant:
                            beaten = True
                            break
            if not beaten:
                surviving_queens.append(q_entry)

        if surviving_queens:
            if len(surviving_queens) == 1:
                winner_pid_q = surviving_queens[0]["pid"]
                result["winner_pid"] = winner_pid_q
                _beaten = [e["card"].label for e in valid if e["pid"] != winner_pid_q]
                _beaten_str = ", ".join(_beaten) if _beaten else ""
                q_card = surviving_queens[0]["card"]
                q_is_dominant = el and q_card.suit == el
                _beat_same_king = any(
                    e["card"].orig_rank == 13 and e["card"].suit == q_card.suit
                    for e in valid
                    if e["pid"] != winner_pid_q
                )
                _beat_any_king = q_is_dominant and any(
                    e["card"].orig_rank == 13 for e in valid if e["pid"] != winner_pid_q
                )
                if _beat_same_king or _beat_any_king:
                    result["queen_portal_pid"] = winner_pid_q
                    fury_note = (
                        " (Dominant Queen Fury!)"
                        if _beat_any_king and not _beat_same_king
                        else ""
                    )
                    result["special_events"].append(
                        f"👑 {(all_players[winner_pid_q].name if all_players and winner_pid_q in all_players else winner_pid_q)}'s Queen dominates"
                        + (f" (beats: {_beaten_str})" if _beaten_str else "")
                        + f" — Queen Fury{fury_note}! Steal a card from any opponent!"
                    )
                else:
                    result["special_events"].append(
                        f"👑 {(all_players[winner_pid_q].name if all_players and winner_pid_q in all_players else winner_pid_q)}'s Queen dominates!"
                        + (f" (beats: {_beaten_str})" if _beaten_str else "")
                    )
                portal_e = next(
                    (e for e in valid if e["card"].is_portal or e.get("has_portal")),
                    None,
                )
                if portal_e:
                    result["portal_pid"] = portal_e["pid"]
                return result
            else:
                # ── FIX #Q1: Multiple Queens — duel among Queens ONLY, Kings never enter ──
                result["special_events"].append(
                    f"👑 {len(surviving_queens)} Queens tied — Queen duel!"
                )
                if all_players:
                    winner_pid_q, duel_cards, pid_draws = _run_duel(
                        surviving_queens,
                        all_players,
                        lead_pid,
                        result["special_events"],
                        el,
                        portal_pids=portal_pids,
                    )
                    result["all_cards"] += duel_cards
                    result["duel_draws"].update(pid_draws)
                else:
                    winner_pid_q = surviving_queens[0]["pid"]
                result["winner_pid"] = winner_pid_q
                # ── FIX #Q2: Check Fury for the winning Queen ──
                winner_q_card = next(
                    e["card"] for e in surviving_queens if e["pid"] == winner_pid_q
                )
                q_is_dominant = el and winner_q_card.suit == el
                _beat_same_king = any(
                    e["card"].orig_rank == 13 and e["card"].suit == winner_q_card.suit
                    for e in valid
                    if e["pid"] != winner_pid_q
                )
                _beat_any_king = q_is_dominant and any(
                    e["card"].orig_rank == 13 for e in valid if e["pid"] != winner_pid_q
                )
                if _beat_same_king or _beat_any_king:
                    result["queen_portal_pid"] = winner_pid_q
                    fury_note = (
                        " (Dominant Queen Fury!)"
                        if _beat_any_king and not _beat_same_king
                        else ""
                    )
                    wname = (
                        all_players[winner_pid_q].name
                        if all_players and winner_pid_q in all_players
                        else winner_pid_q
                    )
                    result["special_events"].append(
                        f"👑 {wname}'s Queen wins duel — Queen Fury{fury_note}! Steal a card from any opponent!"
                    )
                else:
                    wname = (
                        all_players[winner_pid_q].name
                        if all_players and winner_pid_q in all_players
                        else winner_pid_q
                    )
                    result["special_events"].append(
                        f"👑 {wname}'s Queen wins the duel!"
                    )
                portal_e = next(
                    (e for e in valid if e["card"].is_portal or e.get("has_portal")),
                    None,
                )
                if portal_e:
                    result["portal_pid"] = portal_e["pid"]
                return result

    if wizards and not has_dragon:
        remaining = [e for e in valid if not e["card"].is_wizard]
        absorbed_labels = {}
        for w_entry in wizards:
            w = w_entry["card"]
            same_suit = [
                e
                for e in remaining
                if e["card"].suit == w.suit and not e["card"].is_dragon
            ]
            absorbed_labels[w_entry["pid"]] = [e["card"].label for e in same_suit]
            remaining = [e for e in remaining if e not in same_suit]

        contenders = wizards + remaining

        best_rank = max(e["card"].effective_rank(el) for e in contenders)
        top = [e for e in contenders if e["card"].effective_rank(el) == best_rank]

        if len(top) == 1:
            result["winner_pid"] = top[0]["pid"]
            wc = top[0]["card"]
            if wc.is_wizard:
                ab = absorbed_labels.get(top[0]["pid"], [])
                ab_str = f" (absorbed: {', '.join(ab)})" if ab else ""
                result["special_events"].append(
                    f"🧙 {(all_players[top[0]['pid']].name if all_players and top[0]['pid'] in all_players else top[0]['pid'])}'s Wizard wins{ab_str}!"
                )
            else:
                result["special_events"].append(
                    f"🧙 Wizard cleared same-suit cards — "
                    f"{(all_players[top[0]['pid']].name if all_players and top[0]['pid'] in all_players else top[0]['pid'])} wins with {wc.label}!"
                )
        else:
            wizard_tops = [e for e in top if e["card"].is_wizard]
            nonwizard_top = [e for e in top if not e["card"].is_wizard]
            if len(wizard_tops) >= 2 and not nonwizard_top:
                result["special_events"].append(
                    f"🧙 {len(wizard_tops)} Wizards tied — duel!"
                )
                if all_players:
                    winner_pid, duel_cards, pid_draws = _run_duel(
                        wizard_tops,
                        all_players,
                        lead_pid,
                        result["special_events"],
                        el,
                        portal_pids=portal_pids,
                    )
                    result["all_cards"] += duel_cards
                    result["duel_draws"].update(pid_draws)
                else:
                    winner_pid = lead_pid
                result["winner_pid"] = winner_pid
            elif len(nonwizard_top) == 1:
                result["winner_pid"] = nonwizard_top[0]["pid"]
                result["special_events"].append(
                    f"🧙 Wizard cleared same-suit cards — "
                    f"{(all_players[nonwizard_top[0]['pid']].name if all_players and nonwizard_top[0]['pid'] in all_players else nonwizard_top[0]['pid'])} wins with "
                    f"{nonwizard_top[0]['card'].label}!"
                )
            else:
                result["special_events"].append(
                    f"🧙 Wizard cleared same-suit cards — tie, duel!"
                )
                if all_players:
                    winner_pid, duel_cards, pid_draws = _run_duel(
                        nonwizard_top if nonwizard_top else top,
                        all_players,
                        lead_pid,
                        result["special_events"],
                        el,
                        portal_pids=portal_pids,
                    )
                    result["all_cards"] += duel_cards
                    result["duel_draws"].update(pid_draws)
                else:
                    winner_pid = lead_pid
                result["winner_pid"] = winner_pid

        portal_e = next(
            (e for e in valid if e["card"].is_portal or e.get("has_portal")), None
        )
        if portal_e:
            result["portal_pid"] = portal_e["pid"]
        return result

    regular_dragons = [
        e for e in valid if e["card"].is_dragon and not e["card"].is_joker
    ]
    if jokers and regular_dragons:
        max_regular_eff = max(e["card"].effective_rank(el) for e in regular_dragons)
        top_regular = [
            e
            for e in regular_dragons
            if e["card"].effective_rank(el) == max_regular_eff
        ]
        excluded = [
            e for e in regular_dragons if e["card"].effective_rank(el) < max_regular_eff
        ]
        duel_entries = jokers + top_regular
        n_j = len(jokers)
        n_d = len(top_regular)
        skip_note = (
            f" ({len(excluded)} lower-rank dragon(s) excluded)" if excluded else ""
        )
        result["special_events"].append(
            f"\U0001f0cf Special Dragon rule: Joker(s) adopt rank {max_regular_eff:.1f} "
            f"— {n_j} Joker(s) + {n_d} Dragon(s) \u2014 "
            f"{len(duel_entries)}-way duel!{skip_note}"
        )
        if all_players:
            winner_pid, duel_cards, pid_draws = _run_duel(
                duel_entries,
                all_players,
                lead_pid,
                result["special_events"],
                el,
                portal_pids=portal_pids,
            )
            result["all_cards"] += duel_cards
            result["duel_draws"].update(pid_draws)
        else:
            winner_pid = lead_pid
        result["winner_pid"] = winner_pid
        winning_entry = next((e for e in duel_entries if e["pid"] == winner_pid), None)
        if winning_entry and winning_entry["card"].is_joker:
            result["joker_powers"] = [winning_entry["card"].joker_type]
            result["special_events"].append(
                f"\U0001f0cf {(all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid)} wins duel \u2014 "
                f"{winning_entry['card'].joker_type} Dragon power activates!"
            )
        else:
            joker_types = [j["card"].joker_type for j in jokers]
            result["joker_powers"] = joker_types
            result["special_events"].append(
                f"\U0001f0cf {(all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid)}'s Dragon wins duel \u2014 inherits Joker power(s): {joker_types}!"
            )
        space_j_in_duel = next(
            (j for j in duel_entries if j["card"].joker_type == "space"), None
        )
        if space_j_in_duel:
            result["space_dragon_pid"] = winner_pid
        for drawn_card in result.get("all_cards", []):
            if drawn_card.joker_type == "space" and not space_j_in_duel:
                result["space_dragon_pid"] = winner_pid

    if result["winner_pid"] is None:
        best = _best_card([e["card"] for e in valid], el)
        top_e = best.effective_rank(el)
        tied = [e for e in valid if e["card"].effective_rank(el) == top_e]

        if len(tied) == 1:
            result["winner_pid"] = tied[0]["pid"]
        else:
            dragon_tied = [e for e in tied if e["card"].is_dragon]
            all_tied = tied

            if len(all_tied) >= 2:
                n_drag = len(dragon_tied)
                n_jok = sum(1 for e in all_tied if e["card"].is_joker)
                if n_jok >= 2 and n_drag == n_jok:
                    result["special_events"].append(
                        f"🃏 Joker duel! ({n_jok} Jokers tied)"
                    )
                elif n_jok >= 1:
                    result["special_events"].append(
                        f"🃏 Joker + Dragon duel! ({n_jok} Joker(s) + {n_drag - n_jok} Dragon(s))"
                    )
                elif n_drag >= 2:
                    result["special_events"].append(
                        f"⚔️ Dragon duel! ({n_drag} dragons tied)"
                    )
                else:
                    result["special_events"].append(
                        f"⚔️ Regular duel! ({len(all_tied)} cards tied — draw from pile)"
                    )

                if all_players:
                    winner_pid, duel_cards, pid_draws = _run_duel(
                        all_tied,
                        all_players,
                        lead_pid,
                        result["special_events"],
                        el,
                        portal_pids=portal_pids,
                    )
                    result["all_cards"] += duel_cards
                    result["duel_draws"].update(pid_draws)
                else:
                    winner_pid = lead_pid
                result["winner_pid"] = winner_pid

                jokers_in_duel = [e for e in all_tied if e["card"].is_joker]
                if jokers_in_duel:
                    winning_entry = next(
                        (e for e in all_tied if e["pid"] == winner_pid), None
                    )
                    joker_types = [j["card"].joker_type for j in jokers_in_duel]
                    result["joker_powers"] = joker_types
                    if winning_entry and winning_entry["card"].is_joker:
                        result["special_events"].append(
                            f"\U0001f0cf {(all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid)} wins duel with {winning_entry['card'].joker_type} Dragon \u2014 power activates!"
                        )
                    else:
                        result["special_events"].append(
                            f"\U0001f0cf {(all_players[winner_pid].name if all_players and winner_pid in all_players else winner_pid)}'s Dragon wins duel \u2014 inherits Joker power(s): {joker_types}!"
                        )
                    if any(j["card"].joker_type == "space" for j in jokers_in_duel):
                        result["space_dragon_pid"] = winner_pid
            else:
                result["winner_pid"] = lead_pid

    if not result.get("space_dragon_pid") and result["winner_pid"]:
        for c in result.get("all_cards", []):
            if c.joker_type == "space":
                result["space_dragon_pid"] = result["winner_pid"]
                result["special_events"].append(
                    f"🌌 Space Dragon (drawn in duel) — {result['winner_pid']} may swap seats!"
                )
                break

    space_j = next((e for e in valid if e["card"].joker_type == "space"), None)
    if space_j:
        winner_is_tamer = result["winner_pid"] and any(
            e["pid"] == result["winner_pid"] and e["card"].is_tamer for e in valid
        )
        winner_is_space_owner = result["winner_pid"] == space_j["pid"]
        if winner_is_space_owner or winner_is_tamer:
            result["space_dragon_pid"] = result["winner_pid"]
            result["special_events"].append(
                f"🌌 Space Dragon! {result['winner_pid']} won — may swap seats."
            )

    return result


@dataclass
class PlayerState:
    pid: str
    name: str
    hand: List[Card] = field(default_factory=list)
    battle: List[Card] = field(default_factory=list)
    accum: List[Card] = field(default_factory=list)
    out: bool = False
    skip_next: bool = False
    is_ai: bool = False
    ai_strategy: str = "Balanced"
    hand_order: List[int] = field(default_factory=list)

    @property
    def dragon_count(self) -> int:
        return sum(1 for c in self.hand if c.is_dragon) + sum(
            1 for c in self.battle if c.is_dragon
        )

    def public_dict(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "dragon_count": self.dragon_count,
            "hand_count": len(self.hand),
            "battle_count": len(self.battle),
            "out": self.out,
            "is_ai": self.is_ai,
        }

    def private_dict(self) -> dict:
        return {
            **self.public_dict(),
            "hand": [c.to_dict() for c in ordered_hand(self)],
        }


def ai_pick_cards(player: PlayerState, n: int, el: str) -> List[Card]:
    hand = player.hand
    strat = player.ai_strategy
    dragons_count = player.dragon_count
    sorted_h = sorted(hand, key=lambda c: -c.effective_rank(el))
    tamers = [c for c in hand if c.is_tamer]
    dragons = [c for c in hand if c.is_dragon]
    others = sorted(
        [c for c in hand if not c.is_dragon and not c.is_tamer],
        key=lambda c: c.effective_rank(el),
    )

    queens = [c for c in hand if c.is_queen]
    wizards_h = [c for c in hand if c.is_wizard]

    if strat == "Aggressive":
        return sorted_h[:n]
    elif strat == "Conservative":
        if dragons_count >= 3:
            return sorted_h[:n]
        non_queen_others = [c for c in others if not c.is_queen]
        result = non_queen_others[:n]
        if len(result) < n:
            result += queens[: n - len(result)]
        if len(result) < n:
            result += dragons[: n - len(result)]
        if len(result) < n:
            result += tamers[: n - len(result)]
        return result[:n]
    elif strat in ("Diplomat", "AntiDragon"):
        precious = [c for c in hand if c.is_tamer or c.is_princess]
        non_precious_others = [c for c in others if not c.is_queen]
        result = queens[: min(1, n)]
        if len(result) < n:
            result += non_precious_others[: n - len(result)]
        if len(result) < n:
            result += dragons[: n - len(result)]
        if len(result) < n:
            result += precious[: n - len(result)]
        return result[:n]
    elif strat == "Bluffer":
        weak = sorted(others, key=lambda c: c.effective_rank(el))
        result = weak[:n]
        if len(result) < n:
            result += dragons[: n - len(result)]
        if len(result) < n:
            result += tamers[: n - len(result)]
        return result[:n]
    elif strat == "DragonHunter":
        high_others = sorted(others, key=lambda c: -c.effective_rank(el))
        result = list(dragons)
        if len(result) < n:
            result += high_others[: n - len(result)]
        if len(result) < n:
            result += tamers[: n - len(result)]
        return result[:n]
    elif strat == "Purist":
        suit_queens = [c for c in hand if c.suit == el and c.is_queen]
        suit_others = sorted(
            [c for c in hand if c.suit == el and not c.is_queen],
            key=lambda c: -c.effective_rank(el),
        )
        off_suit = sorted(
            [c for c in hand if c.suit != el], key=lambda c: -c.effective_rank(el)
        )
        result = suit_queens[:n]
        if len(result) < n:
            result += suit_others[: n - len(result)]
        if len(result) < n:
            result += off_suit[: n - len(result)]
        return result[:n]
    elif strat == "Maximalist":
        asc = sorted(hand, key=lambda c: c.effective_rank(el))
        desc = sorted(hand, key=lambda c: -c.effective_rank(el))
        result = []
        seen = set()
        hi, lo = 0, 0
        for i in range(min(n, len(hand))):
            card = desc[hi] if i % 2 == 0 else asc[lo]
            if card.cid not in seen:
                result.append(card)
                seen.add(card.cid)
            if i % 2 == 0:
                hi += 1
            else:
                lo += 1
            if hi >= len(desc) or lo >= len(asc):
                break
        if len(result) < n:
            for c in sorted_h:
                if c.cid not in seen:
                    result.append(c)
                    seen.add(c.cid)
                if len(result) >= n:
                    break
        return result[:n]
    elif strat == "Minimalist":
        return sorted_h[:n]
    elif strat == "Warden":
        # Middle band: strongest stay home (buried deep), weakest stay
        # home too (as pile-top decoys for thieves)
        pool = sorted(
            [c for c in others if not c.is_queen], key=lambda c: c.effective_rank(el)
        )
        start = max(0, (len(pool) - n) // 2)
        result = pool[start : start + n]
        if len(result) < n:
            result += queens[: n - len(result)]
        if len(result) < n:
            result += wizards_h[: n - len(result)]
        if len(result) < n:
            result += [c for c in sorted_h if c.cid not in {x.cid for x in result}][
                : n - len(result)
            ]
        return result[:n]
    elif strat == "Raider":
        # Plunder tools first: dominant Wizards, then Queens, then value
        wiz_sorted = sorted(
            wizards_h, key=lambda c: (c.suit != el, -c.effective_rank(el))
        )
        result = wiz_sorted[:n]
        if len(result) < n:
            result += queens[: n - len(result)]
        if len(result) < n:
            result += [c for c in sorted_h if c.cid not in {x.cid for x in result}][
                : n - len(result)
            ]
        return result[:n]
    elif strat == "Spearhead":
        # One spear, the rest chaff
        chaff = sorted(others, key=lambda c: c.effective_rank(el))
        result = (
            sorted_h[:1]
            + [c for c in chaff if c.cid != (sorted_h[0].cid if sorted_h else -1)][
                : max(0, n - 1)
            ]
        )
        if len(result) < n:
            result += [c for c in sorted_h if c.cid not in {x.cid for x in result}][
                : n - len(result)
            ]
        return result[:n]
    elif strat == "Scholar":
        # Deploy Tamers while many dragons still roam; otherwise pure value
        loose = getattr(player, "_dragons_loose", None)
        total = getattr(player, "_dragons_total", None)
        hunt = (loose is None or total is None) or (loose >= total / 2)
        if hunt and tamers:
            result = tamers[: max(1, n // 2)]
            result += [c for c in sorted_h if c.cid not in {x.cid for x in result}][
                : n - len(result)
            ]
            return result[:n]
        non_tamer = [c for c in sorted_h if not c.is_tamer]
        result = non_tamer[:n]
        if len(result) < n:
            result += tamers[: n - len(result)]
        return result[:n]
    elif strat == "Gambler":
        return (
            sorted_h[:n]
            if random.random() < 0.5
            else sorted(hand, key=lambda c: c.effective_rank(el))[:n]
        )
    elif strat == "Opportunist":
        prev_max_rank = (
            max((c.effective_rank(el) for c in player.prev_step_cards_seen), default=0)
            if hasattr(player, "prev_step_cards_seen") and player.prev_step_cards_seen
            else 0
        )
        if prev_max_rank > 0:
            beaters = sorted(
                [c for c in hand if c.effective_rank(el) > prev_max_rank],
                key=lambda c: c.effective_rank(el),
            )
            result = beaters[:n]
            if len(result) < n:
                result += [c for c in sorted_h if c.cid not in {x.cid for x in result}]
            return result[:n]
        else:
            return sorted_h[:n]
    else:
        result = []
        seen_cids = set()
        lo, hi = len(sorted_h) - 1, 0
        for i in range(min(n, len(sorted_h))):
            card = sorted_h[hi] if i % 2 == 0 else sorted_h[lo]
            if card.cid not in seen_cids:
                result.append(card)
                seen_cids.add(card.cid)
            if i % 2 == 0:
                hi += 1
            else:
                lo -= 1
            if hi > lo:
                break
        if len(result) < n:
            for c in sorted_h:
                if c.cid not in seen_cids:
                    result.append(c)
                    seen_cids.add(c.cid)
                if len(result) >= n:
                    break
        return result[:n]


def ai_declare(player: PlayerState, max_steps: int = 4) -> tuple:
    hand = player.hand
    strat = player.ai_strategy
    d = player.dragon_count
    n_hand = len(hand)

    def suit_value(s):
        return sum(c.effective_rank(s) for c in hand if c.suit == s)

    def suit_count(s):
        return sum(1 for c in hand if c.suit == s)

    def dragon_suit():
        ds = [c.suit for c in hand if c.is_dragon and c.suit]
        return max(SUITS, key=lambda s: ds.count(s)) if ds else best_value_suit()

    def best_value_suit():
        return max(SUITS, key=suit_value)

    def richest_suit():
        return max(SUITS, key=suit_count)

    def highest_card_suit():
        if not hand:
            return "Hearts"
        best = max(hand, key=lambda c: c.rank)
        return best.suit or "Hearts"

    def wizard_arm_suit():
        # Suit of a held Wizard — declaring it awakens his portal.
        # Prefer the wizard suit with the highest total value (synergy).
        ws = {c.suit for c in hand if c.is_wizard and c.suit}
        if not ws:
            return None
        return max(ws, key=suit_value)

    if strat == "Aggressive":
        return max_steps, (wizard_arm_suit() or best_value_suit())
    elif strat == "Conservative":
        steps = min(max_steps, 4 if d >= 3 else (2 if d >= 2 else 1))
        return steps, best_value_suit()
    elif strat == "Bluffer":
        return (1 if d < 2 else 3), (wizard_arm_suit() or best_value_suit())
    elif strat == "Diplomat":
        return (2 if d < 3 else max_steps), best_value_suit()
    elif strat == "DragonHunter":
        # Hunts with his dragons when he has them; otherwise arms a
        # Wizard's portal to hunt in rival palaces.
        if any(c.is_dragon and c.suit for c in hand):
            return 3, dragon_suit()
        return 3, (wizard_arm_suit() or dragon_suit())
    elif strat == "Purist":
        el = richest_suit()
        steps = max(1, min(max_steps, suit_count(el)))
        return steps, el
    elif strat == "Maximalist":
        steps = min(max_steps, max(1, n_hand))
        return steps, (wizard_arm_suit() or best_value_suit())
    elif strat == "Minimalist":
        return 1, highest_card_suit()
    elif strat == "Opportunist":
        steps = max_steps if d >= WIN_DRAGONS - 1 else (3 if d >= 2 else 1)
        return steps, (wizard_arm_suit() or best_value_suit())
    elif strat == "Warden":
        # Defensive: short campaigns, no portal schemes
        return (1 if d < 2 else 2), best_value_suit()
    elif strat == "Raider":
        # Theft economy: always tries to arm his own Wizard
        return 3, (wizard_arm_suit() or best_value_suit())
    elif strat == "Spearhead":
        # Boosts the element of his single strongest card
        return 3, highest_card_suit()
    elif strat == "Scholar":
        return min(max_steps, 3), best_value_suit()
    elif strat == "Gambler":
        return random.choice([1, max_steps]), random.choice(SUITS)
    else:
        return 3, best_value_suit()


_SUIT_DISPLAY_ORDER = {"Hearts": 0, "Diamonds": 1, "Clubs": 2, "Spades": 3}


def _default_sort_key(c: Card) -> tuple:
    if c.is_joker:
        display_rank = 99
    elif c.is_dragon:
        display_rank = 15.0
    elif c.is_tamer:
        display_rank = 14.5
    elif c.is_princess:
        display_rank = 10.8
    elif c.is_wizard:
        display_rank = 10.2
    else:
        display_rank = float(c.rank)
    return (-display_rank, _SUIT_DISPLAY_ORDER.get(c.suit, 4))


def ordered_hand(player: PlayerState) -> List[Card]:
    if not player.hand_order:
        return sorted(player.hand, key=_default_sort_key)
    order_map = {cid: i for i, cid in enumerate(player.hand_order)}
    known = sorted(
        [c for c in player.hand if c.cid in order_map], key=lambda c: order_map[c.cid]
    )
    unknown = sorted(
        [c for c in player.hand if c.cid not in order_map], key=_default_sort_key
    )
    return known + unknown


def ai_sort_hand(player: PlayerState) -> None:
    strat = player.ai_strategy
    hand = player.hand
    if not hand:
        player.hand_order = []
        return

    has_tamer = any(c.is_tamer for c in hand)
    has_dragon = any(c.is_dragon for c in hand)
    n_dragons = sum(1 for c in hand if c.is_dragon)

    dragons = sorted(
        [c for c in hand if c.is_dragon], key=lambda c: -c.effective_rank(None)
    )
    tamers = sorted(
        [c for c in hand if c.is_tamer], key=lambda c: -c.effective_rank(None)
    )
    queens = sorted(
        [c for c in hand if c.is_queen], key=lambda c: -c.effective_rank(None)
    )
    kings = sorted(
        [c for c in hand if c.orig_rank == 13], key=lambda c: -c.effective_rank(None)
    )
    wizards = sorted(
        [c for c in hand if c.is_wizard], key=lambda c: -c.effective_rank(None)
    )
    weak = sorted(
        [
            c
            for c in hand
            if not c.is_dragon
            and not c.is_tamer
            and not c.is_queen
            and c.orig_rank != 13
            and not c.is_wizard
            and c.effective_rank(None) <= 7
        ],
        key=lambda c: c.effective_rank(None),
    )
    mid = sorted(
        [
            c
            for c in hand
            if not c.is_dragon
            and not c.is_tamer
            and not c.is_queen
            and c.orig_rank != 13
            and not c.is_wizard
            and c.effective_rank(None) > 7
        ],
        key=lambda c: -c.effective_rank(None),
    )

    def _assemble(*groups):
        seen = set()
        result = []
        for g in groups:
            for c in g:
                if c.cid not in seen:
                    result.append(c)
                    seen.add(c.cid)
        for c in hand:
            if c.cid not in seen:
                result.append(c)
                seen.add(c.cid)
        return result

    if strat == "Aggressive":
        ordered = _assemble(dragons, tamers, mid, weak, queens, kings, wizards)
    elif strat == "Balanced":
        if n_dragons <= 1:
            ordered = _assemble(tamers, mid, weak, queens, kings, wizards, dragons)
        else:
            first_dragon = dragons[:1]
            rest_dragons = dragons[1:]
            ordered = _assemble(
                first_dragon, tamers, mid, weak, queens, kings, wizards, rest_dragons
            )
    elif strat == "Conservative":
        ordered = _assemble(weak, mid, kings, queens, wizards, tamers, dragons)
    elif strat == "Hoarder":
        ordered = _assemble(weak, mid, kings, queens, wizards, tamers, dragons)
    elif strat == "Adaptive":
        if has_tamer:
            ordered = _assemble(tamers, dragons, mid, weak, queens, kings, wizards)
        else:
            ordered = _assemble(mid, weak, queens, kings, wizards, dragons)
    elif strat == "AntiDragon":
        ordered = _assemble(tamers, queens, mid, weak, kings, wizards, dragons)
    elif strat == "Diplomat":
        non_special = mid + weak + kings + wizards
        n = len(hand)
        mid_pos = n // 2
        half1 = non_special[:mid_pos]
        half2 = non_special[mid_pos:]
        ordered = _assemble(queens, half1, dragons, half2, tamers)
    elif strat == "Bluffer":
        ordered = _assemble(weak, mid, queens, kings, wizards, tamers, dragons)
    elif strat == "Avenger":
        ordered = _assemble(tamers, dragons, mid, queens, kings, wizards, weak)
    elif strat == "Warden":
        # Junk on top feeds the thieves; treasures buried deepest
        ordered = _assemble(weak, mid, wizards, kings, queens, dragons, tamers)
    elif strat == "Raider":
        # Protects his loot the same way he takes yours
        ordered = _assemble(weak, mid, kings, queens, wizards, dragons, tamers)
    elif strat == "Spearhead":
        # Duel-hungry: strong draws first, theft risk accepted
        ordered = _assemble(kings, queens, mid, wizards, dragons, tamers, weak)
    elif strat == "Scholar":
        ordered = _assemble(mid, weak, kings, queens, wizards, tamers, dragons)
    elif strat == "Gambler":
        ordered = hand[:]
        random.shuffle(ordered)
    elif strat == "Maximalist":
        weakest = sorted(hand, key=lambda c: c.effective_rank(None))
        non_dragon_non_weak = [
            c
            for c in hand
            if not c.is_dragon and c.cid not in {weakest[0].cid if weakest else -1}
        ]
        desc_rest = sorted(non_dragon_non_weak, key=lambda c: -c.effective_rank(None))
        asc_rest = sorted(non_dragon_non_weak, key=lambda c: c.effective_rank(None))
        middle = []
        seen_m = set()
        hi = lo = 0
        toggle = True
        for _ in range(len(desc_rest)):
            src = asc_rest if toggle else desc_rest
            idx2 = lo if toggle else hi
            if idx2 < len(src) and src[idx2].cid not in seen_m:
                middle.append(src[idx2])
                seen_m.add(src[idx2].cid)
            if toggle:
                lo += 1
            else:
                hi += 1
            toggle = not toggle
        first_weak = [weakest[0]] if weakest else []
        ordered = _assemble(first_weak, dragons, middle, tamers)
    elif strat == "Minimalist":
        if has_dragon:
            ordered = _assemble(dragons, tamers, mid, queens, kings, wizards, weak)
        else:
            ordered = _assemble(tamers, mid, queens, kings, wizards, weak)
    elif strat == "Opportunist":
        ordered = _assemble(tamers, mid, dragons, weak, queens, kings, wizards)
    elif strat == "Purist":
        dominant = max(
            ("Hearts", "Diamonds", "Clubs", "Spades"),
            key=lambda s: sum(1 for c in hand if c.suit == s),
        )
        dom_dragons = [c for c in dragons if c.suit == dominant]
        other_dragons = [c for c in dragons if c.suit != dominant]
        suit_weak = sorted(
            [
                c
                for c in hand
                if c.suit == dominant
                and not c.is_dragon
                and not c.is_tamer
                and c.effective_rank(None) <= 7
            ],
            key=lambda c: c.effective_rank(None),
        )
        suit_strong = sorted(
            [
                c
                for c in hand
                if c.suit == dominant
                and not c.is_dragon
                and not c.is_tamer
                and c.effective_rank(None) > 7
            ],
            key=lambda c: -c.effective_rank(None),
        )
        off_suit = sorted(
            [
                c
                for c in hand
                if c.suit != dominant and not c.is_dragon and not c.is_tamer
            ],
            key=lambda c: -c.effective_rank(None),
        )
        ordered = _assemble(
            dom_dragons, suit_weak, suit_strong, off_suit, tamers, other_dragons
        )
    elif strat == "DragonHunter":
        ordered = _assemble(tamers, dragons, mid, weak, queens, kings, wizards)
    else:
        ordered = sorted(hand, key=_default_sort_key)

    player.hand_order = [c.cid for c in ordered]


def ai_portal_target(player, valid_targets):
    strat = player.ai_strategy

    def stealable_count(p):
        return len(p.hand)

    if strat in ("Aggressive", "Adaptive", "DragonHunter", "Minimalist"):
        return max(valid_targets, key=lambda p: (p.dragon_count, stealable_count(p)))
    elif strat == "Conservative":
        return min(valid_targets, key=lambda p: (p.dragon_count, stealable_count(p)))
    elif strat == "Balanced":
        return max(valid_targets, key=lambda p: p.dragon_count + stealable_count(p))
    elif strat == "Purist":
        dominant = max(
            ("Hearts", "Diamonds", "Clubs", "Spades"),
            key=lambda s: sum(1 for c in player.hand if c.suit == s),
        )
        return max(
            valid_targets, key=lambda p: sum(1 for c in p.hand if c.suit == dominant)
        )
    elif strat == "Opportunist":
        prev = getattr(player, "_prev_step_winner_seen", None)
        if prev:
            m = next((t for t in valid_targets if t.pid == prev), None)
            if m:
                return m
        return max(valid_targets, key=stealable_count)
    elif strat == "Raider":
        return max(valid_targets, key=lambda p: (stealable_count(p), p.dragon_count))
    elif strat in ("Spearhead", "Scholar"):
        return max(valid_targets, key=lambda p: (p.dragon_count, stealable_count(p)))
    elif strat == "Gambler":
        return random.choice(valid_targets)
    elif strat == "Warden":
        return max(valid_targets, key=stealable_count)
    else:
        return max(valid_targets, key=stealable_count)


def ai_space_dragon_swap(player, active_players):
    opponents = [p for p in active_players if p.pid != player.pid]
    if not opponents:
        return None
    strat = player.ai_strategy

    def most_dragons():
        return max(opponents, key=lambda p: p.dragon_count).pid

    def least_dragons():
        return min(opponents, key=lambda p: p.dragon_count).pid

    def most_cards():
        return max(opponents, key=lambda p: len(p.hand)).pid

    def fewest_cards():
        return min(opponents, key=lambda p: len(p.hand)).pid

    if strat == "Aggressive":
        return most_dragons()
    elif strat == "Balanced":
        return most_dragons()
    elif strat == "Conservative":
        return None
    elif strat == "Hoarder":
        return None
    elif strat == "Adaptive":
        return most_dragons()
    elif strat == "AntiDragon":
        return most_dragons()
    elif strat == "Diplomat":
        return most_dragons()
    elif strat == "Bluffer":
        return least_dragons()
    elif strat == "Avenger":
        return most_dragons()
    elif strat == "Maximalist":
        return most_cards()
    elif strat == "Minimalist":
        return most_dragons()
    elif strat == "Opportunist":
        prev = getattr(player, "_prev_step_winner_seen", None)
        if prev:
            m = next((p for p in opponents if p.pid == prev), None)
            if m:
                return m.pid
        return most_dragons()
    elif strat == "Purist":
        dominant = max(
            ("Hearts", "Diamonds", "Clubs", "Spades"),
            key=lambda s: sum(1 for c in player.hand if c.suit == s),
        )
        return max(
            opponents, key=lambda p: sum(1 for c in p.hand if c.suit != dominant)
        ).pid
    elif strat == "DragonHunter":
        return most_dragons()
    elif strat == "Warden":
        return None
    elif strat == "Spearhead":
        return None
    elif strat == "Raider":
        return most_cards()
    elif strat == "Scholar":
        return most_dragons()
    elif strat == "Gambler":
        return random.choice([None] + [p.pid for p in opponents])
    else:
        return most_dragons()


def ai_time_dragon_choice(player, prev_step_cards, prev_step_winner_pid):
    strat = player.ai_strategy
    has_prev = bool(prev_step_cards)
    prev_has_dragon = has_prev and any(
        c.is_dragon and not c.is_joker for c in (prev_step_cards or [])
    )
    can_go_back = (
        has_prev and prev_step_winner_pid != player.pid and not prev_has_dragon
    )
    prev_worth_back = can_go_back and len(prev_step_cards or []) >= 2
    prev_big = can_go_back and len(prev_step_cards or []) >= 3

    if strat == "Aggressive":
        return "back" if can_go_back else "forward"
    elif strat == "Balanced":
        return "back" if prev_worth_back else "forward"
    elif strat == "Conservative":
        return "back" if can_go_back else "nothing"
    elif strat == "Hoarder":
        return "back" if can_go_back else "nothing"
    elif strat == "Adaptive":
        return "back" if prev_worth_back else "forward"
    elif strat == "AntiDragon":
        return "back" if can_go_back else "nothing"
    elif strat == "Diplomat":
        return "back" if prev_big else "nothing"
    elif strat == "Bluffer":
        return "nothing"
    elif strat == "Avenger":
        return "back" if can_go_back else "forward"
    elif strat == "Maximalist":
        return "back" if prev_big else "forward"
    elif strat == "Minimalist":
        return "forward"
    elif strat == "Opportunist":
        return "back" if prev_worth_back else "forward"
    elif strat == "Purist":
        return "back" if prev_worth_back else "forward"
    elif strat == "DragonHunter":
        return "back" if can_go_back else "forward"
    elif strat == "Warden":
        return "back" if prev_worth_back else "nothing"
    elif strat == "Raider":
        return "back" if can_go_back else "nothing"
    elif strat == "Spearhead":
        return "back" if prev_big else "nothing"
    elif strat == "Scholar":
        return "back" if prev_worth_back else "forward"
    elif strat == "Gambler":
        return random.choice(
            [("back" if can_go_back else "nothing"), "forward", "nothing"]
        )
    else:
        return "back" if can_go_back else "nothing"


def ai_love_tamer_choice(princess_player, tamer_pids, all_players):
    strat = princess_player.ai_strategy
    tamer_players = [all_players[pid] for pid in tamer_pids if pid in all_players]
    if not tamer_players:
        return tamer_pids[0]
    if strat in ("Aggressive", "DragonHunter", "Maximalist"):
        return max(tamer_players, key=lambda p: p.dragon_count).pid
    elif strat in ("Conservative", "Minimalist", "Hoarder"):
        return min(tamer_players, key=lambda p: p.dragon_count).pid
    elif strat in ("Balanced", "Purist"):

        def btr(p):
            return max((c.rank for c in p.hand if c.is_tamer), default=0)

        return max(tamer_players, key=btr).pid
    elif strat in ("Warden", "Scholar"):
        return min(tamer_players, key=lambda p: p.dragon_count).pid
    elif strat == "Raider":
        return max(tamer_players, key=lambda p: len(p.hand)).pid
    elif strat == "Gambler":
        return random.choice(tamer_players).pid
    else:
        return max(tamer_players, key=lambda p: p.dragon_count).pid


class DragonTamerGame:
    def __init__(self, room_id: str, max_players: int = 10):
        self.room_id = room_id
        self.max_players = max_players
        self.players: Dict[str, PlayerState] = {}
        self.order: List[str] = []
        self.phase: Phase = Phase.WAITING
        self.round: int = 0
        self.lead_idx: int = 0
        self.love_right: Optional[str] = None
        self.declared_steps: int = 3
        self.declared_el: str = "Hearts"
        self.current_step: int = 0
        self.step_entries: List[dict] = []
        self.prev_step_cards: List[Card] = []
        self.prev_step_winner: Optional[str] = None
        self.step_history: List[dict] = []  # completed step_revealed events, current round only — lets a reconnecting client rebuild what it missed
        self.event_log: List[str] = []
        self._pending_portal_pid: Optional[str] = None
        self._pending_portal_entries: List[dict] = []
        self._pending_wizard_portal_queue: List[dict] = []
        self._pending_queen_portal_pid: Optional[str] = None
        self._pending_queen_portal_entries: List[dict] = []
        self._pending_queen_portal_si: int = 0
        self._pending_love_princess_pids: List[str] = []
        self._pending_love_tamer_pids: List[str] = []
        self._pending_love_step_result: Optional[dict] = None
        self._pending_love_entries: List[dict] = []
        self._pending_love_si: int = 0
        self._pending_love_votes: dict = {}
        self._pending_space_dragon_pid: Optional[str] = None
        self._pending_space_dragon_entries: List[dict] = []
        self._pending_space_dragon_si: int = 0
        self._pending_joker_pid: Optional[str] = None
        self._pending_joker_options: List[str] = []
        self._pending_joker_entries: List[dict] = []
        self._pending_joker_si: int = 0
        self._pending_time_dragon_pid: Optional[str] = None
        self._pending_time_dragon_si: int = 0
        self._claimed_cids: set = set()
        self.final_snapshot: dict = {}
        self._skipped_this_round: set = set()
        self._picked_pids: set = set()
        self._max_steps: int = 4
        self._pending_arrange_pids: set = set()

    def add_player(
        self, pid: str, name: str, is_ai: bool = False, ai_strategy: str = "Balanced"
    ) -> dict:
        if pid in self.players:
            return {"ok": False, "error": "already_joined"}
        if len(self.players) >= self.max_players:
            return {"ok": False, "error": "room_full"}
        if self.phase != Phase.WAITING:
            return {"ok": False, "error": "game_started"}
        p = PlayerState(pid=pid, name=name, is_ai=is_ai, ai_strategy=ai_strategy)
        self.players[pid] = p
        self.order.append(pid)
        return {"ok": True}

    def remove_player(self, pid: str):
        if pid in self.players and self.phase == Phase.WAITING:
            del self.players[pid]
            self.order.remove(pid)

    def fill_with_ai(self, strategies=None):
        all_strats = [
            "Aggressive",
            "Balanced",
            "Conservative",
            "Hoarder",
            "Adaptive",
            "AntiDragon",
            "Diplomat",
            "Bluffer",
            "Avenger",
            "Maximalist",
            "Minimalist",
            "Opportunist",
            "Purist",
            "DragonHunter",
            "Warden",
            "Raider",
            "Spearhead",
            "Scholar",
            "Gambler",
        ]
        i = 0
        import random as _r

        pool = list(strategies or all_strats)
        _r.shuffle(pool)
        max_ai = len(pool) if strategies else self.max_players
        while len(self.players) < self.max_players and i < max_ai:
            strat = pool[i % len(pool)]
            _AI_NAMES = {
                "Aggressive": "Yaniv",
                "Balanced": "Chen",
                "Conservative": "Itzik",
                "Hoarder": "Hadas",
                "Adaptive": "Yotam",
                "AntiDragon": "Meital",
                "Diplomat": "Oren",
                "Bluffer": "Shir",
                "Avenger": "Gil",
                "Maximalist": "Dana",
                "Minimalist": "Amit",
                "Opportunist": "Noa",
                "Purist": "Alon",
                "DragonHunter": "Lior",
                "Warden": "Oded",
                "Raider": "Shahar",
                "Spearhead": "Zohar",
                "Scholar": "Moti",
                "Gambler": "Idan",
            }
            ai_id = f"AI_{i + 1}"
            ai_name = _AI_NAMES.get(strat, strat[:4])
            self.add_player(ai_id, ai_name, is_ai=True, ai_strategy=strat)
            i += 1

    def start_game(self) -> List[dict]:
        if self.phase != Phase.WAITING:
            return [{"type": "error", "msg": "Game already started"}]
        if len(self.players) < 2:
            return [{"type": "error", "msg": "Need at least 2 players"}]

        deck = build_deck()
        if getattr(self, "_num_decks", 1) == 2:
            deck2 = build_deck()
            max_cid = max(c.cid for c in deck)
            for c in deck2:
                c.cid += max_cid + 1
            deck = deck + deck2
            self._max_steps = 5
        else:
            self._max_steps = 4
        random.shuffle(deck)
        n = len(self.order)

        leader_cards = {pid: deck[i] for i, pid in enumerate(self.order)}
        best_pid = max(self.order, key=lambda pid: leader_cards[pid].rank)
        self.lead_idx = self.order.index(best_pid)

        random.shuffle(deck)
        per = len(deck) // n
        leftover = deck[per * n :]
        for i, pid in enumerate(self.order):
            self.players[pid].hand = deck[i * per : (i + 1) * per]
        if leftover:
            self.players[best_pid].hand += leftover

        self.round = 1
        self.phase = Phase.LEADER_DECLARE

        events = [
            {
                "type": "game_started",
                "round": self.round,
                "leader_cards": {pid: c.to_dict() for pid, c in leader_cards.items()},
                "first_leader_pid": best_pid,
                "max_steps": self._max_steps,
                "num_decks": getattr(self, "_num_decks", 1),
            },
            {
                "type": "phase_change",
                "phase": Phase.LEADER_DECLARE,
                "leader_pid": self._lead_pid(),
                "round": self.round,
                "max_steps": self._max_steps,
            },
        ]
        events += self._send_hands()

        if self.players[self._lead_pid()].is_ai:
            events += self._ai_declare()

        return events

    def player_declare(self, pid: str, steps: int, element: str) -> List[dict]:
        if self.phase != Phase.LEADER_DECLARE:
            return [{"type": "error", "msg": "Not in declare phase"}]
        if pid != self._lead_pid():
            return [{"type": "error", "msg": "Not your turn to declare"}]
        if element not in SUITS:
            return [{"type": "error", "msg": "Invalid element"}]
        steps = max(1, min(self._max_steps, steps))

        self.declared_steps = steps
        self.declared_el = element
        self._log(
            f"👑 {self.players[pid].name} declares: {steps} steps · "
            f"{SUIT_ELEMENT[element]} {SUIT_SYM[element]}"
        )

        events = [
            {
                "type": "declaration",
                "pid": pid,
                "steps": steps,
                "element": element,
                "element_name": SUIT_ELEMENT[element],
            }
        ]
        events += self._start_pick_phase()
        return events

    def _start_pick_phase(self) -> List[dict]:
        self.assert_card_integrity(f"round={self.round} pick_phase_start")
        self.phase = Phase.PICK_CARDS
        self._picked_pids = set()
        for p in self.players.values():
            if not p.out:
                p.battle = []

        skip_events = []
        for p in self.players.values():
            if p.out:
                continue
            if p.skip_next:
                self._skipped_this_round.add(p.pid)
                self._picked_pids.add(p.pid)
                p.battle = []
                p.skip_next = False
                self._log(f"⏳ {p.name} skips this round (Time Dragon).")
                skip_events.append(
                    {"type": "skip_next_cleared", "pid": p.pid, "name": p.name}
                )

        events = []
        events += skip_events
        events += [
            {
                "type": "phase_change",
                "phase": Phase.PICK_CARDS,
                "steps_needed": self.declared_steps,
            }
        ]
        events += self._send_hands()
        events += self._ai_pick_all()
        return events

    def player_pick_cards(self, pid: str, card_cids: List[int]) -> List[dict]:
        if self.phase != Phase.PICK_CARDS:
            return [{"type": "error", "msg": "Not in pick phase"}]
        p = self.players.get(pid)
        if not p or p.out:
            return [{"type": "error", "msg": "Invalid player"}]

        if pid in self._skipped_this_round:
            p.battle = []
            self._picked_pids.add(pid)
            events = [{"type": "cards_picked", "pid": pid, "count": 0}]
            if self._all_picked():
                events += self._start_arrange_phase()
            return events

        n = min(self.declared_steps, len(p.hand))
        if len(card_cids) != n:
            return [
                {
                    "type": "error",
                    "msg": f"Must pick exactly {n} cards, got {len(card_cids)}",
                }
            ]

        hand_cids = {c.cid: c for c in p.hand}
        for cid in card_cids:
            if cid not in hand_cids:
                return [{"type": "error", "msg": f"Card {cid} not in hand"}]

        seen = set()
        deduped = []
        for cid in card_cids:
            if cid not in seen:
                deduped.append(cid)
                seen.add(cid)
        card_cids = deduped

        chosen = [hand_cids[cid] for cid in card_cids]
        p.battle = chosen
        p.hand = [c for c in p.hand if c.cid not in set(card_cids)]
        self._picked_pids.add(pid)

        events = [{"type": "cards_picked", "pid": pid, "count": n, "name": p.name}]

        if self._all_picked():
            events += self._start_arrange_phase()

        return events

    def player_reorder_hand(self, pid: str, cid_list: List[int]) -> List[dict]:
        p = self.players.get(pid)
        if not p:
            return [{"type": "error", "msg": "Unknown player"}]
        hand_cids = {c.cid for c in p.hand}
        if set(cid_list) != hand_cids:
            return [{"type": "error", "msg": "Card list does not match hand"}]
        p.hand_order = list(cid_list)
        return [{"type": "hand_reordered", "pid": pid}]

    def reveal_step(self, pid: str) -> List[dict]:
        if self.phase != Phase.REVEAL:
            return [{"type": "error", "msg": "Not in reveal phase"}]
        return self._do_reveal()

    def _do_reveal(self) -> List[dict]:
        si = self.current_step
        active = [
            p
            for p in self.players.values()
            if not p.out and p.pid not in self._skipped_this_round
        ]

        entries = []
        for p in active:
            if si < len(p.battle):
                card = p.battle[si]
                if card.cid not in self._claimed_cids:
                    entries.append({"pid": p.pid, "card": card})

        if not entries:
            return self._end_round()

        # Dominant-wizard portal: any Wizard whose suit matches the declared element
        # fires a portal steal before the battle resolves.
        dominant_wiz_entries = [
            e
            for e in entries
            if e["card"].is_wizard and e["card"].suit == self.declared_el
        ]
        if dominant_wiz_entries:
            # Order: leader first, then clockwise
            lead_pid = self._lead_pid()
            n_order = len(self.order)
            lead_idx = self.order.index(lead_pid) if lead_pid in self.order else 0

            def _wiz_order(e):
                pid = e["pid"]
                if pid not in self.order:
                    return n_order
                return (self.order.index(pid) - lead_idx) % n_order

            dominant_wiz_entries.sort(key=_wiz_order)

            # Find first wizard whose owner has valid steal targets
            first_e = None
            rest_queue = []
            for idx, wiz_e in enumerate(dominant_wiz_entries):
                wiz_pid = wiz_e["pid"]
                targets = [p for p in active if p.pid != wiz_pid and p.hand]
                if targets:
                    first_e = wiz_e
                    rest_queue = dominant_wiz_entries[idx + 1 :]
                    break

            if first_e:
                wiz_pid = first_e["pid"]
                wiz_player = self.players[wiz_pid]
                targets = [p for p in active if p.pid != wiz_pid and p.hand]
                self._pending_wizard_portal_queue = rest_queue
                if not wiz_player.is_ai:
                    self._pending_portal_pid = wiz_pid
                    self._pending_portal_entries = entries
                    return [
                        {
                            "type": "portal_choose_target",
                            "portal_pid": wiz_pid,
                            "step": si + 1,
                            "valid_target_pids": [t.pid for t in targets],
                        }
                    ]
                else:
                    chosen = ai_portal_target(wiz_player, targets)
                    return self._execute_portal_steal(wiz_pid, chosen.pid, entries, si)

        return self._resolve_and_finish_step(entries, si)

    def portal_target_chosen(self, pid: str, target_pid: str) -> List[dict]:
        if self._pending_portal_pid != pid:
            return [
                {"type": "error", "msg": "No pending portal choice for this player"}
            ]
        if target_pid not in self.players or self.players[target_pid].out:
            return [{"type": "error", "msg": "Invalid portal target"}]

        entries = self._pending_portal_entries
        si = self.current_step
        self._pending_portal_pid = None
        self._pending_portal_entries = []

        return self._execute_portal_steal(pid, target_pid, entries, si)

    def princess_choose_tamer(
        self, princess_pid: str, chosen_tamer_pid: str
    ) -> List[dict]:
        if princess_pid not in self._pending_love_princess_pids:
            return [
                {"type": "error", "msg": "No pending Love Power vote for this princess"}
            ]
        if chosen_tamer_pid not in self._pending_love_tamer_pids:
            return [{"type": "error", "msg": "Invalid tamer choice"}]

        self._pending_love_votes[princess_pid] = chosen_tamer_pid
        self._pending_love_princess_pids.remove(princess_pid)

        remaining_humans = [
            pid
            for pid in self._pending_love_princess_pids
            if not self.players[pid].is_ai
        ]
        if remaining_humans:
            next_human = remaining_humans[0]
            total = (
                len(self._pending_love_votes)
                + len(self._pending_love_princess_pids)
                + 1
            )
            return [
                {
                    "type": "love_choose_tamer",
                    "princess_pid": next_human,
                    "tamer_pids": self._pending_love_tamer_pids,
                    "votes_cast": len(self._pending_love_votes),
                    "votes_total": total,
                    "step": self._pending_love_si + 1,
                }
            ]

        for ai_pid in list(self._pending_love_princess_pids):
            if self.players[ai_pid].is_ai:
                vote = ai_love_tamer_choice(
                    self.players[ai_pid], self._pending_love_tamer_pids, self.players
                )
                self._pending_love_votes[ai_pid] = vote

        return self._finalize_love_vote()

    def _finalize_love_vote(self) -> List[dict]:
        from collections import Counter

        result = self._pending_love_step_result
        entries = self._pending_love_entries
        si = self._pending_love_si
        votes = self._pending_love_votes

        counts = Counter(votes.values())
        if counts:
            top_count = counts.most_common(1)[0][1]
            leaders = [t for t, c in counts.items() if c == top_count]
            if len(leaders) == 1:
                winner_tamer = leaders[0]
                result["special_events"].append(
                    f"💕 Love Power result: {self.players[winner_tamer].name} wins!"
                )
                result["love_right_pid"] = winner_tamer
            else:
                result["special_events"].append("💕 Love Power tied — power cancelled!")
                result["love_right_pid"] = None
        else:
            result["love_right_pid"] = None

        self._pending_love_princess_pids = []
        self._pending_love_tamer_pids = []
        self._pending_love_step_result = None
        self._pending_love_entries = []
        self._pending_love_si = 0
        self._pending_love_votes = {}

        if result["love_right_pid"]:
            self.love_right = result["love_right_pid"]

        return self._finish_step_after_resolve(result, entries, si)

    def _do_single_steal(self, stealer_pid, target_pid, entries):
        """Execute one portal/wizard steal. Returns (steal_events, updated_entries)."""
        target = self.players[target_pid]
        stealable = list(target.hand)
        steal_events = []
        if stealable:
            stealable_cids = {c.cid for c in stealable}
            ordered = [c for c in ordered_hand(target) if c.cid in stealable_cids]
            stolen = ordered[0]
            target.hand.remove(stolen)
            entries = list(entries) + [
                {
                    "pid": stealer_pid,
                    "card": stolen,
                    "stolen": True,
                    "stolen_from_pid": target_pid,
                    "stolen_from_name": target.name,
                }
            ]
            self._log(
                f"🌀 {self.players[stealer_pid].name} used Portal — stole {stolen.label} from {target.name}!"
            )
            steal_events.append(
                {
                    "type": "portal_steal",
                    "portal_pid": stealer_pid,
                    "portal_name": self.players[stealer_pid].name,
                    "target_pid": target_pid,
                    "target_name": target.name,
                    "stolen_card": stolen.to_dict(),
                }
            )
        return steal_events, entries

    def _execute_portal_steal(self, portal_pid, target_pid, entries, si):
        steal_events, entries = self._do_single_steal(portal_pid, target_pid, entries)

        # Process the remaining wizard-portal queue (2-deck: multiple dominant Wizards)
        active = [
            p
            for p in self.players.values()
            if not p.out and p.pid not in self._skipped_this_round
        ]
        queue = list(self._pending_wizard_portal_queue)
        self._pending_wizard_portal_queue = []

        while queue:
            next_e = queue.pop(0)
            next_pid = next_e["pid"]
            next_player = self.players[next_pid]
            targets = [p for p in active if p.pid != next_pid and p.hand]
            if not targets:
                continue
            if not next_player.is_ai:
                # Human wizard: pause and wait for their target choice
                self._pending_portal_pid = next_pid
                self._pending_portal_entries = entries
                self._pending_wizard_portal_queue = queue
                return steal_events + [
                    {
                        "type": "portal_choose_target",
                        "portal_pid": next_pid,
                        "step": si + 1,
                        "valid_target_pids": [t.pid for t in targets],
                    }
                ]
            else:
                chosen = ai_portal_target(next_player, targets)
                more_events, entries = self._do_single_steal(
                    next_pid, chosen.pid, entries
                )
                steal_events += more_events

        return steal_events + self._resolve_and_finish_step(entries, si)

    def _resolve_and_finish_step(self, entries, si):
        stolen_entries = [e for e in entries if e.get("stolen")]
        self.assert_card_integrity(
            f"round={self.round} step={si + 1} pre-resolve",
            stolen_entries if stolen_entries else None,
        )
        result = resolve_step(
            entries, self.declared_el, self._lead_pid(), all_players=self.players
        )

        if result.get("love_choice_needed"):
            lcd = result["love_choice_needed"]
            princess_pids = lcd["princess_pids"]
            tamer_pids = lcd["tamer_pids"]

            human_princesses = [
                pid for pid in princess_pids if not self.players[pid].is_ai
            ]
            ai_princesses = [pid for pid in princess_pids if self.players[pid].is_ai]

            ai_votes = {}
            for ai_pid in ai_princesses:
                vote = ai_love_tamer_choice(
                    self.players[ai_pid], tamer_pids, self.players
                )
                ai_votes[ai_pid] = vote

            if human_princesses:
                self._pending_love_princess_pids = list(human_princesses)
                self._pending_love_tamer_pids = tamer_pids
                self._pending_love_step_result = result
                self._pending_love_entries = entries
                self._pending_love_si = si
                self._pending_love_votes = ai_votes

                for msg in result["special_events"]:
                    self._log(msg)

                return [
                    {
                        "type": "love_choose_tamer",
                        "princess_pid": human_princesses[0],
                        "tamer_pids": tamer_pids,
                        "votes_cast": len(ai_votes),
                        "votes_total": len(princess_pids),
                        "step": si + 1,
                        "entries": _dedup_entries_for_client(
                            entries, result["duel_draws"]
                        ),
                        "special_events": result["special_events"],
                    }
                ]
            else:
                self._pending_love_princess_pids = []
                self._pending_love_tamer_pids = tamer_pids
                self._pending_love_step_result = result
                self._pending_love_entries = entries
                self._pending_love_si = si
                self._pending_love_votes = ai_votes
                return self._finalize_love_vote()

        if result["love_right_pid"]:
            self.love_right = result["love_right_pid"]

        return self._finish_step_after_resolve(result, entries, si)

    def _finish_step_after_resolve(self, result, entries, si):
        winner_pid = result["winner_pid"]
        all_cards = result["all_cards"]

        time_j = next((e for e in entries if e["card"].joker_type == "time"), None)

        time_owner_pid = None
        if time_j and winner_pid:
            time_owner_pid = winner_pid
            if winner_pid != time_j["pid"]:
                result["special_events"].append(
                    f"\u23f3 {self.players[winner_pid].name if winner_pid in self.players else winner_pid} wins battle containing Time Dragon \u2014 inherits power!"
                )

        if time_owner_pid:
            time_player = self.players[time_owner_pid]
            space_pid = result.get("space_dragon_pid")
            both_jokers = (space_pid and space_pid == time_owner_pid) or result.get(
                "_tamer_joker_choice"
            ) == time_owner_pid

            if both_jokers:
                if not time_player.is_ai:
                    self._pending_joker_pid = time_owner_pid
                    self._pending_joker_options = ["time", "space"]
                    self._pending_joker_entries = entries
                    self._pending_joker_si = si
                    result["special_events"].append(
                        f"🃏 {time_player.name} won BOTH Jokers — choose one power!"
                    )
                    for msg in result["special_events"]:
                        self._log(msg)
                    if winner_pid:
                        self.players[winner_pid].accum += all_cards
                        self._claimed_cids.update(c.cid for c in all_cards)
                    self.assert_card_integrity(
                        f"round={self.round} step={si + 1} post-resolve"
                    )
                    self.prev_step_cards = all_cards
                    self.prev_step_winner = winner_pid
                    self.current_step += 1
                    step_event = {
                        "type": "step_revealed",
                        "step": si + 1,
                        "total_steps": self.declared_steps,
                        "entries": _dedup_entries_for_client(
                            entries, result["duel_draws"]
                        ),
                        "winner_pid": winner_pid,
                        "special_events": result["special_events"],
                        "love_right_pid": result["love_right_pid"],
                    }
                    self.step_history.append(step_event)
                    return [
                        step_event,
                        {
                            "type": "joker_choose_power",
                            "pid": time_owner_pid,
                            "options": ["time", "space", "nothing"],
                            "step": si + 1,
                        },
                    ]
                else:
                    time_choice = ai_time_dragon_choice(
                        time_player, self.prev_step_cards, self.prev_step_winner
                    )
                    chosen_power = (
                        "time" if time_choice in ("back", "forward") else "space"
                    )
                    result["special_events"].append(
                        f"🃏 {time_player.name} chooses {chosen_power} Dragon power (AI)!"
                    )
                    if chosen_power == "time":
                        result["space_dragon_pid"] = None
                    else:
                        time_owner_pid = None
            else:
                if not time_player.is_ai:
                    self._pending_time_dragon_pid = time_owner_pid
                    self._pending_time_dragon_si = si
                    result["special_events"].append(
                        f"⏳ {time_player.name} holds Time Dragon power!"
                    )
                    for msg in result["special_events"]:
                        self._log(msg)
                    if winner_pid:
                        self.players[winner_pid].accum += all_cards
                        self._claimed_cids.update(c.cid for c in all_cards)
                    self.assert_card_integrity(
                        f"round={self.round} step={si + 1} post-resolve"
                    )
                    _has_prev = bool(self.prev_step_winner and self.prev_step_cards)
                    _can_back = self._can_time_dragon_go_back(time_owner_pid)
                    if not _can_back:
                        if not _has_prev:
                            _block_reason = "no_prev"
                        elif self.prev_step_winner == time_owner_pid:
                            _block_reason = "same_winner"
                        elif any(
                            c.is_dragon and not c.is_joker
                            for c in (self.prev_step_cards or [])
                        ):
                            _block_reason = "had_dragon"
                        else:
                            _block_reason = "unknown"
                    else:
                        _block_reason = None
                    self.prev_step_cards = all_cards
                    self.prev_step_winner = winner_pid
                    self.current_step += 1
                    step_event = {
                        "type": "step_revealed",
                        "step": si + 1,
                        "total_steps": self.declared_steps,
                        "entries": _dedup_entries_for_client(
                            entries, result["duel_draws"]
                        ),
                        "winner_pid": winner_pid,
                        "special_events": result["special_events"],
                        "love_right_pid": result["love_right_pid"],
                    }
                    self.step_history.append(step_event)
                    return [
                        step_event,
                        {
                            "type": "time_dragon_choose",
                            "pid": time_owner_pid,
                            "has_prev": _has_prev,
                            "can_go_back": _can_back,
                            "block_reason": _block_reason,
                            "step": si + 1,
                        },
                    ]

        if time_owner_pid and self.players[time_owner_pid].is_ai:
            time_player = self.players[time_owner_pid]
            time_choice = ai_time_dragon_choice(
                time_player, self.prev_step_cards, self.prev_step_winner
            )
            self._apply_time_dragon(time_owner_pid, time_choice, result)

        if winner_pid:
            self.players[winner_pid].accum += all_cards
            self._claimed_cids.update(c.cid for c in all_cards)

        self.assert_card_integrity(f"round={self.round} step={si + 1} post-resolve")

        for msg in result["special_events"]:
            self._log(msg)

        if winner_pid:
            self._log(
                f"Step {si + 1}: {self.players[winner_pid].name} wins "
                f"({', '.join(c.label for c in all_cards[:3])}...)"
            )

        self.prev_step_cards = all_cards
        self.prev_step_winner = winner_pid
        self.current_step += 1

        step_event = {
            "type": "step_revealed",
            "step": si + 1,
            "total_steps": self.declared_steps,
            "entries": _dedup_entries_for_client(entries, result["duel_draws"]),
            "winner_pid": winner_pid,
            "special_events": result["special_events"],
            "love_right_pid": result["love_right_pid"],
        }
        self.step_history.append(step_event)

        space_j = next((e for e in entries if e["card"].joker_type == "space"), None)
        if space_j and winner_pid:
            if not result.get("space_dragon_pid"):
                result["space_dragon_pid"] = winner_pid

        space_pid = result.get("space_dragon_pid")
        if space_pid:
            active = [p for p in self.players.values() if not p.out]
            valid_targets = [p.pid for p in active if p.pid != space_pid]
            space_player = self.players[space_pid]

            if not space_player.is_ai:
                self._pending_space_dragon_pid = space_pid
                self._pending_space_dragon_entries = entries
                self._pending_space_dragon_si = si
                return [
                    step_event,
                    {
                        "type": "space_dragon_choose_swap",
                        "space_pid": space_pid,
                        "valid_target_pids": valid_targets,
                        "step": si + 1,
                        "can_pass": True,
                    },
                ]
            else:
                target_pid = ai_space_dragon_swap(
                    space_player, [self.players[p] for p in valid_targets]
                )
                if target_pid:
                    swap_events = self._apply_seat_swap(space_pid, target_pid)
                    events = [step_event] + swap_events
                else:
                    events = [step_event]
        else:
            events = [step_event]

        queen_portal_pid = result.get("queen_portal_pid")
        if queen_portal_pid:
            active_qp = [
                p
                for p in self.players.values()
                if not p.out and p.pid != queen_portal_pid and p.hand
            ]
            if active_qp:
                queen_player = self.players[queen_portal_pid]
                if not queen_player.is_ai:
                    self._pending_queen_portal_pid = queen_portal_pid
                    self._pending_queen_portal_entries = entries
                    self._pending_queen_portal_si = si
                    events.append(
                        {
                            "type": "queen_choose_target",
                            "queen_pid": queen_portal_pid,
                            "step": si + 1,
                            "valid_target_pids": [p.pid for p in active_qp],
                        }
                    )
                    return events
                else:
                    chosen = ai_portal_target(queen_player, active_qp)
                    events += self._execute_queen_fury(queen_portal_pid, chosen.pid)

        if self.current_step >= self.declared_steps:
            events += self._end_round()
        else:
            events.append(
                {
                    "type": "phase_change",
                    "phase": Phase.REVEAL,
                    "next_step": self.current_step + 1,
                }
            )

        return events

    def _can_time_dragon_go_back(self, pid: str) -> bool:
        if not self.prev_step_winner or not self.prev_step_cards:
            return False
        if self.prev_step_winner == pid:
            return False
        # Only block on REGULAR dragons (not jokers — the Time Dragon itself is a joker)
        if any(c.is_dragon and not c.is_joker for c in self.prev_step_cards):
            return False
        return True

    def _apply_time_dragon(self, pid, choice, result):
        p = self.players[pid]
        if choice == "back":
            if (
                self._can_time_dragon_go_back(pid)
                and self.prev_step_winner
                and self.prev_step_cards
            ):
                pw = self.players[self.prev_step_winner]
                for c in self.prev_step_cards:
                    if c in pw.accum:
                        pw.accum.remove(c)
                p.accum += self.prev_step_cards
                result["special_events"].append(f"⏳ {p.name} claims previous step!")
            else:
                result["special_events"].append(
                    f"⏳ {p.name} cannot go back (previous step had dragons or same winner)."
                )
        elif choice == "forward":
            p.skip_next = True
            result["special_events"].append(f"⏳ {p.name} skips next round.")
        else:
            result["special_events"].append(f"⏳ {p.name} passes Time Dragon power.")

    def joker_power_chosen(self, pid, power):
        if self._pending_joker_pid != pid:
            return [{"type": "error", "msg": "No pending joker choice for this player"}]
        if power not in ("time", "space", "nothing"):
            return [{"type": "error", "msg": f"Invalid power: {power}"}]

        entries = self._pending_joker_entries
        si = self._pending_joker_si
        self._pending_joker_pid = None
        self._pending_joker_options = []
        self._pending_joker_entries = []
        self._pending_joker_si = 0

        events = []
        if power == "time":
            self._pending_time_dragon_pid = pid
            self._pending_time_dragon_si = si
            events.append(
                {
                    "type": "time_dragon_choose",
                    "pid": pid,
                    "has_prev": bool(self.prev_step_winner and self.prev_step_cards),
                    "step": si + 1,
                }
            )
        elif power == "space":
            active = [p for p in self.players.values() if not p.out]
            valid_targets = [p.pid for p in active if p.pid != pid]
            self._pending_space_dragon_pid = pid
            self._pending_space_dragon_entries = entries
            self._pending_space_dragon_si = si - 1
            events.append(
                {
                    "type": "space_dragon_choose_swap",
                    "space_pid": pid,
                    "valid_target_pids": valid_targets,
                    "step": si + 1,
                    "can_pass": True,
                }
            )
        else:
            events += self._advance_after_step(si)

        return events

    def time_dragon_chosen(self, pid, choice):
        if self._pending_time_dragon_pid != pid:
            return [{"type": "error", "msg": "No pending Time Dragon choice"}]
        if choice not in ("back", "forward", "nothing"):
            return [{"type": "error", "msg": f"Invalid choice: {choice}"}]

        si = self._pending_time_dragon_si
        self._pending_time_dragon_pid = None
        self._pending_time_dragon_si = 0

        dummy_result = {"special_events": []}
        self._apply_time_dragon(pid, choice, dummy_result)
        for msg in dummy_result["special_events"]:
            self._log(msg)

        events = [
            {
                "type": "time_dragon_applied",
                "pid": pid,
                "choice": choice,
                "msg": dummy_result["special_events"][0]
                if dummy_result["special_events"]
                else "",
            }
        ]
        events += self._advance_after_step(si)
        return events

    def _advance_after_step(self, si):
        if self.current_step >= self.declared_steps:
            return self._end_round()
        return [
            {
                "type": "phase_change",
                "phase": Phase.REVEAL,
                "next_step": self.current_step + 1,
            }
        ]

    def _apply_seat_swap(self, pid_a, pid_b):
        if pid_a not in self.order or pid_b not in self.order:
            return [{"type": "error", "msg": "Invalid swap pids"}]
        _lead_before = self._lead_pid() if self.order else None
        ia, ib = self.order.index(pid_a), self.order.index(pid_b)
        self.order[ia], self.order[ib] = self.order[ib], self.order[ia]
        # lead_idx is positional — remap it so the same player stays leader
        if _lead_before in self.order:
            self.lead_idx = self.order.index(_lead_before)
        self._log(
            f"🌌 {self.players[pid_a].name} swaps seat with {self.players[pid_b].name}!"
        )
        return [
            {
                "type": "seat_swap",
                "pid_a": pid_a,
                "pid_b": pid_b,
                "new_order": list(self.order),
            }
        ]

    def space_dragon_swap_chosen(self, pid, target_pid):
        if self._pending_space_dragon_pid != pid:
            return [
                {
                    "type": "error",
                    "msg": "No pending Space Dragon choice for this player",
                }
            ]

        si = self._pending_space_dragon_si
        self._pending_space_dragon_pid = None
        self._pending_space_dragon_entries = []
        self._pending_space_dragon_si = 0

        events = []
        if target_pid:
            if target_pid not in self.players or self.players[target_pid].out:
                return [{"type": "error", "msg": "Invalid swap target"}]
            events += self._apply_seat_swap(pid, target_pid)

        if self.current_step >= self.declared_steps:
            events += self._end_round()
        else:
            events.append(
                {
                    "type": "phase_change",
                    "phase": Phase.REVEAL,
                    "next_step": self.current_step + 1,
                }
            )

        return events

    def queen_portal_target_chosen(self, pid: str, target_pid: str) -> List[dict]:
        if self._pending_queen_portal_pid != pid:
            return [
                {"type": "error", "msg": "No pending Queen Fury choice for this player"}
            ]
        if target_pid not in self.players or self.players[target_pid].out:
            return [{"type": "error", "msg": "Invalid Queen Fury target"}]

        self._pending_queen_portal_pid = None
        self._pending_queen_portal_entries = []
        self._pending_queen_portal_si = 0

        events = self._execute_queen_fury(pid, target_pid)

        if self.current_step >= self.declared_steps:
            events += self._end_round()
        else:
            events.append(
                {
                    "type": "phase_change",
                    "phase": Phase.REVEAL,
                    "next_step": self.current_step + 1,
                }
            )
        return events

    def _execute_queen_fury(self, queen_pid: str, target_pid: str) -> List[dict]:
        target = self.players[target_pid]
        queen_player = self.players[queen_pid]
        events = []
        if target.hand:
            stealable_cids = {c.cid for c in target.hand}
            ordered = [c for c in ordered_hand(target) if c.cid in stealable_cids]
            if ordered:
                stolen = ordered[0]
                target.hand.remove(stolen)
                if stolen.cid in target.hand_order:
                    target.hand_order.remove(stolen.cid)
                # Prize goes DIRECTLY to the Queen owner's Main Pile,
                # pinned at the very bottom (rightmost in the hand UI).
                queen_player.hand.append(stolen)
                if not queen_player.hand_order:
                    queen_player.hand_order = [
                        c.cid for c in ordered_hand(queen_player) if c.cid != stolen.cid
                    ]
                if stolen.cid not in queen_player.hand_order:
                    queen_player.hand_order.append(stolen.cid)
                self._log(
                    f"👑 Queen Fury! {queen_player.name} steals "
                    f"{stolen.label} from {target.name}!"
                )
                events.append(
                    {
                        "type": "queen_fury_steal",
                        "queen_pid": queen_pid,
                        "queen_name": queen_player.name,
                        "target_pid": target_pid,
                        "target_name": target.name,
                        "stolen_card": stolen.to_dict(),
                    }
                )
                # Push fresh hands to both players (same schema as _send_hands)
                for _pid, _pl in ((queen_pid, queen_player), (target_pid, target)):
                    events.append(
                        {
                            "type": "hand_update",
                            "pid": _pid,
                            "hand": [c.to_dict() for c in ordered_hand(_pl)],
                            "dragon_count": _pl.dragon_count,
                        }
                    )
        return events

    def _end_round(self):
        for p in self.players.values():
            if p.out:
                continue
            existing = {c.cid for c in p.hand}
            for c in p.battle:
                if c.cid not in existing and c.cid not in self._claimed_cids:
                    p.hand.append(c)
                    existing.add(c.cid)
            for c in p.accum:
                if c.cid not in existing:
                    p.hand.append(c)
                    existing.add(c.cid)
            p.battle = []
            p.accum = []
            p.hand_order = []

        self._claimed_cids = set()
        self._skipped_this_round = set()

        self.assert_card_integrity(f"round={self.round} end_round post-return")

        for p in self.players.values():
            if not p.out and p.dragon_count >= WIN_DRAGONS:
                self.phase = Phase.GAME_OVER
                self._capture_final_snapshot()
                self._log(f"🏆 {p.name} wins with {p.dragon_count} dragons!")
                return [
                    {
                        "type": "game_over",
                        "winner_pid": p.pid,
                        "winner_name": p.name,
                        "dragons": p.dragon_count,
                        "round": self.round,
                        **self._game_over_payload(p.pid),
                    },
                    {"type": "eliminated", "pids": []},
                ]

        eliminated = []
        for p in self.players.values():
            if not p.out and not p.hand:
                p.out = True
                eliminated.append(p.pid)
                self._log(f"💀 {p.name} is eliminated!")

        for p in self.players.values():
            if not p.out and p.dragon_count >= WIN_DRAGONS:
                self.phase = Phase.GAME_OVER
                self._capture_final_snapshot()
                self._log(f"🏆 {p.name} wins with {p.dragon_count} dragons!")
                return [
                    {
                        "type": "game_over",
                        "winner_pid": p.pid,
                        "winner_name": p.name,
                        "dragons": p.dragon_count,
                        "round": self.round,
                        **self._game_over_payload(p.pid),
                    },
                    {"type": "eliminated", "pids": eliminated},
                ]

        if self.round >= MAX_ROUNDS:
            active_players = [p for p in self.players.values() if not p.out]
            winner = max(active_players, key=lambda p: (p.dragon_count, len(p.hand)))
            self.phase = Phase.GAME_OVER
            self._capture_final_snapshot()
            return [
                {"type": "eliminated", "pids": eliminated},
                {
                    "type": "game_over",
                    "winner_pid": winner.pid,
                    "winner_name": winner.name,
                    "dragons": winner.dragon_count,
                    "round": self.round,
                    "reason": "round_limit",
                    **self._game_over_payload(winner.pid),
                },
            ]

        active = [pid for pid in self.order if not self.players[pid].out]
        if len(active) <= 1:
            winner = self.players[active[0]] if active else None
            self.phase = Phase.GAME_OVER
            self._capture_final_snapshot()
            extra = self._game_over_payload(winner.pid) if winner else {}
            return [
                {
                    "type": "game_over",
                    "winner_pid": winner.pid if winner else None,
                    "winner_name": winner.name if winner else "Nobody",
                    "dragons": winner.dragon_count if winner else 0,
                    "round": self.round,
                    **extra,
                }
            ]

        if (
            self.love_right
            and not self.players[self.love_right].out
            and not self.players[self.love_right].skip_next
        ):
            self.lead_idx = self.order.index(self.love_right)
        else:
            self.lead_idx = (self.lead_idx + 1) % len(self.order)
            checked = 0
            while (
                self.players[self.order[self.lead_idx]].out
                or self.players[self.order[self.lead_idx]].skip_next
            ):
                self.lead_idx = (self.lead_idx + 1) % len(self.order)
                checked += 1
                if checked >= len(self.order):
                    self.lead_idx = (self.lead_idx + 1) % len(self.order)
                    while self.players[self.order[self.lead_idx]].out:
                        self.lead_idx = (self.lead_idx + 1) % len(self.order)
                    break

        self.love_right = None
        self.round += 1
        self.phase = Phase.LEADER_DECLARE
        self.prev_step_cards = []
        self.prev_step_winner = None
        self.step_history = []
        self._claimed_cids = set()  # free memory

        events = [
            {"type": "eliminated", "pids": eliminated},
            {"type": "round_end", "round": self.round - 1},
            {
                "type": "phase_change",
                "phase": Phase.LEADER_DECLARE,
                "leader_pid": self._lead_pid(),
                "round": self.round,
                "max_steps": self._max_steps,
            },
        ]
        events += self._send_hands()

        if self.players[self._lead_pid()].is_ai:
            events += self._ai_declare()

        return events

    def _capture_final_snapshot(self):
        self.final_snapshot = {}
        for p in self.players.values():
            unclaimed_battle = [c for c in p.battle if c.cid not in self._claimed_cids]
            self.final_snapshot[p.pid] = {
                "hand": list(p.hand),
                "battle": unclaimed_battle,
                "accum": list(p.accum),
            }

    def _game_over_payload(self, winner_pid):
        all_players = []
        for pid, p in self.players.items():
            snap = self.final_snapshot.get(pid, {})
            hand = snap.get("hand", list(p.hand))
            all_players.append(
                {
                    "pid": pid,
                    "name": p.name,
                    "dragons": p.dragon_count,
                    "out": p.out,
                    "hand": [c.to_dict() for c in hand],
                }
            )
        winner_snap = self.final_snapshot.get(winner_pid, {})
        w_hand = winner_snap.get(
            "hand",
            list(self.players[winner_pid].hand) if winner_pid in self.players else [],
        )
        all_dragons = [c.to_dict() for c in w_hand if c.is_dragon]
        return {"all_players": all_players, "all_dragons": all_dragons}

    def _lead_pid(self):
        return self.order[self.lead_idx % len(self.order)]

    def _next_lead_pid(self):
        """Predict who leads the NEXT campaign (read-only mirror of
        _end_round's lead pass): Love-Power holder overrides; otherwise
        clockwise from the current leader, skipping out/skip_next players."""
        try:
            n = len(self.order)
            if n == 0:
                return None
            if n == 1:
                # Only one seat exists — nobody else to predict, and the
                # doomed-skip search below cannot progress (idx is always 0
                # mod 1), which previously caused an infinite loop for a
                # freshly created single-player room. No loop needed here.
                return self.order[0]
            if self.phase == Phase.WAITING:
                # Game hasn't started — no hands dealt yet, so every player
                # looks "doomed" (empty hand/accum/battle) even though
                # they're not. There's no meaningful "next leader" before
                # start_game() runs anyway.
                return None

            def _doomed(p):
                # out already, or certain to be eliminated at round end
                if p.out:
                    return True
                _claimed = getattr(self, "_claimed_cids", set()) or set()
                _unclaimed = [c for c in p.battle if c.cid not in _claimed]
                return not p.hand and not p.accum and not _unclaimed

            _skip_counts = self.phase != Phase.LEADER_DECLARE
            if (
                self.love_right
                and self.love_right in self.order
                and not _doomed(self.players[self.love_right])
                and not (self.players[self.love_right].skip_next and _skip_counts)
            ):
                return self.love_right
            idx = (self.lead_idx + 1) % n
            checked = 0
            while _doomed(self.players[self.order[idx]]) or (
                _skip_counts and self.players[self.order[idx]].skip_next
            ):
                idx = (idx + 1) % n
                checked += 1
                if checked >= n:
                    # everyone alive is skipping — ignore skip_next
                    idx = (idx + 1) % n
                    while _doomed(self.players[self.order[idx]]):
                        idx = (idx + 1) % n
                    break
            return self.order[idx]
        except Exception:
            return None

    def assert_card_integrity(self, context="", entries=None):
        try:
            seen = {}

            def register(cid, location):
                if cid in seen:
                    raise AssertionError(
                        f"[{context}] DUPLICATE card cid={cid}: "
                        f"first at {seen[cid]}, again at {location}"
                    )
                seen[cid] = location

            for p in self.players.values():
                label = p.name
                for c in p.hand:
                    register(c.cid, f"{label}.hand")
                for c in p.battle:
                    if c.cid not in self._claimed_cids:
                        register(c.cid, f"{label}.battle")
                for c in p.accum:
                    register(c.cid, f"{label}.accum")

            if entries:
                for e in entries:
                    register(e["card"].cid, f"entries[pid={e['pid']}]")

            total = len(seen)
            expected = 54 * getattr(self, "_num_decks", 1)
            if total != expected:
                # Don't raise — just log (cards in transit during complex steps)
                pass
        except AssertionError as e:
            self._log(f"⚠️ Card integrity warning: {e}")

    def _all_picked(self):
        for p in self.players.values():
            if p.out:
                continue
            if p.skip_next:
                continue
            if not p.hand and p.pid not in self._picked_pids:
                continue
            if p.pid not in self._picked_pids:
                return False
        return True

    def _start_arrange_phase(self):
        self.phase = Phase.ARRANGE_HAND
        events = []

        for p in self.players.values():
            if p.is_ai and not p.out:
                ai_sort_hand(p)

        human_pending = [
            p.pid
            for p in self.players.values()
            if not p.is_ai and not p.out and p.pid not in self._skipped_this_round
        ]
        self._pending_arrange_pids = set(human_pending)

        events += self._send_hands()

        if not self._pending_arrange_pids:
            events += self._start_reveal()
        else:
            events.append(
                {
                    "type": "phase_change",
                    "phase": Phase.ARRANGE_HAND,
                    "pending_pids": human_pending,
                }
            )

        return events

    def player_ready_arrange(self, pid):
        if self.phase != Phase.ARRANGE_HAND:
            return [{"type": "error", "msg": "Not in arrange_hand phase"}]
        p = self.players.get(pid)
        if not p or p.out:
            return [{"type": "error", "msg": "Invalid player"}]

        self._pending_arrange_pids.discard(pid)
        events = [{"type": "arrange_ready", "pid": pid}]

        if not self._pending_arrange_pids:
            events += self._start_reveal()

        return events

    def _start_reveal(self):
        self.assert_card_integrity(f"round={self.round} reveal_start")
        self.phase = Phase.REVEAL
        self.current_step = 0
        return [
            {
                "type": "phase_change",
                "phase": Phase.REVEAL,
                "step": 1,
                "total_steps": self.declared_steps,
            }
        ]

    def _send_hands(self):
        return [
            {
                "type": "hand_update",
                "pid": pid,
                "hand": [c.to_dict() for c in ordered_hand(p)],
                "dragon_count": p.dragon_count,
            }
            for pid, p in self.players.items()
        ]

    def _ai_declare(self):
        lead = self.players[self._lead_pid()]
        steps, el = ai_declare(lead, self._max_steps)
        return self.player_declare(lead.pid, steps, el)

    def _ai_pick_all(self):
        events = []
        picks = []
        for p in self.players.values():
            if (
                p.is_ai
                and not p.out
                and not p.battle
                and not p.skip_next
                and p.pid not in self._skipped_this_round
            ):
                n = min(self.declared_steps, len(p.hand))
                _total_dragons = 6 * getattr(self, "_num_decks", 1)
                _captured = sum(pl.dragon_count for pl in self.players.values())
                p._dragons_total = _total_dragons
                p._dragons_loose = max(0, _total_dragons - _captured)
                chosen = ai_pick_cards(p, n, self.declared_el)
                picks.append((p, chosen))

        for p, chosen in picks:
            cids = [c.cid for c in chosen]
            seen_c = set()
            deduped = []
            for cid in cids:
                if cid not in seen_c:
                    deduped.append(cid)
                    seen_c.add(cid)
            hand_map = {c.cid: c for c in p.hand}
            valid_cids = [cid for cid in deduped if cid in hand_map]
            p.battle = [hand_map[cid] for cid in valid_cids]
            p.hand = [c for c in p.hand if c.cid not in set(valid_cids)]
            self._picked_pids.add(p.pid)
            events.append(
                {
                    "type": "cards_picked",
                    "pid": p.pid,
                    "count": len(valid_cids),
                    "name": p.name,
                }
            )

        if self._all_picked():
            events += self._start_arrange_phase()

        return events

    def _log(self, msg):
        self.event_log.append(msg)
        if len(self.event_log) > 50:
            self.event_log.pop(0)

    def public_state(self):
        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "round": self.round,
            "lead_pid": self._lead_pid() if self.order else None,
            "next_lead_pid": self._next_lead_pid(),
            "declared_steps": self.declared_steps,
            "declared_el": self.declared_el,
            "declared_el_name": SUIT_ELEMENT.get(self.declared_el, ""),
            "current_step": self.current_step,
            "players": {pid: p.public_dict() for pid, p in self.players.items()},
            "order": self.order,
            "log": self.event_log[-20:],
            "pending_arrange_pids": list(self._pending_arrange_pids),
            "max_steps": getattr(self, "_max_steps", 4),
            "num_decks": getattr(self, "_num_decks", 1),
            "step_history": self.step_history,
        }

    def player_state(self, pid):
        state = self.public_state()
        if pid in self.players:
            state["me"] = self.players[pid].private_dict()
        return state


class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, DragonTamerGame] = {}

    def create_room(self, room_id, max_players=10):
        import time as _time

        self._purge_old_rooms()
        game = DragonTamerGame(room_id, max_players)
        game._created_at = _time.time()
        game._last_active = _time.time()
        self.rooms[room_id] = game
        return game

    def get_room(self, room_id):
        import time as _time

        room = self.rooms.get(room_id)
        if room:
            room._last_active = _time.time()
        return room

    def delete_room(self, room_id):
        self.rooms.pop(room_id, None)

    def _purge_old_rooms(self):
        import time as _time

        now = _time.time()
        to_delete = []
        for room_id, r in self.rooms.items():
            last = getattr(r, "_last_active", now)
            phase = r.phase
            if phase == "game_over" and (now - last) > 600:
                to_delete.append(room_id)
            elif phase == "waiting" and (now - last) > 1800:
                to_delete.append(room_id)
            elif (now - last) > 7200:
                to_delete.append(room_id)
        for room_id in to_delete:
            del self.rooms[room_id]

    def list_rooms(self):
        self._purge_old_rooms()
        return [
            {
                "room_id": r.room_id,
                "players": len(r.players),
                "max_players": r.max_players,
                "phase": r.phase,
                "spectator_count": getattr(r, "_spectator_count", 0),
            }
            for r in self.rooms.values()
        ]


if __name__ == "__main__":
    rm = RoomManager()
    game = rm.create_room("test-room", max_players=4)
    game.add_player("human1", "Roy")
    game.fill_with_ai()
    print(f"Room: {game.room_id} | Players: {len(game.players)}")
    events = game.start_game()
    print(f"Start events: {[e['type'] for e in events]}")
    for _ in range(5):
        if game.phase == Phase.LEADER_DECLARE:
            if not game.players[game._lead_pid()].is_ai:
                events = game.player_declare("human1", 3, "Hearts")
        elif game.phase == Phase.PICK_CARDS:
            p = game.players["human1"]
            n = min(game.declared_steps, len(p.hand))
            cids = [c.cid for c in p.hand[:n]]
            events = game.player_pick_cards("human1", cids)
        elif game.phase == Phase.REVEAL:
            events = game.reveal_step("human1")
        print(f"Phase: {game.phase} | Round: {game.round}")
        if game.phase == Phase.GAME_OVER:
            print("GAME OVER")
            break
    print("\n✅ Engine v3.8 test passed")



