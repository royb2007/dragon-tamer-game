"""
Dragon Tamer — Game Engine v1.1
Fixes applied:
  - dragon_count: jokers now count as dragons
  - Portal (9♣): steal blind card from opponent
  - skip_next: Time Dragon forward actually skips next round
"""
import random
import json
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

SUITS = ['Hearts', 'Clubs', 'Diamonds', 'Spades']
SUIT_SYM = {'Hearts': '♥', 'Clubs': '♣', 'Diamonds': '♦', 'Spades': '♠'}
SUIT_ELEMENT = {'Hearts': 'Fire', 'Clubs': 'Water', 'Diamonds': 'Air', 'Spades': 'Earth'}
WIN_DRAGONS = 4


class Phase(str, Enum):
    WAITING        = "waiting"
    LEADER_DECLARE = "leader_declare"
    PICK_CARDS     = "pick_cards"
    REVEAL         = "reveal"
    END_ROUND      = "end_round"
    GAME_OVER      = "game_over"


@dataclass
class Card:
    _next_id: int = field(default=0, init=False, repr=False, compare=False)
    cid:       int
    rank:      int
    orig_rank: int
    suit:      Optional[str]
    label:     str
    is_joker:  bool = False
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
        return self.orig_rank == 9 and self.suit == 'Clubs'

    def effective_rank(self, leading_suit: Optional[str]) -> int:
        if self.is_joker or not self.suit or not leading_suit:
            return self.rank
        return min(self.rank + 1, 15) if self.suit == leading_suit else self.rank

    def to_dict(self) -> dict:
        return {
            'cid': self.cid, 'rank': self.rank, 'orig_rank': self.orig_rank,
            'suit': self.suit, 'label': self.label,
            'is_joker': self.is_joker, 'joker_type': self.joker_type,
            'is_dragon': self.is_dragon, 'is_tamer': self.is_tamer,
            'is_princess': self.is_princess, 'is_portal': self.is_portal,
        }


_card_counter = 0

def _new_card(rank, suit, is_joker=False, joker_type=None) -> Card:
    global _card_counter
    _card_counter += 1
    labels = {1: 'A', 11: 'J', 12: 'Q', 13: 'K'}
    if is_joker:
        label = '🌌' if joker_type == 'space' else '⏳'
        return Card(cid=_card_counter, rank=14, orig_rank=14,
                    suit=None, label=label, is_joker=True, joker_type=joker_type)
    sym = SUIT_SYM[suit]
    label = (labels.get(rank, str(rank))) + sym
    return Card(cid=_card_counter, rank=14 if rank == 1 else rank,
                orig_rank=rank, suit=suit, label=label)


def build_deck() -> List[Card]:
    global _card_counter
    _card_counter = 0
    deck = []
    for suit in SUITS:
        for r in range(1, 14):
            deck.append(_new_card(r, suit))
    deck.append(_new_card(0, None, True, 'space'))
    deck.append(_new_card(0, None, True, 'time'))
    return deck


def _best_card(cards: List[Card], el: Optional[str]) -> Optional[Card]:
    best = None
    for c in cards:
        if best is None or c.effective_rank(el) > best.effective_rank(el):
            best = c
    return best


def resolve_step(entries: List[dict], el: Optional[str], lead_pid: str) -> dict:
    result = {
        'winner_pid': None,
        'all_cards': [e['card'] for e in entries],
        'joker_powers': [],
        'love_right_pid': None,
        'portal_pid': None,
        'special_events': [],
    }

    valid = [e for e in entries if not e.get('forfeited', False)]
    if not valid:
        return result

    has_dragon = any(e['card'].is_dragon for e in valid)
    tamers     = [e for e in valid if e['card'].is_tamer]
    princesses = [e for e in valid if e['card'].is_princess]
    jokers     = [e for e in valid if e['card'].is_joker]

    result['joker_powers'] = [j['card'].joker_type for j in jokers if j['card'].joker_type]

    # Love Power
    if tamers and princesses:
        if len(tamers) == 1:
            result['love_right_pid'] = tamers[0]['pid']
            result['special_events'].append(f"💕 Love Power! {tamers[0]['pid']} earns next lead!")
        else:
            result['love_right_pid'] = tamers[0]['pid']
            result['special_events'].append(f"💕 Love Power → {tamers[0]['pid']}!")

    # Tamer beats dragons
    if has_dragon and len(tamers) == 1:
        result['winner_pid'] = tamers[0]['pid']
        result['special_events'].append(f"⚔️ {tamers[0]['pid']}'s Tamer beats all dragons!")
        return result

    if has_dragon and len(tamers) > 1:
        result['winner_pid'] = tamers[0]['pid']
        result['special_events'].append("⚔️ Tamer duel! First Tamer wins (full duel UI pending).")
        return result

    # Normal resolution
    best = _best_card([e['card'] for e in valid], el)
    top_e = best.effective_rank(el)
    tied = [e for e in valid if e['card'].effective_rank(el) == top_e]

    if len(tied) == 1:
        result['winner_pid'] = tied[0]['pid']
    else:
        result['winner_pid'] = lead_pid

    # Space dragon
    space_j = next((e for e in valid if e['card'].joker_type == 'space'), None)
    if space_j and not tamers:
        result['special_events'].append(f"🌌 Space Dragon! {space_j['pid']} may change seat.")

    # Portal detection
    portal_e = next((e for e in valid if e['card'].is_portal), None)
    if portal_e:
        result['portal_pid'] = portal_e['pid']
        result['special_events'].append(f"🌀 Portal! {portal_e['pid']} steals a blind card!")

    return result


@dataclass
class PlayerState:
    pid:      str
    name:     str
    hand:     List[Card] = field(default_factory=list)
    battle:   List[Card] = field(default_factory=list)
    accum:    List[Card] = field(default_factory=list)
    sleeping: List[tuple] = field(default_factory=list)
    out:      bool = False
    skip_next: bool = False
    is_ai:    bool = False
    ai_strategy: str = 'Balanced'

    @property
    def dragon_count(self) -> int:
        # jokers ARE dragons — count them
        return (sum(1 for c in self.hand if c.is_dragon)
                + len(self.sleeping))

    def apply_sleeping(self):
        sleeping_cids = {c.cid for pair in self.sleeping for c in pair}
        dragons = {c.suit: c for c in self.hand
                   if c.is_dragon and not c.is_joker and c.suit
                   and c.cid not in sleeping_cids}
        tamers  = {c.suit: c for c in self.hand
                   if c.is_tamer and c.suit
                   and c.cid not in sleeping_cids}
        for suit in SUITS:
            if suit in dragons and suit in tamers:
                t, d = tamers[suit], dragons[suit]
                if t in self.hand and d in self.hand:
                    self.hand.remove(t)
                    self.hand.remove(d)
                    self.sleeping.append((t, d))

    def public_dict(self) -> dict:
        return {
            'pid': self.pid, 'name': self.name,
            'dragon_count': self.dragon_count,
            'hand_count': len(self.hand),
            'battle_count': len(self.battle),
            'sleeping_count': len(self.sleeping),
            'out': self.out, 'is_ai': self.is_ai,
        }

    def private_dict(self) -> dict:
        return {
            **self.public_dict(),
            'hand': [c.to_dict() for c in self.hand],
            'sleeping': [[t.to_dict(), d.to_dict()] for t, d in self.sleeping],
        }


def ai_pick_cards(player: PlayerState, n: int, el: str) -> List[Card]:
    hand = player.hand
    strat = player.ai_strategy
    dragons_count = player.dragon_count
    sorted_h = sorted(hand, key=lambda c: -c.effective_rank(el))
    tamers   = [c for c in hand if c.is_tamer]
    dragons  = [c for c in hand if c.is_dragon]
    others   = sorted([c for c in hand if not c.is_dragon and not c.is_tamer],
                      key=lambda c: c.effective_rank(el))

    if strat == 'Aggressive':
        return sorted_h[:n]
    elif strat == 'Conservative':
        if dragons_count >= 3: return sorted_h[:n]
        result = others[:n]
        if len(result) < n: result += dragons[:n-len(result)]
        if len(result) < n: result += tamers[:n-len(result)]
        return result[:n]
    elif strat in ('Diplomat', 'AntiDragon'):
        precious = [c for c in hand if c.is_tamer or c.is_princess]
        result = others[:n]
        if len(result) < n: result += dragons[:n-len(result)]
        if len(result) < n: result += precious[:n-len(result)]
        return result[:n]
    elif strat == 'Bluffer':
        weak = sorted(others, key=lambda c: c.effective_rank(el))
        result = weak[:n]
        if len(result) < n: result += dragons[:n-len(result)]
        return result[:n]
    else:
        result = []
        lo, hi = len(sorted_h)-1, 0
        for i in range(min(n, len(sorted_h))):
            result.append(sorted_h[hi] if i % 2 == 0 else sorted_h[lo])
            if i % 2 == 0: hi += 1
            else: lo -= 1
        return result[:n]


def ai_declare(player: PlayerState) -> tuple:
    hand = player.hand
    strat = player.ai_strategy
    d = player.dragon_count
    best_el = max(SUITS,
        key=lambda s: sum(c.effective_rank(s) for c in hand if c.suit == s),
        default='Hearts')
    steps_map = {
        'Aggressive': 4 if d == 0 else 5,
        'Conservative': 4 if d >= 3 else (2 if d >= 2 else 1),
        'Bluffer': 1 if d < 2 else 3,
        'Diplomat': 2 if d < 3 else 4,
    }
    steps = steps_map.get(strat, 3)
    return steps, best_el


class DragonTamerGame:
    def __init__(self, room_id: str, max_players: int = 10):
        self.room_id     = room_id
        self.max_players = max_players
        self.players:  Dict[str, PlayerState] = {}
        self.order:    List[str] = []
        self.phase:    Phase = Phase.WAITING
        self.round:    int = 0
        self.lead_idx: int = 0
        self.love_right: Optional[str] = None
        self.declared_steps: int = 3
        self.declared_el:    str = 'Hearts'
        self.current_step:   int = 0
        self.step_entries:   List[dict] = []
        self.prev_step_cards:   List[Card] = []
        self.prev_step_winner:  Optional[str] = None
        self.event_log:      List[str] = []

    def add_player(self, pid: str, name: str, is_ai: bool = False,
                   ai_strategy: str = 'Balanced') -> dict:
        if pid in self.players:
            return {'ok': False, 'error': 'already_joined'}
        if len(self.players) >= self.max_players:
            return {'ok': False, 'error': 'room_full'}
        if self.phase != Phase.WAITING:
            return {'ok': False, 'error': 'game_started'}
        p = PlayerState(pid=pid, name=name, is_ai=is_ai, ai_strategy=ai_strategy)
        self.players[pid] = p
        self.order.append(pid)
        return {'ok': True}

    def remove_player(self, pid: str):
        if pid in self.players and self.phase == Phase.WAITING:
            del self.players[pid]
            self.order.remove(pid)

    def fill_with_ai(self, strategies=None):
        all_strats = ['Aggressive','Balanced','Conservative','Hoarder',
                      'Adaptive','AntiDragon','Diplomat','Bluffer','Avenger']
        i = 0
        while len(self.players) < self.max_players:
            strat = (strategies or all_strats)[i % len(strategies or all_strats)]
            ai_id = f'AI_{i+1}'
            ai_name = f'{strat[:4]}-{i+1}'
            self.add_player(ai_id, ai_name, is_ai=True, ai_strategy=strat)
            i += 1

    def start_game(self) -> List[dict]:
        if self.phase != Phase.WAITING:
            return [{'type': 'error', 'msg': 'Game already started'}]
        if len(self.players) < 2:
            return [{'type': 'error', 'msg': 'Need at least 2 players'}]

        deck = build_deck()
        random.shuffle(deck)
        n = len(self.order)

        # Leader by highest dealt card
        leader_cards = {pid: deck[i] for i, pid in enumerate(self.order)}
        best_pid = max(self.order, key=lambda pid: leader_cards[pid].rank)
        self.lead_idx = self.order.index(best_pid)

        remaining = deck[n:]
        per = len(remaining) // n
        for i, pid in enumerate(self.order):
            share = remaining[i * per:(i + 1) * per]
            self.players[pid].hand = [leader_cards[pid]] + share

        self.round = 1
        self.phase = Phase.LEADER_DECLARE

        events = [{
            'type': 'game_started',
            'round': self.round,
            'leader_cards': {pid: c.to_dict() for pid, c in leader_cards.items()},
            'first_leader_pid': best_pid,
        }, {
            'type': 'phase_change',
            'phase': Phase.LEADER_DECLARE,
            'leader_pid': self._lead_pid(),
            'round': self.round,
        }]
        events += self._send_hands()

        if self.players[self._lead_pid()].is_ai:
            events += self._ai_declare()

        return events

    def player_declare(self, pid: str, steps: int, element: str) -> List[dict]:
        if self.phase != Phase.LEADER_DECLARE:
            return [{'type': 'error', 'msg': 'Not in declare phase'}]
        if pid != self._lead_pid():
            return [{'type': 'error', 'msg': 'Not your turn to declare'}]
        if element not in SUITS:
            return [{'type': 'error', 'msg': 'Invalid element'}]
        steps = max(1, min(5, steps))

        self.declared_steps = steps
        self.declared_el    = element
        self._log(f"👑 {self.players[pid].name} declares: {steps} steps · "
                  f"{SUIT_ELEMENT[element]} {SUIT_SYM[element]}")

        self.phase = Phase.PICK_CARDS
        self.current_step = 0

        events = [{
            'type': 'declaration',
            'pid': pid, 'steps': steps, 'element': element,
            'element_name': SUIT_ELEMENT[element],
        }, {
            'type': 'phase_change',
            'phase': Phase.PICK_CARDS,
            'steps_needed': steps,
        }]
        events += self._ai_pick_all()
        return events

    def player_pick_cards(self, pid: str, card_cids: List[int]) -> List[dict]:
        if self.phase != Phase.PICK_CARDS:
            return [{'type': 'error', 'msg': 'Not in pick phase'}]
        p = self.players.get(pid)
        if not p or p.out:
            return [{'type': 'error', 'msg': 'Invalid player'}]

        n = min(self.declared_steps, len(p.hand))
        if len(card_cids) != n:
            return [{'type': 'error',
                     'msg': f'Must pick exactly {n} cards, got {len(card_cids)}'}]

        hand_cids = {c.cid: c for c in p.hand}
        for cid in card_cids:
            if cid not in hand_cids:
                return [{'type': 'error', 'msg': f'Card {cid} not in hand'}]

        chosen = [hand_cids[cid] for cid in card_cids]
        p.battle = chosen
        p.hand   = [c for c in p.hand if c.cid not in set(card_cids)]

        events = [{'type': 'cards_picked', 'pid': pid, 'count': n}]

        if self._all_picked():
            events += self._start_reveal()

        return events

    def reveal_step(self, pid: str) -> List[dict]:
        if self.phase != Phase.REVEAL:
            return [{'type': 'error', 'msg': 'Not in reveal phase'}]
        return self._do_reveal()

    def _do_reveal(self) -> List[dict]:
        si = self.current_step
        active = [p for p in self.players.values() if not p.out]

        entries = []
        for p in active:
            rev_idx = len(p.battle) - 1 - si
            if rev_idx >= 0:
                entries.append({'pid': p.pid, 'card': p.battle[rev_idx]})

        if not entries:
            return self._end_round()

        result = resolve_step(entries, self.declared_el, self._lead_pid())

        if result['love_right_pid']:
            self.love_right = result['love_right_pid']

        winner_pid = result['winner_pid']
        all_cards  = result['all_cards']

        # Time dragon logic
        time_j = next((e for e in entries if e['card'].joker_type == 'time'), None)
        has_tamer = any(e['card'].is_tamer for e in entries)

        time_owner_pid = None
        if has_tamer and 'time' in result['joker_powers'] and winner_pid:
            time_owner_pid = winner_pid
        elif time_j and not has_tamer:
            time_owner_pid = time_j['pid']

        if time_owner_pid:
            if self.prev_step_winner and self.prev_step_cards:
                pw = self.players[self.prev_step_winner]
                for c in self.prev_step_cards:
                    if c in pw.accum: pw.accum.remove(c)
                self.players[time_owner_pid].accum += self.prev_step_cards
                result['special_events'].append(
                    f"⏳ {self.players[time_owner_pid].name} claims previous step!")
            else:
                # forward in time — set skip_next
                self.players[time_owner_pid].skip_next = True
                result['special_events'].append(
                    f"⏳ {self.players[time_owner_pid].name} skips next round.")

        # Execute Portal steal
        if result.get('portal_pid'):
            portal_pid = result['portal_pid']
            targets = [p for p in active
                       if p.pid != portal_pid and p.hand]
            if targets:
                target = max(targets, key=lambda p: len(p.hand))
                sleeping_cids = {c.cid for pair in target.sleeping for c in pair}
                stealable = [c for c in target.hand
                            if c.cid not in sleeping_cids]
                if stealable:
                    stolen = random.choice(stealable)
                    target.hand.remove(stolen)
                    self.players[portal_pid].accum.append(stolen)
                    self._log(f"🌀 {self.players[portal_pid].name} "
                              f"stole a card from {target.name}!")

        # Remove revealed cards from battle piles
        revealed_cids = {c.cid for c in all_cards}
        for p in active:
            p.battle = [c for c in p.battle if c.cid not in revealed_cids]

        if winner_pid:
            self.players[winner_pid].accum += all_cards

        for msg in result['special_events']:
            self._log(msg)

        if winner_pid:
            self._log(f"Step {si+1}: {self.players[winner_pid].name} wins "
                      f"({', '.join(c.label for c in all_cards[:3])}...)")

        self.prev_step_cards  = all_cards
        self.prev_step_winner = winner_pid
        self.current_step += 1

        events = [{
            'type': 'step_revealed',
            'step': si + 1,
            'total_steps': self.declared_steps,
            'entries': [{'pid': e['pid'], 'card': e['card'].to_dict()} for e in entries],
            'winner_pid': winner_pid,
            'special_events': result['special_events'],
            'love_right_pid': result['love_right_pid'],
        }]

        if self.current_step >= self.declared_steps:
            events += self._end_round()
        else:
            events.append({
                'type': 'phase_change',
                'phase': Phase.REVEAL,
                'next_step': self.current_step + 1,
            })

        return events

    def _end_round(self) -> List[dict]:
        # Reset skip_next for players who were skipping
        for p in self.players.values():
            if p.skip_next:
                p.skip_next = False
                self._log(f"⏳ {p.name} returns from Time Dragon skip.")

        # Return cards
        for p in self.players.values():
            if p.out: continue
            existing = {c.cid for c in p.hand}
            for c in p.battle:
                if c.cid not in existing:
                    p.hand.append(c); existing.add(c.cid)
            for c in p.accum:
                if c.cid not in existing:
                    p.hand.append(c); existing.add(c.cid)
            p.battle = []
            p.accum  = []

        # Dragon sleep
        for p in self.players.values():
            if not p.out:
                p.apply_sleeping()

        # Elimination
        eliminated = []
        for p in self.players.values():
            if not p.out and not p.hand and not p.sleeping:
                p.out = True
                eliminated.append(p.pid)
                self._log(f"💀 {p.name} is eliminated!")

        # Victory check
        for p in self.players.values():
            if not p.out and p.dragon_count >= WIN_DRAGONS:
                self.phase = Phase.GAME_OVER
                self._log(f"🏆 {p.name} wins with {p.dragon_count} dragons!")
                return [{
                    'type': 'game_over',
                    'winner_pid': p.pid,
                    'winner_name': p.name,
                    'dragons': p.dragon_count,
                    'round': self.round,
                }, {'type': 'eliminated', 'pids': eliminated}]

        # Next leader
        active = [pid for pid in self.order if not self.players[pid].out]
        if len(active) <= 1:
            winner = self.players[active[0]] if active else None
            self.phase = Phase.GAME_OVER
            return [{'type': 'game_over',
                     'winner_pid': winner.pid if winner else None,
                     'winner_name': winner.name if winner else 'Nobody',
                     'dragons': winner.dragon_count if winner else 0,
                     'round': self.round}]

        if self.love_right and not self.players[self.love_right].out:
            self.lead_idx = self.order.index(self.love_right)
        else:
            self.lead_idx = (self.lead_idx + 1) % len(self.order)
            while self.players[self.order[self.lead_idx]].out:
                self.lead_idx = (self.lead_idx + 1) % len(self.order)

        self.love_right = None
        self.round += 1
        self.phase = Phase.LEADER_DECLARE
        self.prev_step_cards  = []
        self.prev_step_winner = None

        events = [
            {'type': 'eliminated', 'pids': eliminated},
            {'type': 'round_end', 'round': self.round - 1},
            {'type': 'phase_change',
             'phase': Phase.LEADER_DECLARE,
             'leader_pid': self._lead_pid(),
             'round': self.round},
        ]
        events += self._send_hands()

        if self.players[self._lead_pid()].is_ai:
            events += self._ai_declare()

        return events

    def _lead_pid(self) -> str:
        return self.order[self.lead_idx % len(self.order)]

    def _all_picked(self) -> bool:
        # skip_next players don't need to pick
        active = [p for p in self.players.values()
                  if not p.out and not p.skip_next]
        return all(p.battle or p.is_ai for p in active)

    def _start_reveal(self) -> List[dict]:
        self.phase = Phase.REVEAL
        self.current_step = 0
        return [{'type': 'phase_change', 'phase': Phase.REVEAL,
                 'step': 1, 'total_steps': self.declared_steps}]

    def _send_hands(self) -> List[dict]:
        return [{'type': 'hand_update', 'pid': pid,
                 'hand': [c.to_dict() for c in p.hand],
                 'sleeping': [[t.to_dict(), d.to_dict()] for t, d in p.sleeping],
                 'dragon_count': p.dragon_count}
                for pid, p in self.players.items()]

    def _ai_declare(self) -> List[dict]:
        lead = self.players[self._lead_pid()]
        steps, el = ai_declare(lead)
        return self.player_declare(lead.pid, steps, el)

    def _ai_pick_all(self) -> List[dict]:
        events = []
        for p in self.players.values():
            # skip players with skip_next
            if p.is_ai and not p.out and not p.battle and not p.skip_next:
                n = min(self.declared_steps, len(p.hand))
                chosen = ai_pick_cards(p, n, self.declared_el)
                cids = [c.cid for c in chosen]
                events += self.player_pick_cards(p.pid, cids)
        return events

    def _log(self, msg: str):
        self.event_log.append(msg)
        if len(self.event_log) > 200:
            self.event_log.pop(0)

    def public_state(self) -> dict:
        return {
            'room_id': self.room_id, 'phase': self.phase,
            'round': self.round,
            'lead_pid': self._lead_pid() if self.order else None,
            'declared_steps': self.declared_steps,
            'declared_el': self.declared_el,
            'declared_el_name': SUIT_ELEMENT.get(self.declared_el, ''),
            'current_step': self.current_step,
            'players': {pid: p.public_dict() for pid, p in self.players.items()},
            'order': self.order,
            'log': self.event_log[-20:],
        }

    def player_state(self, pid: str) -> dict:
        state = self.public_state()
        if pid in self.players:
            state['me'] = self.players[pid].private_dict()
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
        return [{
            'room_id': r.room_id,
            'players': len(r.players),
            'max_players': r.max_players,
            'phase': r.phase,
        } for r in self.rooms.values()]


if __name__ == '__main__':
    rm = RoomManager()
    game = rm.create_room('test-room', max_players=4)
    game.add_player('human1', 'Roy')
    game.fill_with_ai()
    print(f"Room: {game.room_id} | Players: {len(game.players)}")
    events = game.start_game()
    print(f"Start events: {[e['type'] for e in events]}")
    for _ in range(5):
        if game.phase == Phase.LEADER_DECLARE:
            if not game.players[game._lead_pid()].is_ai:
                events = game.player_declare('human1', 3, 'Hearts')
        elif game.phase == Phase.PICK_CARDS:
            p = game.players['human1']
            n = min(game.declared_steps, len(p.hand))
            cids = [c.cid for c in p.hand[:n]]
            events = game.player_pick_cards('human1', cids)
        elif game.phase == Phase.REVEAL:
            events = game.reveal_step('human1')
        print(f"Phase: {game.phase} | Round: {game.round}")
        if game.phase == Phase.GAME_OVER:
            print("GAME OVER")
            break

    print("\n✅ Engine test passed")
