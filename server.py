"""
Dragon Tamer — WebSocket Server
Python 3.10+ | websockets 16+

Run locally:  python server.py
"""
import asyncio
import json
import logging
import os
import time
import uuid
from typing import Dict, Set

import websockets
from websockets.asyncio.server import ServerConnection, Request, Response
from websockets.datastructures import Headers

from game_engine import RoomManager, Phase

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

rooms  = RoomManager()
room_sockets: Dict[str, Set[ServerConnection]] = {}
socket_meta:  Dict[ServerConnection, dict] = {}


def _spectator_count(room_id: str) -> int:
    return sum(1 for ws, m in socket_meta.items()
               if m.get('room_id') == room_id and m.get('is_spectator'))


def _get_room_sockets(room_id: str) -> Set[ServerConnection]:
    return room_sockets.get(room_id, set())


async def broadcast(room_id: str, message: dict,
                    exclude: ServerConnection = None):
    data = json.dumps(message)
    targets = {ws for ws in _get_room_sockets(room_id) if ws is not exclude}
    if targets:
        await asyncio.gather(*[ws.send(data) for ws in targets],
                              return_exceptions=True)


async def send(ws: ServerConnection, message: dict):
    try:
        await ws.send(json.dumps(message))
    except Exception:
        pass


async def broadcast_events(room_id: str, events: list,
                            private_ws: ServerConnection = None):
    game = rooms.get_room(room_id)
    if not game:
        return

    pid_to_ws: Dict[str, ServerConnection] = {}
    for ws, meta in socket_meta.items():
        if meta.get('room_id') == room_id:
            pid_to_ws[meta['pid']] = ws

    for i, event in enumerate(events):
        if event['type'] == 'hand_update':
            target_ws = pid_to_ws.get(event['pid'])
            if target_ws:
                await send(target_ws, event)
        else:
            await broadcast(room_id, event)
        if i % 5 == 4:
            await asyncio.sleep(0)

    await asyncio.sleep(0)

    if game.phase == Phase.LEADER_DECLARE:
        lead_pid = game._lead_pid()
        if lead_pid and lead_pid in game.players and game.players[lead_pid].is_ai:
            ai_events = game._ai_declare()
            if ai_events:
                await broadcast_events(room_id, ai_events)

    if game.phase == Phase.REVEAL:
        connected_human_pids = {
            meta['pid'] for ws, meta in socket_meta.items()
            if meta.get('room_id') == room_id
            and meta.get('pid')
            and not meta.get('is_spectator')
            and meta['pid'] in game.players
            and not game.players[meta['pid']].out
        }
        active_players = [p for p in game.players.values() if not p.out]
        all_disconnected = all(p.pid not in connected_human_pids and not p.is_ai
                               for p in active_players
                               if not p.is_ai)
        if all_disconnected and active_players:
            reveal_pid = active_players[0].pid
            await asyncio.sleep(1)
            if game.phase == Phase.REVEAL:
                rev_events = game.reveal_step(reveal_pid)
                for ev in (rev_events or []):
                    t = ev.get('type')
                    if t == 'portal_choose_target':
                        tgts = ev.get('valid_target_pids', [])
                        if tgts:
                            game.portal_target_chosen(ev['portal_pid'], tgts[0])
                    elif t == 'queen_choose_target':
                        tgts = ev.get('valid_target_pids', [])
                        if tgts:
                            game.queen_portal_target_chosen(ev['queen_pid'], tgts[0])
                    elif t == 'time_dragon_choose':
                        game.time_dragon_chosen(ev['pid'], 'nothing')
                    elif t == 'space_dragon_choose_swap':
                        game.space_dragon_swap_chosen(ev['space_pid'], None)
                    elif t == 'joker_choose_power':
                        game.joker_power_chosen(ev['pid'], 'nothing')
                    elif t == 'love_choose_tamer':
                        tpids = ev.get('tamer_pids', [])
                        if tpids:
                            game.princess_choose_tamer(ev['princess_pid'], tpids[0])
                if rev_events:
                    await broadcast_events(room_id, rev_events)


def unique_name(name: str, game) -> str:
    """Return name, appending -2, -3 etc. if name already taken in this room."""
    existing = {p.name for p in game.players.values()}
    if name not in existing:
        return name
    i = 2
    while f"{name}-{i}" in existing:
        i += 1
    return f"{name}-{i}"


async def handle_create_room(ws, data):
    room_id      = data.get('room_id') or str(uuid.uuid4())[:8].upper()
    pid          = data.get('pid') or str(uuid.uuid4())[:8]
    name         = data.get('name', 'Player')
    human_count  = int(data.get('ai_count', 3))   # now means "expected humans" (including creator)
    win_drag     = int(data.get('win_dragons', 5))
    num_decks    = int(data.get('num_decks', 1))
    max_pl       = 10  # always allow up to 10 seats total

    if rooms.get_room(room_id):
        await send(ws, {'type': 'error', 'msg': 'Room already exists'})
        return

    import game_engine as ge
    if win_drag in (4, 5, 6, 7, 8, 9, 10, 11, 12):
        ge.set_win_dragons(win_drag)

    game = rooms.create_room(room_id, max_pl)
    game._human_count = human_count
    game._num_decks   = num_decks
    game._stored_num_decks = num_decks  # backup in case _num_decks gets lost
    name = unique_name(name, game)
    game.add_player(pid, name)

    room_sockets[room_id] = {ws}
    socket_meta[ws] = {'pid': pid, 'room_id': room_id, 'name': name}

    await send(ws, {
        'type': 'room_created',
        'room_id': room_id,
        'pid': pid,
        'win_dragons': ge.WIN_DRAGONS,
        'human_count': human_count,
        'state': game.player_state(pid),
    })
    log.info(f"Room {room_id} created by {name} ({pid}), expected_humans={human_count}, win_dragons={ge.WIN_DRAGONS}")


async def handle_join_room(ws, data):
    room_id = data.get('room_id', '').upper()
    pid     = data.get('pid') or str(uuid.uuid4())[:8]
    name    = data.get('name', 'Player')

    game = rooms.get_room(room_id)
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Room not found'})
        return

    name   = unique_name(name, game)
    result = game.add_player(pid, name)
    if not result['ok']:
        await send(ws, {'type': 'error', 'msg': result['error']})
        return

    room_sockets.setdefault(room_id, set()).add(ws)
    socket_meta[ws] = {'pid': pid, 'room_id': room_id, 'name': name}

    await send(ws, {
        'type': 'room_joined',
        'room_id': room_id,
        'pid': pid,
        'state': game.player_state(pid),
    })
    await broadcast(room_id, {
        'type': 'player_joined',
        'pid': pid,
        'name': name,
        'player_count': len(game.players),
    }, exclude=ws)
    log.info(f"{name} ({pid}) joined room {room_id}")


async def handle_start_game(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Not in a room'})
        return
    if game.phase != Phase.WAITING:
        return

    # Fill remaining empty seats with AI now (at start time, not room creation)
    import random as _rand
    _all_strats = ['Aggressive','Balanced','Conservative','Hoarder',
                   'Adaptive','AntiDragon','Diplomat','Bluffer','Avenger',
                   'Maximalist','Minimalist','Opportunist','Purist','DragonHunter']
    _rand.shuffle(_all_strats)
    expected_total = getattr(game, '_human_count', 4)  # total players expected
    actual_humans  = len(game.players)
    ai_needed      = max(1, expected_total - actual_humans)  # at least 1 AI
    # Set max_players to actual total so fill_with_ai stops at the right count
    game.max_players = actual_humans + ai_needed
    game.fill_with_ai(_all_strats[:ai_needed])

    # Ensure _num_decks is set before start_game (may have been lost on rejoin/reconnect)
    # Re-read from stored value if available
    if not hasattr(game, '_num_decks') or game._num_decks is None:
        game._num_decks = getattr(game, '_stored_num_decks', 1)
    game._num_decks = getattr(game, '_num_decks', 1)  # safety
    log.info(f"DEBUG start_game: _num_decks={game._num_decks}, _stored_num_decks={getattr(game,'_stored_num_decks','NOT SET')}, hasattr={hasattr(game,'_num_decks')}")

    events = game.start_game()
    await broadcast_events(meta['room_id'], events)
    log.info(f"Game started in room {meta['room_id']} ({actual_humans} humans + {ai_needed} AI, total={actual_humans+ai_needed}, num_decks={game._num_decks}, max_steps={game._max_steps})")


async def handle_declare(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return

    steps   = int(data.get('steps', 3))
    element = data.get('element', 'Hearts')
    events  = game.player_declare(meta['pid'], steps, element)
    await broadcast_events(meta['room_id'], events)


async def handle_pick_cards(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return

    cids   = [int(c) for c in data.get('card_cids', [])]
    events = game.player_pick_cards(meta['pid'], cids)
    await broadcast_events(meta['room_id'], events)


async def handle_reorder_hand(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return

    cids   = [int(c) for c in data.get('cid_list', [])]
    events = game.player_reorder_hand(meta['pid'], cids)
    for ev in events:
        await send(ws, ev)


async def handle_ready_arrange(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return

    events = game.player_ready_arrange(meta['pid'])
    await asyncio.sleep(0)
    await broadcast_events(meta['room_id'], events)


async def handle_reveal(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return

    events = game.reveal_step(meta['pid'])
    await broadcast_events(meta['room_id'], events)


async def handle_portal_target(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.portal_target_chosen(meta['pid'], data.get('target_pid', ''))
    await broadcast_events(meta['room_id'], events)


async def handle_queen_fury_target(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.queen_portal_target_chosen(meta['pid'], data.get('target_pid', ''))
    await broadcast_events(meta['room_id'], events)


async def handle_princess_choose_tamer(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.princess_choose_tamer(meta['pid'], data.get('tamer_pid', ''))
    await broadcast_events(meta['room_id'], events)


async def handle_joker_power(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    power  = data.get('power', '')
    choice = data.get('choice', '')
    if power == 'time' and choice in ('back', 'forward', 'nothing'):
        events = game.time_dragon_chosen(meta['pid'], choice)
        await broadcast_events(meta['room_id'], events)


async def handle_joker_power_chosen(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    power = data.get('power', '')
    events = game.joker_power_chosen(meta['pid'], power)
    await broadcast_events(meta['room_id'], events)


async def handle_space_dragon_swap(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game:
        return
    target_pid = data.get('target_pid') or None
    events = game.space_dragon_swap_chosen(meta['pid'], target_pid)
    await broadcast_events(meta['room_id'], events)


async def handle_get_state(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Not in a room'})
        return
    await send(ws, {'type': 'state', 'state': game.player_state(meta['pid'])})


async def handle_spectate_room(ws, data):
    room_id = data.get('room_id', '').upper()
    name    = data.get('name', 'Spectator')

    game = rooms.get_room(room_id)
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Room not found'})
        return

    room_sockets.setdefault(room_id, set()).add(ws)
    socket_meta[ws] = {'pid': None, 'room_id': room_id,
                        'name': name, 'is_spectator': True}

    spec_count = _spectator_count(room_id)
    await send(ws, {
        'type': 'spectating',
        'room_id': room_id,
        'spectator_count': spec_count,
        'state': game.public_state(),
    })
    await broadcast(room_id, {
        'type': 'spectator_update',
        'spectator_count': spec_count,
    }, exclude=ws)
    log.info(f"Spectator '{name}' joined room {room_id} ({spec_count} watching)")


_rejoin_ts: Dict[tuple, float] = {}

async def handle_rejoin(ws, data):
    room_id = data.get('room_id', '').upper()
    pid     = data.get('pid', '')
    key = (room_id, pid)
    now = time.monotonic()
    if now - _rejoin_ts.get(key, 0.0) < 3.0:
        return
    _rejoin_ts[key] = now
    game    = rooms.get_room(room_id)
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Room not found — game may have ended'})
        return
    if pid not in game.players:
        await send(ws, {'type': 'error', 'msg': 'Player not found in room'})
        return
    name = game.players[pid].name
    stale = [w for w, m in socket_meta.items()
             if m.get('room_id') == room_id and m.get('pid') == pid and w is not ws]
    for w in stale:
        room_sockets.get(room_id, set()).discard(w)
        socket_meta.pop(w, None)
    room_sockets.setdefault(room_id, set()).add(ws)
    socket_meta[ws] = {'pid': pid, 'room_id': room_id, 'name': name}
    await send(ws, {
        'type': 'rejoined',
        'room_id': room_id,
        'pid': pid,
        'state': game.player_state(pid),
    })
    log.info(f"Rejoined: {name} ({pid}) in room {room_id}")


async def handle_list_rooms(ws, data):
    room_list = rooms.list_rooms()
    for r in room_list:
        r['spectator_count'] = _spectator_count(r['room_id'])
    await send(ws, {'type': 'rooms', 'rooms': room_list})


async def handle_ping(ws, data):
    await send(ws, {'type': 'pong'})

HANDLERS = {
    'ping':                  handle_ping,
    'create_room':           handle_create_room,
    'join_room':             handle_join_room,
    'rejoin':                handle_rejoin,
    'spectate_room':         handle_spectate_room,
    'start_game':            handle_start_game,
    'declare':               handle_declare,
    'pick_cards':            handle_pick_cards,
    'reorder_hand':          handle_reorder_hand,
    'ready_arrange':         handle_ready_arrange,
    'reveal':                handle_reveal,
    'joker_power':           handle_joker_power,
    'joker_power_chosen':    handle_joker_power_chosen,
    'space_dragon_swap':     handle_space_dragon_swap,
    'portal_target':         handle_portal_target,
    'queen_fury_target':     handle_queen_fury_target,
    'princess_choose_tamer': handle_princess_choose_tamer,
    'get_state':             handle_get_state,
    'list_rooms':            handle_list_rooms,
}


async def connection_handler(ws: ServerConnection):
    log.info(f"New connection: {ws.remote_address}")
    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
                msg_type = data.get('type', '')
                handler = HANDLERS.get(msg_type)
                if handler:
                    await handler(ws, data)
                else:
                    await send(ws, {'type': 'error', 'msg': f'Unknown type: {msg_type}'})
            except json.JSONDecodeError:
                await send(ws, {'type': 'error', 'msg': 'Invalid JSON'})
            except Exception as e:
                log.exception(f"Handler error: {e}")
                await send(ws, {'type': 'error', 'msg': str(e)})
    finally:
        meta = socket_meta.pop(ws, {})
        room_id = meta.get('room_id')
        if room_id and room_id in room_sockets:
            room_sockets[room_id].discard(ws)
            if not room_sockets[room_id]:
                room_sockets.pop(room_id, None)
            elif meta.get('is_spectator'):
                await broadcast(room_id, {
                    'type': 'spectator_update',
                    'spectator_count': _spectator_count(room_id),
                })
        log.info(f"Disconnected: {meta.get('name', 'unknown')} "
                 f"({'spectator' if meta.get('is_spectator') else 'player'})")


async def _http_handler(connection: ServerConnection, request: Request):
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    try:
        with open("index.html", "rb") as f:
            body = f.read()
        headers = Headers([
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ])
        return Response(200, "OK", headers, body)
    except FileNotFoundError:
        return Response(404, "Not Found", Headers([]), b"Not found")


async def main():
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 8080))

    log.info(f"Dragon Tamer Server starting on {host}:{port} (HTTP + WS)")
    async with websockets.asyncio.server.serve(
        connection_handler, host, port,
        process_request=_http_handler,
        ping_interval=None,
        ping_timeout=None,
        reuse_port=True,
    ):
        await asyncio.Future()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server stopped by user (Ctrl+C)")
    except Exception as e:
        log.exception(f"FATAL: Server crashed at top level: {e}")
        import sys
        sys.exit(1)  # non-zero exit so watchdog.sh restarts it

