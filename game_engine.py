"""
Dragon Tamer — Game Engine v3.4
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
  - Sleeping pairs: player chooses each round (sleep/wake/pass) — no longer automatic [Bugs #9, #10]
  - Love Power: princess chooses between tamers; AI auto-chooses [Bug #11]
  - skip_next reset at start of skipped round, not end of current round [Bug #20]
  - Dominant suit bonus: +0.5 instead of +1 [Bug #1]
  - Leftover cards on uneven deal go to leader [Bug #16]
  - Victory check before elimination (edge case: 4 dragons same round as running out) [Bug #15]
  - Setup: full deck reshuffled after leader draw before dealing [Bug #17]
  - Sleeping cards excluded from _end_round card-return (card duplication bug found by stress test)
  - MAX_ROUNDS=300 stalemate safeguard: most dragons wins
  - AI sleeping: wakes pairs when 1 dragon from victory
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
WIN_DRAGONS = 5  # Default goal: 5 dragons to win. Settable to 4 or 6.
VALID_WIN_DRAGONS = (4, 5, 6)  # allowed values


def set_win_dragons(n: int):
    """Change the dragon win goal. Must be 4, 5, or 6."""
    global WIN_DRAGONS
    if n not in VALID_WIN_DRAGONS:
        raise ValueError(f"WIN_DRAGONS must be one of {VALID_WIN_DRAGONS}, got {n}")
    WIN_DRAGONS = n


MAX_ROUNDS = (
    300  # stalemate safeguard: declare winner by dragon count after this many rounds
)


class Phase(str, Enum):
    WAITING = "waiting"
    LEADER_DECLARE = "leader_declare"
    SLEEPING = "sleeping"
    PICK_CARDS = "pick_cards"
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
        return self.orig_rank == 9 and self.suit == "Clubs"

    @property
    def is_wizard(self) -> bool:
        return self.orig_rank == 8 and not self.is_joker

    @property
    def is_queen(self) -> bool:
        return self.orig_rank == 12 and not self.is_joker

    def effective_rank(self, leading_suit: Optional[str]) -> float:
        if self.is_joker or not self.suit or not leading_suit:
            return float(self.rank)
        return self.rank + 0.5 if self.suit == leading_suit else float(self.rank)

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


def _run_duel(
    contestants: List[dict],
    all_players: Dict[str, "PlayerState"],
    lead_pid: str,
    events: List[str],
    el: Optional[str] = None,
) -> tuple:
    """
    Draw top cards from each contestant's Main Pile repeatedly until one wins.
    Dominant suit bonus (+0.5) applies to drawn cards — same as in battle.
    Returns (winning_pid, duel_cards, pid_draws) where:
      - duel_cards: ALL cards drawn across all rounds (flat list)
      - pid_draws: dict pid → [cards drawn by that player]
    Per rules: winner takes all original battle cards + all duel cards into accum.

    3-way+ duel: if draw produces a tie between SOME players, only the tied players
    continue to the next draw. The eliminated/lower players drop out permanently.
    """
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
                drawn = p.hand.pop(0)
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

        # Remove players who had no cards
        active = [c for c in active if c["pid"] not in eliminated_from_duel]

        if not active:
            events.append(f"⚔️ Duel: all out of cards — leader {lead_pid} wins")
            return lead_pid, all_drawn, pid_draws

        if not draws:
            return lead_pid, all_drawn, pid_draws

        # Compare by effective rank (dominant suit applies)
        best_eff = max(draws[pid].effective_rank(el) for pid in draws)
        winners = [
            c
            for c in active
            if c["pid"] in draws and draws[c["pid"]].effective_rank(el) == best_eff
        ]

        if len(winners) == 1:
            winner_pid = winners[0]["pid"]
            events.append(f"⚔️ Duel won by {winner_pid}!")
            return winner_pid, all_drawn, pid_draws

        # Still tied — only the tied players continue, others eliminated
        tied_pids = {c["pid"] for c in winners}
        dropped = [c for c in active if c["pid"] not in tied_pids]
        for c in dropped:
            events.append(f"⚔️ Duel: {all_players[c['pid']].name} loses — out of duel")
        active = winners
        events.append(f"⚔️ Duel tied between {len(winners)} players — redraw!")


def resolve_step(
    entries: List[dict],
    el: Optional[str],
    lead_pid: str,
    all_players: Optional[Dict] = None,
) -> dict:
    """
    Resolve a single battle step.
    all_players is the game's players dict; required for duels.
    """
    result = {
        "winner_pid": None,
        "all_cards": [e["card"] for e in entries],
        "joker_powers": [],
        "love_right_pid": None,
        "love_choice_needed": None,
        "space_dragon_pid": None,
        "portal_pid": None,
        "special_events": [],
        "duel_draws": {},  # pid → [cards drawn in duel]
    }

    valid = [e for e in entries if not e.get("forfeited", False)]
    if not valid:
        return result

    # ── Portal dual-entry: if a player has two cards (portal + stolen),
    # collapse to the best-ranked one for resolution purposes,
    # but keep ALL cards in all_cards so winner takes the full pot.
    pid_entries: Dict[str, List[dict]] = {}
    for e in valid:
        pid_entries.setdefault(e["pid"], []).append(e)

    resolved_valid = []
    for pid, player_entries in pid_entries.items():
        if len(player_entries) == 1:
            resolved_valid.append(player_entries[0])
        else:
            # Player has multiple cards (portal + stolen) — use best-ranked for competition
            best_entry = max(
                player_entries, key=lambda e: e["card"].effective_rank(el or "Hearts")
            )
            other_entries = [e for e in player_entries if e is not best_entry]
            combined = dict(best_entry)
            combined["portal_extras"] = [e["card"] for e in other_entries]
            if any(e.get("stolen") for e in player_entries):
                combined["stolen"] = True
            resolved_valid.append(combined)
            portal_card = next(
                (e["card"] for e in player_entries if e["card"].is_portal), None
            )
            stolen_card = next(
                (e["card"] for e in player_entries if e.get("stolen")), None
            )
            best_label = best_entry["card"].label
            other_labels = [e["card"].label for e in other_entries]
            result["special_events"].append(
                f"🌀 {pid} plays Portal ({portal_card.label if portal_card else '9♣'}) "
                f"+ stolen {stolen_card.label if stolen_card else other_labels[0]} "
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

    # ── Love Power (always checked regardless of outcome) ──
    # Must be evaluated before Wizard tamer-inheritance changes who "holds" Love Power.
    # Rule: Love Power stays with the player who PLAYED the Tamer, even if Wizard inherits.
    love_tamers = list(tamers)  # snapshot before any wizard shenanigans

    # ── Wizard tamer-inheritance check ──
    # A Wizard of suit S that beats the Tamer of suit S inherits tamer power
    # when a Dragon is also present.
    wizard_inherited_tamer: Optional[dict] = None  # the wizard entry that inherits
    wizard_displaced_tamer: Optional[dict] = None  # the tamer it beat

    if has_dragon and wizards and tamers:
        for w_entry in wizards:
            w = w_entry["card"]
            # Find a tamer of the SAME suit
            same_suit_tamer = next(
                (t for t in tamers if t["card"].suit == w.suit), None
            )
            if same_suit_tamer:
                # Wizard beats that tamer (rank 8 < tamer rank 2... wait,
                # Tamer is orig_rank 2 → rank 2; Wizard is orig_rank 8 → rank 8.
                # Wizard (8) > Tamer (2), so Wizard always beats same-suit Tamer.
                wizard_inherited_tamer = w_entry
                wizard_displaced_tamer = same_suit_tamer
                result["special_events"].append(
                    f"🧙 {w_entry['pid']}'s Wizard ({w.label}) inherits Tamer power "
                    f"from {same_suit_tamer['pid']}!"
                )
                break  # only one inheritance can happen per step

    # Rebuild effective tamer list for combat resolution:
    # Wizard replaces the displaced Tamer. Love Power stays with Tamer's player.
    combat_tamers = list(tamers)
    if wizard_inherited_tamer and wizard_displaced_tamer:
        # Remove the displaced Tamer, add the Wizard as its replacement
        combat_tamers = [
            e for e in combat_tamers if e is not wizard_displaced_tamer
        ] + [wizard_inherited_tamer]

    # ── Love Power — uses original tamer players ──
    if love_tamers and princesses:
        if len(love_tamers) == 1 and len(princesses) == 1:
            # Simple case: 1 princess, 1 tamer → automatic
            result["love_right_pid"] = love_tamers[0]["pid"]
            result["special_events"].append(
                f"💕 Love Power! {love_tamers[0]['pid']} earns next lead!"
            )
        elif len(love_tamers) == 1:
            # Multiple princesses, 1 tamer → all vote for the only tamer → automatic
            result["love_right_pid"] = love_tamers[0]["pid"]
            result["special_events"].append(
                f"💕 Love Power! {len(princesses)} Princesses — only one Tamer: "
                f"{love_tamers[0]['pid']} earns next lead!"
            )
        else:
            # Multiple tamers → majority vote among all princesses
            # Signal for caller to collect votes (human input) or compute (all AI)
            result["love_choice_needed"] = {
                "princess_pids": [e["pid"] for e in princesses],
                "tamer_pids": [e["pid"] for e in love_tamers],
                "votes_needed": len(princesses),
            }
            result["special_events"].append(
                f"💕 Love Power — {len(princesses)} Princess(es) vote for "
                f"{len(love_tamers)} Tamers! Majority wins; tie = cancelled."
            )

    # ── Tamer / Wizard-as-Tamer beats dragons ──
    if has_dragon and len(combat_tamers) == 1:
        result["winner_pid"] = combat_tamers[0]["pid"]
        result["special_events"].append(
            f"⚔️ {combat_tamers[0]['pid']}'s "
            f"{'Wizard' if combat_tamers[0] is wizard_inherited_tamer else 'Tamer'} "
            f"beats all dragons!"
        )
        # If Jokers are present and Tamer wins, Tamer player inherits joker powers
        joker_types = [j["card"].joker_type for j in jokers if j["card"].joker_type]
        if joker_types:
            if len(joker_types) == 1:
                result["joker_powers"] = joker_types
                result["special_events"].append(
                    f"💕 {combat_tamers[0]['pid']}'s Tamer inherits {joker_types[0]} Dragon power!"
                )
            else:
                result["joker_powers"] = joker_types
                result["special_events"].append(
                    f"💕 {combat_tamers[0]['pid']}'s Tamer must choose a Joker power: {joker_types}"
                )
        # Space Dragon power goes to Tamer winner if Space Dragon was in step
        space_j = next((j for j in jokers if j["card"].joker_type == "space"), None)
        if space_j:
            result["space_dragon_pid"] = combat_tamers[0]["pid"]
            result["special_events"].append(
                f"🌌 Space Dragon power goes to {combat_tamers[0]['pid']} (Tamer winner)!"
            )
        return result

    if has_dragon and len(combat_tamers) > 1:
        # Tamer duel: first compare effective rank (dominant suit applies).
        # Only if ranks are exactly equal do we fall back to card draw.
        best_tamer_eff = max(e["card"].effective_rank(el) for e in combat_tamers)
        top_tamers = [
            e for e in combat_tamers if e["card"].effective_rank(el) == best_tamer_eff
        ]
        if len(top_tamers) == 1:
            # Clear winner by effective rank — no duel needed
            winner_pid = top_tamers[0]["pid"]
            result["special_events"].append(
                f"⚔️ {top_tamers[0]['pid']}'s Tamer wins by higher rank "
                f"({top_tamers[0]['card'].label} eff:{best_tamer_eff:.1f})!"
            )
        else:
            # Exact tie — card draw duel
            result["special_events"].append("⚔️ Tamer duel — equal rank, drawing cards!")
            if all_players:
                winner_pid, duel_cards, pid_draws = _run_duel(
                    top_tamers, all_players, lead_pid, result["special_events"], el
                )
                result["all_cards"] += duel_cards
                result["duel_draws"].update(pid_draws)
            else:
                winner_pid = top_tamers[0]["pid"]
        result["winner_pid"] = winner_pid
        # Winning Tamer inherits joker powers
        joker_types = [j["card"].joker_type for j in jokers if j["card"].joker_type]
        if joker_types:
            result["joker_powers"] = joker_types
            result["special_events"].append(
                f"💕 {winner_pid}'s Tamer inherits joker power(s): {joker_types}"
            )
        # Space Dragon power goes to Tamer winner
        space_j = next((j for j in jokers if j["card"].joker_type == "space"), None)
        if space_j:
            result["space_dragon_pid"] = winner_pid
            result["special_events"].append(
                f"🌌 Space Dragon power goes to {winner_pid} (Tamer duel winner)!"
            )
        return result

    # ── Queen logic ──
    # Queen beats all cards of her OWN suit except Wizard and Dragon of that suit.
    # Loses to Kings from OTHER suits.
    # No dragon present here (handled above), no tamer.
    if queens and not has_dragon and not combat_tamers:
        # Check each queen: does she dominate?
        surviving_queens = []
        for q_entry in queens:
            q = q_entry["card"]
            beaten = False
            for e in valid:
                c = e["card"]
                if c is q:
                    continue
                if c.suit == q.suit:
                    # Same suit: Queen loses only to Wizard and Dragon of own suit
                    if c.is_wizard or c.is_dragon:
                        beaten = True
                        break
                else:
                    # Other suit: Queen loses to Kings (rank 13)
                    if c.orig_rank == 13:
                        beaten = True
                        break
            if not beaten:
                surviving_queens.append(q_entry)

        if surviving_queens:
            if len(surviving_queens) == 1:
                result["winner_pid"] = surviving_queens[0]["pid"]
                result["special_events"].append(
                    f"👑 {surviving_queens[0]['pid']}'s Queen dominates!"
                )
                # Portal detection still applies
                portal_e = next((e for e in valid if e["card"].is_portal), None)
                if portal_e:
                    result["portal_pid"] = portal_e["pid"]
                return result
            else:
                # Multiple queens survive — fall through to normal rank comparison
                # (they'll tie and go to duel below)
                result["special_events"].append("👑 Multiple Queens — comparing ranks!")

    # ── Wizard normal power (no dragon present, no tamer) ──
    # Wizard beats all cards of its own suit up to King (rank ≤ 13).
    # Two Wizards: higher effective rank wins; equal → duel.
    if wizards and not has_dragon and not combat_tamers:
        # Find the best non-wizard non-dragon cards
        non_wizard_entries = [e for e in valid if not e["card"].is_wizard]
        for w_entry in wizards:
            w = w_entry["card"]
            same_suit_losers = [
                e
                for e in non_wizard_entries
                if e["card"].suit == w.suit
                and not e["card"].is_dragon
                and e["card"].orig_rank <= 13
            ]
            # Remove all cards this wizard beats from competition
            non_wizard_entries = [
                e for e in non_wizard_entries if e not in same_suit_losers
            ]

        # Now resolve among wizards + any remaining non-wizard cards
        contenders = wizards + non_wizard_entries
        best_rank = max(e["card"].effective_rank(el) for e in contenders)
        top = [e for e in contenders if e["card"].effective_rank(el) == best_rank]

        if len(top) == 1:
            result["winner_pid"] = top[0]["pid"]
            winner_card = top[0]["card"]
            if winner_card.is_wizard:
                result["special_events"].append(f"🧙 Wizard wins: {top[0]['pid']}!")
            else:
                result["special_events"].append(
                    f"🧙 Wizard cleared same-suit cards — {top[0]['pid']} wins with {winner_card.label}!"
                )
        else:
            # Tied wizards (or wizard tied with another card) → duel
            result["special_events"].append("🧙 Wizard tie — duel!")
            if all_players:
                winner_pid, duel_cards, pid_draws = _run_duel(
                    top, all_players, lead_pid, result["special_events"], el
                )
                result["all_cards"] += duel_cards
                result["duel_draws"].update(pid_draws)
            else:
                winner_pid = lead_pid
            result["winner_pid"] = winner_pid

        portal_e = next((e for e in valid if e["card"].is_portal), None)
        if portal_e:
            result["portal_pid"] = portal_e["pid"]
        return result

    # ── Joker equality rule ──
    # Any Joker always enters a duel with ALL regular Dragons present.
    # 1 Joker + 2 Dragons → 3-way duel; 2 Jokers + 1 Dragon → 3-way duel; etc.
    # Applies only when both Jokers and regular (non-joker) Dragons are present
    # and no Tamer/Wizard has already resolved the battle above.
    regular_dragons = [
        e for e in valid if e["card"].is_dragon and not e["card"].is_joker
    ]
    if jokers and regular_dragons:
        duel_entries = jokers + regular_dragons
        n_j = len(jokers)
        n_d = len(regular_dragons)
        result["special_events"].append(
            f"\U0001f0cf Joker equality rule: {n_j} Joker(s) + {n_d} Dragon(s) \u2014 "
            f"{n_j + n_d}-way duel!"
        )
        if all_players:
            winner_pid, duel_cards, pid_draws = _run_duel(
                duel_entries, all_players, lead_pid, result["special_events"], el
            )
            result["all_cards"] += duel_cards
            result["duel_draws"].update(pid_draws)
        else:
            winner_pid = lead_pid
        result["winner_pid"] = winner_pid
        # Joker power fires only if a Joker wins
        winning_entry = next((e for e in duel_entries if e["pid"] == winner_pid), None)
        if winning_entry and winning_entry["card"].is_joker:
            result["joker_powers"] = [winning_entry["card"].joker_type]
            result["special_events"].append(
                f"\U0001f0cf {winner_pid} wins duel \u2014 "
                f"{winning_entry['card'].joker_type} Dragon power activates!"
            )
        else:
            result["joker_powers"] = []
            result["special_events"].append(
                f"\U0001f0cf {winner_pid}'s Dragon wins duel \u2014 Joker power forfeit."
            )
        # Space Dragon: fires if Space Joker wins
        space_j = next((j for j in jokers if j["card"].joker_type == "space"), None)
        if space_j and winner_pid == space_j["pid"]:
            result["space_dragon_pid"] = winner_pid
            result["special_events"].append(
                f"\U0001f30c Space Dragon! {winner_pid} wins duel \u2014 may swap seats."
            )
        portal_e = next((e for e in valid if e["card"].is_portal), None)
        if portal_e:
            result["portal_pid"] = portal_e["pid"]
        return result

    # ── Normal resolution ──
    best = _best_card([e["card"] for e in valid], el)
    top_e = best.effective_rank(el)
    tied = [e for e in valid if e["card"].effective_rank(el) == top_e]

    if len(tied) == 1:
        result["winner_pid"] = tied[0]["pid"]
    else:
        # All tied players (dragons, jokers, or any equal-rank cards) enter a duel.
        # Jokers and regular dragons are treated equally — whoever is highest rank
        # among the tied group wins. Only the WINNER's joker power (if any) fires.
        dragon_tied = [e for e in tied if e["card"].is_dragon]  # includes jokers
        all_tied = tied  # all tied, regardless of card type

        if len(all_tied) >= 2:
            n_drag = len(dragon_tied)
            n_jok = sum(1 for e in all_tied if e["card"].is_joker)
            if n_jok >= 2:
                result["special_events"].append(
                    f"🃏 Dragon duel! ({n_jok} Jokers"
                    + (f" + {n_drag - n_jok} Dragon(s)" if n_drag > n_jok else "")
                    + ")"
                )
            else:
                result["special_events"].append(
                    f"⚔️ Dragon duel! ({n_drag} dragon(s) tied)"
                )

            if all_players:
                winner_pid, duel_cards, pid_draws = _run_duel(
                    all_tied, all_players, lead_pid, result["special_events"], el
                )
                result["all_cards"] += duel_cards
                result["duel_draws"].update(pid_draws)
            else:
                winner_pid = lead_pid
            result["winner_pid"] = winner_pid

            # Joker power: only applies if the WINNER played a joker
            winning_entry = next((e for e in all_tied if e["pid"] == winner_pid), None)
            if winning_entry and winning_entry["card"].is_joker:
                result["joker_powers"] = [winning_entry["card"].joker_type]
                result["special_events"].append(
                    f"🃏 {winner_pid} wins duel with {winning_entry['card'].joker_type} Dragon — power activates!"
                )
            else:
                # Winner played a regular dragon — no joker power fires
                result["joker_powers"] = []
                if n_jok > 0:
                    result["special_events"].append(
                        "🃏 Joker owner(s) lost the duel — dragon powers forfeit."
                    )
        else:
            result["winner_pid"] = lead_pid

    # Space Dragon — fires if Space Dragon is in the step AND winner holds it or beat it with Tamer
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
    sleeping: List[tuple] = field(default_factory=list)
    out: bool = False
    skip_next: bool = False
    is_ai: bool = False
    ai_strategy: str = "Balanced"

    @property
    def dragon_count(self) -> int:
        # ── FIX: include battle cards — dragons may already be picked
        return (
            sum(1 for c in self.hand if c.is_dragon)
            + sum(1 for c in self.battle if c.is_dragon)
            + len(self.sleeping)
        )

    def apply_sleeping(self):
        sleeping_cids = {c.cid for pair in self.sleeping for c in pair}
        dragons = {
            c.suit: c
            for c in self.hand
            if c.is_dragon and not c.is_joker and c.suit and c.cid not in sleeping_cids
        }
        tamers = {
            c.suit: c
            for c in self.hand
            if c.is_tamer and c.suit and c.cid not in sleeping_cids
        }
        for suit in SUITS:
            if suit in dragons and suit in tamers:
                t, d = tamers[suit], dragons[suit]
                if t in self.hand and d in self.hand:
                    self.hand.remove(t)
                    self.hand.remove(d)
                    self.sleeping.append((t, d))

    def sleep_pair(self, tamer_cid: int, dragon_cid: int) -> bool:
        """
        Move a specific tamer+dragon pair to sleeping.
        Returns True on success, False if the pair is invalid or a joker.
        """
        hand_map = {c.cid: c for c in self.hand}
        t = hand_map.get(tamer_cid)
        d = hand_map.get(dragon_cid)
        if not t or not d:
            return False
        if not t.is_tamer or not d.is_dragon:
            return False
        if d.is_joker:  # Jokers cannot sleep
            return False
        if t.suit != d.suit:  # Must be matching suit
            return False
        self.hand.remove(t)
        self.hand.remove(d)
        self.sleeping.append((t, d))
        return True

    def wake_pair(self, pair_index: int) -> bool:
        """
        Wake a sleeping pair by index, returning both cards to hand.
        Returns True on success.
        """
        if pair_index < 0 or pair_index >= len(self.sleeping):
            return False
        t, d = self.sleeping.pop(pair_index)
        self.hand.append(t)
        self.hand.append(d)
        return True

    def eligible_sleep_pairs(self) -> List[tuple]:
        """
        Return list of (tamer, dragon) pairs in hand that could be put to sleep.
        Excludes jokers and pairs already sleeping.
        """
        sleeping_cids = {c.cid for pair in self.sleeping for c in pair}
        dragons = {
            c.suit: c
            for c in self.hand
            if c.is_dragon and not c.is_joker and c.suit and c.cid not in sleeping_cids
        }
        tamers = {
            c.suit: c
            for c in self.hand
            if c.is_tamer and c.suit and c.cid not in sleeping_cids
        }
        pairs = []
        for suit in SUITS:
            if suit in dragons and suit in tamers:
                pairs.append((tamers[suit], dragons[suit]))
        return pairs

    def public_dict(self) -> dict:
        return {
            "pid": self.pid,
            "name": self.name,
            "dragon_count": self.dragon_count,
            "hand_count": len(self.hand),
            "battle_count": len(self.battle),
            "sleeping_count": len(self.sleeping),
            "out": self.out,
            "is_ai": self.is_ai,
        }

    def private_dict(self) -> dict:
        return {
            **self.public_dict(),
            "hand": [c.to_dict() for c in self.hand],
            "sleeping": [[t.to_dict(), d.to_dict()] for t, d in self.sleeping],
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

    if strat == "Aggressive":
        return sorted_h[:n]

    elif strat == "Conservative":
        if dragons_count >= 3:
            return sorted_h[:n]
        result = others[:n]
        if len(result) < n:
            result += dragons[: n - len(result)]
        if len(result) < n:
            result += tamers[: n - len(result)]
        return result[:n]

    elif strat in ("Diplomat", "AntiDragon"):
        precious = [c for c in hand if c.is_tamer or c.is_princess]
        result = others[:n]
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

    # ── NEW CHARACTERS ──

    elif strat == "DragonHunter":
        # Dragons first (highest rank), then high others, then tamers as filler
        high_others = sorted(others, key=lambda c: -c.effective_rank(el))
        result = list(dragons)
        if len(result) < n:
            result += high_others[: n - len(result)]
        if len(result) < n:
            result += tamers[: n - len(result)]
        return result[:n]

    elif strat == "Purist":
        # Only dominant-suit cards, highest first; fill with any high card
        suit_cards = sorted(
            [c for c in hand if c.suit == el], key=lambda c: -c.effective_rank(el)
        )
        off_suit = sorted(
            [c for c in hand if c.suit != el], key=lambda c: -c.effective_rank(el)
        )
        result = suit_cards[:n]
        if len(result) < n:
            result += off_suit[: n - len(result)]
        return result[:n]

    elif strat == "Maximalist":
        # Strict alternating extreme: best, worst, 2nd best, 2nd worst...
        asc = sorted(hand, key=lambda c: c.effective_rank(el))  # worst first
        desc = sorted(hand, key=lambda c: -c.effective_rank(el))  # best first
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
        # Single best card (always declares 1 step, so n=1 normally)
        return sorted_h[:n]

    elif strat == "Opportunist":
        # Try to beat what the prev winner played — send cards just above their level
        # Falls back to high cards if no prev info
        prev_max_rank = (
            max((c.effective_rank(el) for c in player.prev_step_cards_seen), default=0)
            if hasattr(player, "prev_step_cards_seen") and player.prev_step_cards_seen
            else 0
        )

        if prev_max_rank > 0:
            # Cards that beat prev winner, sorted lowest-sufficient first (efficient)
            beaters = sorted(
                [c for c in hand if c.effective_rank(el) > prev_max_rank],
                key=lambda c: c.effective_rank(el),
            )
            result = beaters[:n]
            if len(result) < n:
                # Not enough beaters — send best available
                result += [c for c in sorted_h if c.cid not in {x.cid for x in result}]
            return result[:n]
        else:
            return sorted_h[:n]

    else:
        # Default: alternating high/low (Balanced, Hoarder, Adaptive, Avenger, RandomAI)
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


def ai_declare(player: PlayerState) -> tuple:
    hand = player.hand
    strat = player.ai_strategy
    d = player.dragon_count
    n_hand = len(hand)

    def suit_value(s):
        return sum(c.effective_rank(s) for c in hand if c.suit == s)

    def suit_count(s):
        return sum(1 for c in hand if c.suit == s)

    def dragon_suit():
        """Suit where player holds most dragons."""
        ds = [c.suit for c in hand if c.is_dragon and c.suit]
        return max(SUITS, key=lambda s: ds.count(s)) if ds else best_value_suit()

    def best_value_suit():
        return max(SUITS, key=suit_value)

    def richest_suit():
        """Suit with most cards in hand."""
        return max(SUITS, key=suit_count)

    def highest_card_suit():
        """Suit of single highest-ranked card."""
        if not hand:
            return "Hearts"
        best = max(hand, key=lambda c: c.rank)
        return best.suit or "Hearts"

    # ── Existing strategies ──
    if strat == "Aggressive":
        return (4 if d == 0 else 5), best_value_suit()

    elif strat == "Conservative":
        steps = 4 if d >= 3 else (2 if d >= 2 else 1)
        return steps, best_value_suit()

    elif strat == "Bluffer":
        return (1 if d < 2 else 3), best_value_suit()

    elif strat == "Diplomat":
        return (2 if d < 3 else 4), best_value_suit()

    # ── New characters ──
    elif strat == "DragonHunter":
        # Element: suit of most dragons held
        # Steps: 3 always (send dragons + fillers to contest 3 steps)
        return 3, dragon_suit()

    elif strat == "Purist":
        # Element: suit with most cards
        # Steps: number of cards in that suit (min 1, max 5)
        el = richest_suit()
        steps = max(1, min(5, suit_count(el)))
        return steps, el

    elif strat == "Maximalist":
        # Element: highest total value suit
        # Steps: always 5 (flood the table)
        steps = min(5, max(1, n_hand))
        return steps, best_value_suit()

    elif strat == "Minimalist":
        # Element: suit of single best card
        # Steps: always 1 (precision over volume)
        return 1, highest_card_suit()

    elif strat == "Opportunist":
        # Element: best value suit (adapt each round)
        # Steps: 1 if behind (d < 2), 3 if competitive, 5 if close to winning
        steps = 5 if d >= WIN_DRAGONS - 1 else (3 if d >= 2 else 1)
        return steps, best_value_suit()

    else:
        # Default (Balanced, Hoarder, Adaptive, Avenger, RandomAI, etc.)
        return 3, best_value_suit()


def ai_sleeping_choice(player: PlayerState) -> dict:
    strat = player.ai_strategy
    eligible = player.eligible_sleep_pairs()
    has_sleeping = len(player.sleeping) > 0
    dragons_needed = WIN_DRAGONS - player.dragon_count

    # Universal: if 1 more dragon wins, wake a pair to deploy it
    if has_sleeping and dragons_needed <= 1:
        return {"action": "wake", "pair_index": 0}

    if strat in ("Conservative", "Hoarder"):
        if len(player.sleeping) >= WIN_DRAGONS - 1 and has_sleeping:
            return {"action": "wake", "pair_index": 0}
        if eligible:
            t, d = eligible[0]
            return {"action": "sleep", "tamer_cid": t.cid, "dragon_cid": d.cid}

    elif strat == "Aggressive":
        if has_sleeping:
            return {"action": "wake", "pair_index": 0}
        if eligible:
            t, d = eligible[0]
            return {"action": "sleep", "tamer_cid": t.cid, "dragon_cid": d.cid}

    elif strat in ("Diplomat", "Balanced", "Adaptive"):
        if len(player.sleeping) < 2 and eligible:
            t, d = eligible[0]
            return {"action": "sleep", "tamer_cid": t.cid, "dragon_cid": d.cid}

    # ── New characters ──
    elif strat == "DragonHunter":
        # Never sleep — keep all dragons in hand for battle
        pass

    elif strat == "Purist":
        # Sleep pairs where dragon suit doesn't match the purist's dominant suit
        # (determined by richest suit in hand)
        dominant = max(
            ("Hearts", "Diamonds", "Clubs", "Spades"),
            key=lambda s: sum(1 for c in player.hand if c.suit == s),
        )
        off_suit_pairs = [(t, d) for t, d in eligible if d.suit != dominant]
        if off_suit_pairs:
            t, d = off_suit_pairs[0]
            return {"action": "sleep", "tamer_cid": t.cid, "dragon_cid": d.cid}
        # Don't sleep same-suit dragons
        pass

    elif strat == "Maximalist":
        # Never sleep — needs all cards to fill max steps
        pass

    elif strat == "Minimalist":
        # Sleep EVERY eligible pair immediately — dragons are safest asleep
        if eligible:
            t, d = eligible[0]
            return {"action": "sleep", "tamer_cid": t.cid, "dragon_cid": d.cid}

    elif strat == "Opportunist":
        # Sleep if already has 2+ sleeping pairs, else pass
        if len(player.sleeping) >= 2 and eligible:
            pass  # enough sleeping, don't over-extend
        elif eligible and len(player.sleeping) < 2:
            t, d = eligible[0]
            return {"action": "sleep", "tamer_cid": t.cid, "dragon_cid": d.cid}

    return {"action": "pass"}


def ai_forced_wake_choice(
    player: PlayerState, pairs_needed: int, declared_el: Optional[str]
) -> List[int]:
    strat = player.ai_strategy
    indexed = list(enumerate(player.sleeping))

    def score_deploy(idx_pair):
        idx, (t, d) = idx_pair
        matches_el = 1 if d.suit == declared_el else 0
        return matches_el, d.rank

    def score_preserve(idx_pair):
        idx, (t, d) = idx_pair
        matches_el = 1 if d.suit == declared_el else 0
        return -matches_el, -d.rank  # sacrifice weakest non-matching

    if strat in ("Aggressive", "Balanced", "Diplomat", "DragonHunter", "Opportunist"):
        # Deploy the best matching pair
        ranked = sorted(indexed, key=score_deploy, reverse=True)
    elif strat in ("Conservative", "Hoarder", "Purist"):
        # Sacrifice weakest pair, preserve best
        ranked = sorted(indexed, key=score_preserve, reverse=True)
    elif strat == "Maximalist":
        # Deploy highest-rank pair regardless of suit (maximise battle power)
        ranked = sorted(indexed, key=lambda x: x[1][1].rank, reverse=True)
    elif strat == "Minimalist":
        # Wake the pair with highest-rank dragon (precision deployment)
        ranked = sorted(indexed, key=lambda x: x[1][1].rank, reverse=True)
    elif strat == "Adaptive":
        ranked = sorted(indexed, key=score_deploy, reverse=True)
    else:
        ranked = indexed  # FIFO fallback

    chosen_indices = [idx for idx, _ in ranked[:pairs_needed]]
    return sorted(chosen_indices, reverse=True)


def ai_portal_target(player, valid_targets):
    """AI chooses portal steal target by strategy."""
    strat = player.ai_strategy

    def stealable_count(p):
        sleeping_cids = {c.cid for pair in p.sleeping for c in pair}
        return sum(1 for c in p.hand if c.cid not in sleeping_cids)

    if strat in ("Aggressive", "Adaptive", "DragonHunter", "Minimalist"):
        return max(valid_targets, key=lambda p: (p.dragon_count, stealable_count(p)))
    elif strat in (
        "Hoarder",
        "Maximalist",
        "Diplomat",
        "AntiDragon",
        "Avenger",
        "RandomAI",
        "Bluffer",
    ):
        return max(valid_targets, key=stealable_count)
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
    else:
        return max(valid_targets, key=stealable_count)


def ai_space_dragon_swap(player, active_players):
    opponents = [p for p in active_players if p.pid != player.pid]
    if not opponents:
        return None
    strat = player.ai_strategy
    if strat in ("Conservative", "Hoarder"):
        return None
    elif strat in ("Aggressive", "Adaptive", "DragonHunter", "Minimalist"):
        return max(opponents, key=lambda p: p.dragon_count).pid
    elif strat in ("Balanced", "Diplomat", "Purist", "Maximalist"):
        return opponents[0].pid
    elif strat == "Opportunist":
        prev = getattr(player, "_prev_step_winner_seen", None)
        if prev:
            m = next((p for p in opponents if p.pid == prev), None)
            if m:
                return m.pid
        return opponents[0].pid
    else:
        import random as _r

        return _r.choice(opponents).pid


def ai_time_dragon_choice(player, prev_step_cards, prev_step_winner_pid):
    strat = player.ai_strategy
    has_prev = bool(prev_step_cards)
    prev_has_dragon = has_prev and any(c.is_dragon for c in prev_step_cards)
    prev_big = has_prev and len(prev_step_cards) >= 3
    if strat in ("Aggressive", "DragonHunter"):
        return "back" if has_prev else "forward"
    elif strat in ("Hoarder", "Conservative"):
        return "back" if has_prev else "nothing"
    elif strat == "Maximalist":
        return "back" if prev_big else "forward"
    elif strat == "Minimalist":
        return "forward"
    elif strat == "Opportunist":
        return "back" if prev_has_dragon else "forward"
    elif strat in ("Balanced", "Adaptive", "Purist"):
        return "back" if has_prev else "forward"
    else:
        return "back" if has_prev else "nothing"


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
        self.event_log: List[str] = []
        # Portal state — set while waiting for human to choose a target
        self._pending_portal_pid: Optional[str] = None
        self._pending_portal_entries: List[dict] = []
        # Sleeping phase — tracks which players still need to make their choice
        self._sleeping_pending: List[str] = []  # pids yet to act
        # Love Power — set while waiting for princess to choose between tamers
        self._pending_love_princess_pids: List[str] = []  # all princesses yet to vote
        self._pending_love_tamer_pids: List[str] = []
        self._pending_love_step_result: Optional[dict] = None
        self._pending_love_entries: List[dict] = []
        self._pending_love_si: int = 0
        self._pending_love_votes: dict = {}  # princess_pid → chosen_tamer_pid
        # Forced wake — set while waiting for human to choose which pairs to wake
        self._pending_forced_wake_pid: Optional[str] = None
        self._pending_forced_wake_needed: int = 0
        # Space Dragon — set while waiting for seat-swap choice
        self._pending_space_dragon_pid: Optional[str] = None
        self._pending_space_dragon_entries: List[dict] = []
        self._pending_space_dragon_si: int = 0
        # Joker power selection — when Tamer wins both Jokers, player must choose one
        self._pending_joker_pid: Optional[str] = None
        self._pending_joker_options: List[str] = []  # ['time','space'] or subset
        self._pending_joker_entries: List[dict] = []
        self._pending_joker_si: int = 0
        # Time Dragon choice — pause for human player
        self._pending_time_dragon_pid: Optional[str] = None
        self._pending_time_dragon_si: int = 0
        # Cards won mid-round — tracked separately so battle stays fixed-length
        # (rev_idx = len(battle)-1-si requires battle not to shrink between steps)
        self._claimed_cids: set = set()
        self.final_snapshot: dict = {}
        self._skipped_this_round: set = set()  # pids skipping the current round

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
        ]
        i = 0
        while len(self.players) < self.max_players:
            strat = (strategies or all_strats)[i % len(strategies or all_strats)]
            ai_id = f"AI_{i + 1}"
            ai_name = f"{strat[:4]}-{i + 1}"
            self.add_player(ai_id, ai_name, is_ai=True, ai_strategy=strat)
            i += 1

    def start_game(self) -> List[dict]:
        if self.phase != Phase.WAITING:
            return [{"type": "error", "msg": "Game already started"}]
        if len(self.players) < 2:
            return [{"type": "error", "msg": "Need at least 2 players"}]

        deck = build_deck()
        random.shuffle(deck)
        n = len(self.order)

        # Leader by highest dealt card from initial draw
        leader_cards = {pid: deck[i] for i, pid in enumerate(self.order)}
        best_pid = max(self.order, key=lambda pid: leader_cards[pid].rank)
        self.lead_idx = self.order.index(best_pid)

        # ── Fix #17: reshuffle ALL 54 cards back in, then deal fresh ──
        random.shuffle(deck)
        per = len(deck) // n
        leftover = deck[per * n :]  # cards that don't divide evenly
        for i, pid in enumerate(self.order):
            self.players[pid].hand = deck[i * per : (i + 1) * per]
        # ── Fix #16: leftover cards go to leader ──
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
            },
            {
                "type": "phase_change",
                "phase": Phase.LEADER_DECLARE,
                "leader_pid": self._lead_pid(),
                "round": self.round,
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
        steps = max(1, min(5, steps))

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
        events += self._start_sleeping_phase()
        return events

    def _start_sleeping_phase(self) -> List[dict]:
        """Enter SLEEPING phase — collect choices from all active players."""
        self.phase = Phase.SLEEPING
        self.current_step = 0
        self._claimed_cids = set()  # fresh for this round
        self._skipped_this_round = set()  # fresh for this round

        # ── Fix #20: reset skip_next HERE, at the start of the round being skipped ──
        # Capture skipped players BEFORE clearing the flag, so they're excluded this round.
        skip_events = []
        skipped_pids = set()
        for p in self.players.values():
            if p.skip_next:
                skipped_pids.add(p.pid)
                self._skipped_this_round.add(p.pid)
                p.skip_next = False
                self._log(f"⏳ {p.name} returns from Time Dragon skip.")
                skip_events.append({"type": "skip_next_cleared", "pid": p.pid})

        active = [
            p for p in self.players.values() if not p.out and p.pid not in skipped_pids
        ]
        self._sleeping_pending = [p.pid for p in active]

        self.assert_card_integrity(f"round={self.round} sleeping_phase_start")

        events = skip_events + [
            {
                "type": "phase_change",
                "phase": Phase.SLEEPING,
                "pending_pids": list(self._sleeping_pending),
            }
        ]

        # AI players act immediately
        events += self._ai_sleeping_all()
        return events

    def _ai_sleeping_all(self) -> List[dict]:
        """Process sleeping choices for all AI players still pending."""
        events = []
        for pid in list(self._sleeping_pending):
            p = self.players[pid]
            if p.is_ai:
                choice = ai_sleeping_choice(p)
                events += self._apply_sleeping_choice(pid, choice)
        return events

    def player_sleeping_choice(
        self,
        pid: str,
        action: str,
        tamer_cid: int = None,
        dragon_cid: int = None,
        pair_index: int = None,
    ) -> List[dict]:
        """
        Human calls this with action='sleep'|'wake'|'pass'.
        For 'sleep': tamer_cid and dragon_cid required.
        For 'wake':  pair_index required.
        """
        if self.phase != Phase.SLEEPING:
            return [{"type": "error", "msg": "Not in sleeping phase"}]
        if pid not in self._sleeping_pending:
            return [{"type": "error", "msg": "Not your turn for sleeping choice"}]
        choice = {
            "action": action,
            "tamer_cid": tamer_cid,
            "dragon_cid": dragon_cid,
            "pair_index": pair_index,
        }
        return self._apply_sleeping_choice(pid, choice)

    def _apply_sleeping_choice(self, pid: str, choice: dict) -> List[dict]:
        p = self.players[pid]
        events = []
        action = choice.get("action", "pass")

        if action == "sleep":
            t_cid = choice.get("tamer_cid")
            d_cid = choice.get("dragon_cid")
            if t_cid and d_cid and p.sleep_pair(t_cid, d_cid):
                self._log(f"😴 {p.name} puts a pair to sleep.")
                events.append(
                    {
                        "type": "sleeping_choice",
                        "pid": pid,
                        "action": "sleep",
                        "dragon_count": p.dragon_count,
                    }
                )
            else:
                events.append(
                    {
                        "type": "sleeping_choice",
                        "pid": pid,
                        "action": "pass",
                        "reason": "invalid_pair",
                    }
                )

        elif action == "wake":
            idx = choice.get("pair_index", 0)
            if isinstance(idx, int) and p.wake_pair(idx):
                self._log(f"⏰ {p.name} wakes a sleeping pair!")
                events.append(
                    {
                        "type": "sleeping_choice",
                        "pid": pid,
                        "action": "wake",
                        "dragon_count": p.dragon_count,
                    }
                )
            else:
                events.append(
                    {
                        "type": "sleeping_choice",
                        "pid": pid,
                        "action": "pass",
                        "reason": "invalid_index",
                    }
                )

        else:  # pass
            events.append({"type": "sleeping_choice", "pid": pid, "action": "pass"})

        if pid in self._sleeping_pending:
            self._sleeping_pending.remove(pid)

        # If all players have acted, move to PICK_CARDS (forced-wake runs inside _start_pick_phase)
        if not self._sleeping_pending:
            if not self._pending_forced_wake_pid:
                events += self._start_pick_phase()

        return events

    def _force_wake_if_needed(self) -> List[dict]:
        """
        After sleeping phase: any player with fewer hand cards than declared_steps
        must wake sleeping pairs until they have enough (or exhaust all pairs).

        - AI: selects which pairs to wake by strategy via ai_forced_wake_choice()
        - Human: emits forced_wake_choose event and pauses; resumes via forced_wake_chosen()
        """
        events = []
        for p in self.players.values():
            if p.out or p.skip_next:
                continue
            shortfall = self.declared_steps - len(p.hand)
            if shortfall <= 0 or not p.sleeping:
                continue

            # How many pairs must be woken (each pair adds 2 cards)
            pairs_needed = 0
            sim_hand = len(p.hand)
            for _ in p.sleeping:
                if sim_hand >= self.declared_steps:
                    break
                sim_hand += 2
                pairs_needed += 1

            if p.is_ai:
                indices = ai_forced_wake_choice(p, pairs_needed, self.declared_el)
                woken = []
                for idx in sorted(indices, reverse=True):  # high→low so pops are safe
                    t, d = p.sleeping.pop(idx)
                    p.hand.append(t)
                    p.hand.append(d)
                    woken.append({"tamer": t.to_dict(), "dragon": d.to_dict()})
                    self._log(
                        f"⏰ {p.name} forced to wake {d.suit} pair "
                        f"(strategy: {p.ai_strategy})"
                    )
                events.append(
                    {
                        "type": "forced_wake",
                        "pid": p.pid,
                        "pairs_woken": woken,
                        "hand_size": len(p.hand),
                        "steps_needed": self.declared_steps,
                    }
                )
            else:
                # Human: build suggested_default using Balanced logic
                suggested = ai_forced_wake_choice(
                    PlayerState(
                        pid=p.pid,
                        name=p.name,
                        is_ai=True,
                        ai_strategy="Balanced",
                        sleeping=list(p.sleeping),
                        hand=list(p.hand),
                    ),
                    pairs_needed,
                    self.declared_el,
                )
                self._pending_forced_wake_pid = p.pid
                self._pending_forced_wake_needed = pairs_needed
                events.append(
                    {
                        "type": "forced_wake_choose",
                        "pid": p.pid,
                        "pairs_available": [
                            {"index": i, "tamer": t.to_dict(), "dragon": d.to_dict()}
                            for i, (t, d) in enumerate(p.sleeping)
                        ],
                        "pairs_needed": pairs_needed,
                        "suggested_default": suggested,
                        "steps_needed": self.declared_steps,
                        "hand_size": len(p.hand),
                    }
                )
                # Stop here — game pauses until forced_wake_chosen() is called
                return events

        return events

    def forced_wake_chosen(self, pid: str, pair_indices: List[int]) -> List[dict]:
        """
        Called by frontend when human player selects which pairs to wake.
        pair_indices: list of sleeping[] indices to wake (length must == pairs_needed).
        If the server timer expired, call this with suggested_default indices.
        """
        if self._pending_forced_wake_pid != pid:
            return [{"type": "error", "msg": "No pending forced wake for this player"}]

        p = self.players[pid]
        needed = self._pending_forced_wake_needed

        # Validate
        if len(pair_indices) != needed:
            return [
                {
                    "type": "error",
                    "msg": f"Must choose exactly {needed} pair(s), got {len(pair_indices)}",
                }
            ]
        for idx in pair_indices:
            if idx < 0 or idx >= len(p.sleeping):
                return [{"type": "error", "msg": f"Invalid pair index {idx}"}]

        self._pending_forced_wake_pid = None
        self._pending_forced_wake_needed = 0

        # Wake chosen pairs (high→low index so pops are safe)
        woken = []
        for idx in sorted(pair_indices, reverse=True):
            t, d = p.sleeping.pop(idx)
            p.hand.append(t)
            p.hand.append(d)
            woken.append({"tamer": t.to_dict(), "dragon": d.to_dict()})
            self._log(f"⏰ {p.name} woke {d.suit} pair (forced, player chose)")

        events = [
            {
                "type": "forced_wake",
                "pid": pid,
                "pairs_woken": woken,
                "hand_size": len(p.hand),
                "steps_needed": self.declared_steps,
            }
        ]

        # Check if any other players also need forced wake (multi-human games)
        events += self._force_wake_if_needed()

        # If no more pending forced wakes, advance to pick phase
        if not self._pending_forced_wake_pid:
            events += self._start_pick_phase()

        return events

    def _start_pick_phase(self) -> List[dict]:
        self.assert_card_integrity(f"round={self.round} pick_phase_start")
        self.phase = Phase.PICK_CARDS
        events = [
            {
                "type": "phase_change",
                "phase": Phase.PICK_CARDS,
                "steps_needed": self.declared_steps,
            }
        ]
        events += self._send_hands()
        # Force-wake BEFORE AI picks so woken cards are available immediately
        fw_events = self._force_wake_if_needed()
        events += fw_events
        # Only proceed to pick if no human forced-wake is pending
        if not self._pending_forced_wake_pid:
            events += self._ai_pick_all()
        return events

    def player_pick_cards(self, pid: str, card_cids: List[int]) -> List[dict]:
        if self.phase != Phase.PICK_CARDS:
            return [{"type": "error", "msg": "Not in pick phase"}]
        p = self.players.get(pid)
        if not p or p.out:
            return [{"type": "error", "msg": "Invalid player"}]

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

        # Guard: deduplicate cids (defensive — AI should never send dupes)
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

        events = [{"type": "cards_picked", "pid": pid, "count": n}]

        if self._all_picked():
            events += self._start_reveal()

        return events

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
            rev_idx = len(p.battle) - 1 - si
            if rev_idx >= 0:
                card = p.battle[rev_idx]
                if card.cid not in self._claimed_cids:
                    entries.append({"pid": p.pid, "card": card})

        if not entries:
            return self._end_round()

        # ── Portal: fires when 9♣ is the current step's card ──
        # The portal card (9♣) is already in entries as the portal owner's step card.
        # The stolen card is added alongside it — both compete in this step.
        # Winner takes all: portal card + stolen card + all other players' step cards.
        # Resolution uses the BEST effective_rank among the portal owner's two cards.
        portal_entry = next((e for e in entries if e["card"].is_portal), None)
        if portal_entry:
            portal_pid = portal_entry["pid"]
            portal_player = self.players[portal_pid]
            targets = [p for p in active if p.pid != portal_pid and p.hand]
            valid_targets = []
            for t in targets:
                sleeping_cids = {c.cid for pair in t.sleeping for c in pair}
                if any(c.cid not in sleeping_cids for c in t.hand):
                    valid_targets.append(t)

            if valid_targets:
                if not portal_player.is_ai:
                    self._pending_portal_pid = portal_pid
                    self._pending_portal_entries = entries
                    return [
                        {
                            "type": "portal_choose_target",
                            "portal_pid": portal_pid,
                            "step": si + 1,
                            "valid_target_pids": [t.pid for t in valid_targets],
                        }
                    ]
                else:
                    chosen_target = ai_portal_target(portal_player, valid_targets)
                    events = self._execute_portal_steal(
                        portal_pid, chosen_target.pid, entries, si
                    )
                    return events

        return self._resolve_and_finish_step(entries, si)

    def portal_target_chosen(self, pid: str, target_pid: str) -> List[dict]:
        """Called by the frontend when the human Portal player picks a target."""
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
        """
        Called when a human princess casts their Love Power vote.
        Collects all votes then resolves by majority.
        """
        if princess_pid not in self._pending_love_princess_pids:
            return [
                {"type": "error", "msg": "No pending Love Power vote for this princess"}
            ]
        if chosen_tamer_pid not in self._pending_love_tamer_pids:
            return [{"type": "error", "msg": "Invalid tamer choice"}]

        # Record this vote and remove from pending
        self._pending_love_votes[princess_pid] = chosen_tamer_pid
        self._pending_love_princess_pids.remove(princess_pid)

        # Check if more human princesses still need to vote
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

        # All human votes in — collect AI votes
        for ai_pid in list(self._pending_love_princess_pids):
            if self.players[ai_pid].is_ai:
                vote = ai_love_tamer_choice(
                    self.players[ai_pid], self._pending_love_tamer_pids, self.players
                )
                self._pending_love_votes[ai_pid] = vote

        return self._finalize_love_vote()

    def _finalize_love_vote(self) -> List[dict]:
        """Count votes, determine winner by majority, then finish the step."""
        from collections import Counter

        result = self._pending_love_step_result
        entries = self._pending_love_entries
        si = self._pending_love_si
        votes = self._pending_love_votes
        tamer_pids = self._pending_love_tamer_pids

        # Count votes
        counts = Counter(votes.values())
        if counts:
            top_count = counts.most_common(1)[0][1]
            leaders = [t for t, c in counts.items() if c == top_count]
            if len(leaders) == 1:
                winner_tamer = leaders[0]
                result["special_events"].append(
                    f"💕 Love Power vote result: "
                    f"{', '.join(self.players[p].name + ' → ' + self.players[t].name for p, t in votes.items())} "
                    f"— {self.players[winner_tamer].name} wins!"
                )
                result["love_right_pid"] = winner_tamer
            else:
                result["special_events"].append(
                    f"💕 Love Power tied — power cancelled! "
                    f"({', '.join(self.players[p].name + ' → ' + self.players[t].name for p, t in votes.items())})"
                )
                result["love_right_pid"] = None
        else:
            result["love_right_pid"] = None

        # Reset all pending love state
        self._pending_love_princess_pids = []
        self._pending_love_tamer_pids = []
        self._pending_love_step_result = None
        self._pending_love_entries = []
        self._pending_love_si = 0
        self._pending_love_votes = {}

        if result["love_right_pid"]:
            self.love_right = result["love_right_pid"]

        return self._finish_step_after_resolve(result, entries, si)

    def _execute_portal_steal(
        self, portal_pid: str, target_pid: str, entries: List[dict], si: int
    ) -> List[dict]:
        """
        Steal a blind card from target.
        The stolen card joins THIS step alongside the portal card (9♣).
        Both belong to the portal owner — the best of the two determines their
        rank in resolution. Both stay in the pot; winner takes all.
        """
        target = self.players[target_pid]
        sleeping_cids = {c.cid for pair in target.sleeping for c in pair}
        stealable = [c for c in target.hand if c.cid not in sleeping_cids]

        steal_events = []
        if stealable:
            # Steal the leftmost card as shown in the target's hand display.
            # Frontend sorts: rank ascending (2→A), then suit Hearts<Diamonds<Clubs<Spades.
            # Jokers (orig_rank 14) sort last. Replicate that here so "first card" is consistent.
            _SUIT_DISPLAY_ORDER = {"Hearts": 0, "Diamonds": 1, "Clubs": 2, "Spades": 3}
            stealable_sorted = sorted(
                stealable,
                key=lambda c: (
                    99 if c.is_joker else c.orig_rank,
                    _SUIT_DISPLAY_ORDER.get(c.suit, 4),
                ),
            )
            stolen = stealable_sorted[0]
            target.hand.remove(stolen)
            # Add stolen card to entries alongside the portal card.
            # Both entries have portal_pid — resolution sees two cards for this player.
            # resolve_step will use the best-ranked of the two for comparison.
            entries = list(entries) + [
                {"pid": portal_pid, "card": stolen, "stolen": True}
            ]
            self._log(
                f"🌀 {self.players[portal_pid].name} stole {stolen.label} "
                f"from {target.name} — both cards compete in this step!"
            )
            steal_events.append(
                {
                    "type": "portal_steal",
                    "portal_pid": portal_pid,
                    "target_pid": target_pid,
                    "stolen_card": stolen.to_dict(),
                }
            )

        return steal_events + self._resolve_and_finish_step(entries, si)

    def _resolve_and_finish_step(self, entries: List[dict], si: int) -> List[dict]:
        """Run resolve_step on final entries and handle all post-resolution logic."""
        # Entries are views into p.battle (already counted there) EXCEPT stolen cards
        # which were removed from target.hand before being appended to entries.
        # So stolen cards are NOT in any player location — pass them as extra.
        stolen_entries = [e for e in entries if e.get("stolen")]
        self.assert_card_integrity(
            f"round={self.round} step={si + 1} pre-resolve",
            stolen_entries if stolen_entries else None,
        )
        result = resolve_step(
            entries, self.declared_el, self._lead_pid(), all_players=self.players
        )

        # ── Love Power majority voting ──
        if result.get("love_choice_needed"):
            lcd = result["love_choice_needed"]
            princess_pids = lcd["princess_pids"]
            tamer_pids = lcd["tamer_pids"]

            human_princesses = [
                pid for pid in princess_pids if not self.players[pid].is_ai
            ]
            ai_princesses = [pid for pid in princess_pids if self.players[pid].is_ai]

            # Pre-collect all AI votes immediately
            ai_votes = {}
            for ai_pid in ai_princesses:
                vote = ai_love_tamer_choice(
                    self.players[ai_pid], tamer_pids, self.players
                )
                ai_votes[ai_pid] = vote

            if human_princesses:
                # Set up pending state — humans vote one by one
                self._pending_love_princess_pids = list(human_princesses)
                self._pending_love_tamer_pids = tamer_pids
                self._pending_love_step_result = result
                self._pending_love_entries = entries
                self._pending_love_si = si
                self._pending_love_votes = ai_votes  # AI votes already in

                for msg in result["special_events"]:
                    self._log(msg)

                total = len(princess_pids)
                return [
                    {
                        "type": "love_choose_tamer",
                        "princess_pid": human_princesses[0],
                        "tamer_pids": tamer_pids,
                        "votes_cast": len(ai_votes),
                        "votes_total": total,
                        "step": si + 1,
                        "entries": [
                            {
                                "pid": e["pid"],
                                "card": e["card"].to_dict(),
                                "stolen": e.get("stolen", False),
                            }
                            for e in entries
                        ],
                        "special_events": result["special_events"],
                    }
                ]
            else:
                # All princesses are AI — resolve immediately by majority
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

    def _finish_step_after_resolve(
        self, result: dict, entries: List[dict], si: int
    ) -> List[dict]:
        """Apply result to game state and advance the step. Shared by normal and paused flows."""
        winner_pid = result["winner_pid"]
        all_cards = result["all_cards"]  # includes stolen card if portal fired

        # Time dragon logic — fires if Time Dragon is in step AND winner holds it or beat it with Tamer
        time_j = next((e for e in entries if e["card"].joker_type == "time"), None)
        has_tamer_winner = time_j and any(
            e["pid"] == winner_pid and e["card"].is_tamer for e in entries
        )

        time_owner_pid = None
        if time_j:
            if winner_pid == time_j["pid"]:
                # Time Dragon card owner won directly
                time_owner_pid = winner_pid
            elif has_tamer_winner:
                # A Tamer beat the Time Dragon — Tamer winner gets the power
                time_owner_pid = winner_pid
        # else: Time Dragon is in step but its owner lost and no Tamer won — power forfeit

        if time_owner_pid:
            time_player = self.players[time_owner_pid]
            # Check if Space Dragon is ALSO active for the same player
            space_pid = result.get("space_dragon_pid")
            both_jokers = space_pid and space_pid == time_owner_pid

            if both_jokers:
                # Player won BOTH Jokers — must choose which power to use (or neither)
                if not time_player.is_ai:
                    # Pause for human choice
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
                        "entries": [
                            {
                                "pid": e["pid"],
                                "card": e["card"].to_dict(),
                                "stolen": e.get("stolen", False),
                                "duel_cards": [
                                    c.to_dict()
                                    for c in result["duel_draws"].get(e["pid"], [])
                                ],
                            }
                            for e in entries
                        ],
                        "winner_pid": winner_pid,
                        "special_events": result["special_events"],
                        "love_right_pid": result["love_right_pid"],
                    }
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
                    # AI chooses: prefer time if prev step has cards, else space
                    time_choice = ai_time_dragon_choice(
                        time_player, self.prev_step_cards, self.prev_step_winner
                    )
                    chosen_power = (
                        "time" if time_choice in ("back", "forward") else "space"
                    )
                    result["special_events"].append(
                        f"🃏 {time_player.name} chooses {chosen_power} Dragon power (AI)!"
                    )
                    # Apply chosen power, suppress the other
                    if chosen_power == "time":
                        result["space_dragon_pid"] = None  # suppress space
                    else:
                        time_owner_pid = None  # suppress time, handle space below
            else:
                # Only Time Dragon — apply normally
                if not time_player.is_ai:
                    # Pause for human Time Dragon choice
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
                    self.prev_step_cards = all_cards
                    self.prev_step_winner = winner_pid
                    self.current_step += 1
                    step_event = {
                        "type": "step_revealed",
                        "step": si + 1,
                        "total_steps": self.declared_steps,
                        "entries": [
                            {
                                "pid": e["pid"],
                                "card": e["card"].to_dict(),
                                "stolen": e.get("stolen", False),
                                "duel_cards": [
                                    c.to_dict()
                                    for c in result["duel_draws"].get(e["pid"], [])
                                ],
                            }
                            for e in entries
                        ],
                        "winner_pid": winner_pid,
                        "special_events": result["special_events"],
                        "love_right_pid": result["love_right_pid"],
                    }
                    return [
                        step_event,
                        {
                            "type": "time_dragon_choose",
                            "pid": time_owner_pid,
                            "has_prev": bool(
                                self.prev_step_winner and self.prev_step_cards
                            ),
                            "step": si + 1,
                        },
                    ]

        # Apply Time Dragon choice for AI (or after both-joker AI resolution above)
        if time_owner_pid and self.players[time_owner_pid].is_ai:
            time_player = self.players[time_owner_pid]
            time_choice = ai_time_dragon_choice(
                time_player, self.prev_step_cards, self.prev_step_winner
            )
            self._apply_time_dragon(time_owner_pid, time_choice, result)

        if winner_pid:
            won_cids = {c.cid for c in all_cards}
            self.players[winner_pid].accum += all_cards
            # Track claimed cids — do NOT remove from battle mid-round.
            # battle list must stay fixed-length so rev_idx keeps working for remaining steps.
            # Cards are physically cleared from battle in _end_round.
            self._claimed_cids.update(won_cids)

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
            "entries": [
                {
                    "pid": e["pid"],
                    "card": e["card"].to_dict(),
                    "stolen": e.get("stolen", False),
                    "duel_cards": [
                        c.to_dict() for c in result["duel_draws"].get(e["pid"], [])
                    ],
                }
                for e in entries
            ],
            "winner_pid": winner_pid,
            "special_events": result["special_events"],
            "love_right_pid": result["love_right_pid"],
        }

        # ── Space Dragon: pause immediately for seat-swap choice ──
        space_pid = result.get("space_dragon_pid")
        if space_pid:
            active = [p for p in self.players.values() if not p.out]
            valid_targets = [p.pid for p in active if p.pid != space_pid]
            space_player = self.players[space_pid]

            if not space_player.is_ai:
                # Pause for human choice
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
                # AI decides and applies immediately
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

        # Advance: next step or end round
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

    def _apply_time_dragon(self, pid: str, choice: str, result: dict):
        """Apply a Time Dragon choice ('back'/'forward'/'nothing') to game state."""
        p = self.players[pid]
        if choice == "back" and self.prev_step_winner and self.prev_step_cards:
            pw = self.players[self.prev_step_winner]
            for c in self.prev_step_cards:
                if c in pw.accum:
                    pw.accum.remove(c)
            p.accum += self.prev_step_cards
            result["special_events"].append(f"⏳ {p.name} claims previous step!")
        elif choice == "forward":
            p.skip_next = True
            result["special_events"].append(f"⏳ {p.name} skips next round.")
        else:
            result["special_events"].append(f"⏳ {p.name} passes Time Dragon power.")

    def joker_power_chosen(self, pid: str, power: str) -> List[dict]:
        """
        Called when human player chooses which Joker power to use after winning both.
        power: 'time' | 'space' | 'nothing'
        """
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
            # Show Time Dragon choice to human
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
            # Apply Space Dragon — reuse existing swap flow
            active = [p for p in self.players.values() if not p.out]
            valid_targets = [p.pid for p in active if p.pid != pid]
            self._pending_space_dragon_pid = pid
            self._pending_space_dragon_entries = entries
            self._pending_space_dragon_si = si - 1  # already incremented
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
            # Nothing — advance normally
            events += self._advance_after_step(si)

        return events

    def time_dragon_chosen(self, pid: str, choice: str) -> List[dict]:
        """
        Called when human Time Dragon winner makes their choice.
        choice: 'back' | 'forward' | 'nothing'
        """
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

    def _advance_after_step(self, si: int) -> List[dict]:
        """Emit phase_change or end_round depending on whether more steps remain."""
        if self.current_step >= self.declared_steps:
            return self._end_round()
        return [
            {
                "type": "phase_change",
                "phase": Phase.REVEAL,
                "next_step": self.current_step + 1,
            }
        ]

    def _apply_seat_swap(self, pid_a: str, pid_b: str) -> List[dict]:
        """Swap the seat positions of two players in self.order."""
        if pid_a not in self.order or pid_b not in self.order:
            return [{"type": "error", "msg": "Invalid swap pids"}]
        ia, ib = self.order.index(pid_a), self.order.index(pid_b)
        self.order[ia], self.order[ib] = self.order[ib], self.order[ia]
        # Update lead_idx if either swapped player is the current leader
        lead = self._lead_pid()  # recalculate after swap
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

    def space_dragon_swap_chosen(
        self, pid: str, target_pid: Optional[str]
    ) -> List[dict]:
        """
        Called by frontend when human Space Dragon winner chooses a swap target.
        target_pid=None means pass (stay in current seat).
        """
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

    def _end_round(self) -> List[dict]:
        # NOTE: skip_next is NOT reset here — it must survive into the next round
        # and be cleared at the START of that round (_start_sleeping_phase).
        # Resetting here was Bug #20.

        # Return cards — unclaimed battle cards back to hand, clear battle and accum
        for p in self.players.values():
            if p.out:
                continue
            sleeping_cids = {c.cid for pair in p.sleeping for c in pair}
            existing = {c.cid for c in p.hand} | sleeping_cids
            for c in p.battle:
                # unclaimed battle cards return to hand; claimed ones are already in accum
                if c.cid not in existing and c.cid not in self._claimed_cids:
                    p.hand.append(c)
                    existing.add(c.cid)
            for c in p.accum:
                if c.cid not in existing:
                    p.hand.append(c)
                    existing.add(c.cid)
            p.battle = []
            p.accum = []

        self._claimed_cids = set()  # reset for next round

        # (Sleeping pairs are now managed by player choice at round start, not auto-applied here)
        self.assert_card_integrity(f"round={self.round} end_round post-return")

        # ── Fix #15: Victory check BEFORE elimination ──
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

        # Elimination
        eliminated = []
        for p in self.players.values():
            if not p.out and not p.hand and not p.sleeping:
                p.out = True
                eliminated.append(p.pid)
                self._log(f"💀 {p.name} is eliminated!")

        # Victory check after elimination
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

        # ── Stalemate safeguard ──
        if self.round >= MAX_ROUNDS:
            active_players = [p for p in self.players.values() if not p.out]
            winner = max(active_players, key=lambda p: (p.dragon_count, len(p.hand)))
            self.phase = Phase.GAME_OVER
            self._capture_final_snapshot()
            self._log(f"⏱️ Round limit reached — {winner.name} wins by dragon count!")
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

        # Next leader
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

        if self.love_right and not self.players[self.love_right].out:
            self.lead_idx = self.order.index(self.love_right)
        else:
            self.lead_idx = (self.lead_idx + 1) % len(self.order)
            while self.players[self.order[self.lead_idx]].out:
                self.lead_idx = (self.lead_idx + 1) % len(self.order)

        self.love_right = None
        self.round += 1
        self.phase = Phase.LEADER_DECLARE
        self.prev_step_cards = []
        self.prev_step_winner = None

        events = [
            {"type": "eliminated", "pids": eliminated},
            {"type": "round_end", "round": self.round - 1},
            {
                "type": "phase_change",
                "phase": Phase.LEADER_DECLARE,
                "leader_pid": self._lead_pid(),
                "round": self.round,
            },
        ]
        events += self._send_hands()

        if self.players[self._lead_pid()].is_ai:
            events += self._ai_declare()

        return events

    def _capture_final_snapshot(self):
        """Store a snapshot of all card locations at the moment game ends."""
        self.final_snapshot = {}
        for p in self.players.values():
            unclaimed_battle = [c for c in p.battle if c.cid not in self._claimed_cids]
            self.final_snapshot[p.pid] = {
                "hand": list(p.hand),
                "battle": unclaimed_battle,
                "accum": list(p.accum),
                "sleeping": list(p.sleeping),
            }

    def _game_over_payload(self, winner_pid: str) -> dict:
        """Build all_players and all_dragons fields for game_over events."""
        all_players = []
        for pid, p in self.players.items():
            snap = self.final_snapshot.get(pid, {})
            hand = snap.get("hand", list(p.hand))
            sleeping = snap.get("sleeping", list(p.sleeping))
            all_players.append(
                {
                    "pid": pid,
                    "name": p.name,
                    "dragons": p.dragon_count,
                    "out": p.out,
                    "hand": [c.to_dict() for c in hand],
                    "sleeping": [[t.to_dict(), d.to_dict()] for t, d in sleeping],
                }
            )
        winner_snap = self.final_snapshot.get(winner_pid, {})
        w_hand = winner_snap.get(
            "hand",
            list(self.players[winner_pid].hand) if winner_pid in self.players else [],
        )
        w_sleeping = winner_snap.get(
            "sleeping",
            list(self.players[winner_pid].sleeping)
            if winner_pid in self.players
            else [],
        )
        all_dragons = [c.to_dict() for c in w_hand if c.is_dragon] + [
            d.to_dict() for _, d in w_sleeping
        ]
        return {"all_players": all_players, "all_dragons": all_dragons}

    def _lead_pid(self) -> str:
        return self.order[self.lead_idx % len(self.order)]

    def assert_card_integrity(self, context: str = "", entries: List[dict] = None):
        """
        Assert all 54 cards appear exactly once across every location.
        Note: during mid-round, won cards sit in both battle (for rev_idx) and accum.
        _claimed_cids tracks these; we count them only from accum, skip in battle.
        Logs errors instead of crashing in production.
        """
        try:
            seen: Dict[int, str] = {}

            def register(cid: int, location: str):
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
                for i, pair in enumerate(p.sleeping):
                    for c in pair:
                        register(c.cid, f"{label}.sleeping[{i}]")

            if entries:
                for e in entries:
                    c = e["card"]
                    register(c.cid, f"entries[pid={e['pid']}]")

            total = len(seen)
            if total != 54:
                all_expected = set(range(1, 55))
                missing = all_expected - set(seen.keys())
                raise AssertionError(
                    f"[{context}] WRONG card count: expected 54, got {total}. "
                    f"Missing cids: {sorted(missing)}"
                )
        except AssertionError as e:
            self._log(f"⚠️ Card integrity warning: {e}")

    def _all_picked(self) -> bool:
        active = [
            p
            for p in self.players.values()
            if not p.out and not p.skip_next and p.pid not in self._skipped_this_round
        ]
        for p in active:
            if p.battle:
                continue  # submitted cards — done
            if not p.hand:
                continue  # no cards to submit — contributes nothing this round
            return False  # has cards but hasn't picked yet (human or AI mid-loop)
        return True

    def _start_reveal(self) -> List[dict]:
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

    def _send_hands(self) -> List[dict]:
        return [
            {
                "type": "hand_update",
                "pid": pid,
                "hand": [c.to_dict() for c in p.hand],
                "sleeping": [[t.to_dict(), d.to_dict()] for t, d in p.sleeping],
                "dragon_count": p.dragon_count,
            }
            for pid, p in self.players.items()
        ]

    def _ai_declare(self) -> List[dict]:
        lead = self.players[self._lead_pid()]
        steps, el = ai_declare(lead)
        return self.player_declare(lead.pid, steps, el)

    def _ai_pick_all(self) -> List[dict]:
        events = []
        # Pick for ALL eligible AI players first, without triggering _all_picked mid-loop
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
                chosen = ai_pick_cards(p, n, self.declared_el)
                picks.append((p, chosen))

        # Apply all picks atomically
        for p, chosen in picks:
            cids = [c.cid for c in chosen]
            # Deduplicate
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
            events.append(
                {"type": "cards_picked", "pid": p.pid, "count": len(valid_cids)}
            )

        # Now check if everyone is ready
        if self._all_picked():
            events += self._start_reveal()

        return events

    def _log(self, msg: str):
        self.event_log.append(msg)
        if len(self.event_log) > 200:
            self.event_log.pop(0)

    def public_state(self) -> dict:
        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "round": self.round,
            "lead_pid": self._lead_pid() if self.order else None,
            "declared_steps": self.declared_steps,
            "declared_el": self.declared_el,
            "declared_el_name": SUIT_ELEMENT.get(self.declared_el, ""),
            "current_step": self.current_step,
            "players": {pid: p.public_dict() for pid, p in self.players.items()},
            "order": self.order,
            "log": self.event_log[-20:],
        }

    def player_state(self, pid: str) -> dict:
        state = self.public_state()
        if pid in self.players:
            state["me"] = self.players[pid].private_dict()
        return state


class RoomManager:
    def __init__(self):
        self.rooms: Dict[str, DragonTamerGame] = {}

    def create_room(self, room_id: str, max_players: int = 10) -> DragonTamerGame:
        game = DragonTamerGame(room_id, max_players)
        self.rooms[room_id] = game
        return game

    def get_room(self, room_id: str) -> Optional[DragonTamerGame]:
        return self.rooms.get(room_id)

    def delete_room(self, room_id: str):
        self.rooms.pop(room_id, None)

    def list_rooms(self) -> List[dict]:
        return [
            {
                "room_id": r.room_id,
                "players": len(r.players),
                "max_players": r.max_players,
                "phase": r.phase,
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
    print("\n✅ Engine v1.1 test passed")
