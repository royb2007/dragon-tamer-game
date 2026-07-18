"""
Dragon Tamer — WebSocket Server (Memory-Optimized, websockets legacy API)
"""
import asyncio
import json
import logging
import os
import signal
import time
import uuid
import gc
from typing import Any, Dict, Set

import websockets

from game_engine import RoomManager, Phase

logging.basicConfig(level=logging.WARNING,
    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

rooms  = RoomManager()
room_sockets: Dict[str, Set[Any]] = {}
socket_meta:  Dict[Any, dict] = {}

_GC_COUNTER = 0

def _maybe_gc():
    global _GC_COUNTER
    _GC_COUNTER += 1
    if _GC_COUNTER % 20 == 0:
        gc.collect()

def _spectator_count(room_id: str) -> int:
    return sum(1 for ws, m in socket_meta.items()
               if m.get('room_id') == room_id and m.get('is_spectator'))

def _get_room_sockets(room_id: str) -> Set[Any]:
    return room_sockets.get(room_id, set())

async def broadcast(room_id: str, message: dict, exclude=None):
    data = json.dumps(message)
    targets = {ws for ws in _get_room_sockets(room_id) if ws is not exclude}
    if targets:
        await asyncio.gather(*[ws.send(data) for ws in targets], return_exceptions=True)

async def send(ws, message: dict):
    try:
        await ws.send(json.dumps(message))
    except Exception:
        pass

async def broadcast_events(room_id: str, events: list, private_ws=None):
    game = rooms.get_room(room_id)
    if not game:
        return

    pid_to_ws: Dict[str, Any] = {}
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
    _maybe_gc()

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
                               for p in active_players if not p.is_ai)
        if all_disconnected and active_players:
            reveal_pid = active_players[0].pid
            await asyncio.sleep(1)
            if game.phase == Phase.REVEAL:
                rev_events = game.reveal_step(reveal_pid)
                for ev in (rev_events or []):
                    t = ev.get('type')
                    if t == 'portal_choose_target':
                        tgts = ev.get('valid_target_pids', [])
                        if tgts: game.portal_target_chosen(ev['portal_pid'], tgts[0])
                    elif t == 'queen_choose_target':
                        tgts = ev.get('valid_target_pids', [])
                        if tgts: game.queen_portal_target_chosen(ev['queen_pid'], tgts[0])
                    elif t == 'time_dragon_choose':
                        game.time_dragon_chosen(ev['pid'], 'nothing')
                    elif t == 'space_dragon_choose_swap':
                        game.space_dragon_swap_chosen(ev['space_pid'], None)
                    elif t == 'joker_choose_power':
                        game.joker_power_chosen(ev['pid'], 'nothing')
                    elif t == 'love_choose_tamer':
                        tpids = ev.get('tamer_pids', [])
                        if tpids: game.princess_choose_tamer(ev['princess_pid'], tpids[0])
                if rev_events:
                    await broadcast_events(room_id, rev_events)


def unique_name(name: str, game) -> str:
    existing = {p.name for p in game.players.values()}
    if name not in existing:
        return name
    i = 2
    while f"{name}-{i}" in existing:
        i += 1
    return f"{name}-{i}"


async def handle_create_room(ws, data):
    room_id     = data.get('room_id') or str(uuid.uuid4())[:8].upper()
    pid         = data.get('pid') or str(uuid.uuid4())[:8]
    name        = data.get('name', 'Player')
    human_count = int(data.get('ai_count', 3))
    win_drag    = int(data.get('win_dragons', 5))
    num_decks   = int(data.get('num_decks', 1))
    max_pl      = 10

    if rooms.get_room(room_id):
        await send(ws, {'type': 'error', 'msg': 'Room already exists'})
        return

    import game_engine as ge
    if win_drag in (4, 5, 6, 7, 8, 9, 10, 11, 12):
        ge.set_win_dragons(win_drag)

    game = rooms.create_room(room_id, max_pl)
    game._human_count = human_count
    game._num_decks   = num_decks
    game._stored_num_decks = num_decks
    name = unique_name(name, game)
    game.add_player(pid, name)

    room_sockets[room_id] = {ws}
    socket_meta[ws] = {'pid': pid, 'room_id': room_id, 'name': name}

    await send(ws, {
        'type': 'room_created', 'room_id': room_id, 'pid': pid,
        'win_dragons': ge.WIN_DRAGONS, 'human_count': human_count,
        'state': game.player_state(pid),
    })
    log.warning(f"Room {room_id} created by {name} ({pid}), win_dragons={win_drag}")


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

    await send(ws, {'type': 'room_joined', 'room_id': room_id, 'pid': pid,
                    'state': game.player_state(pid)})
    await broadcast(room_id, {'type': 'player_joined', 'pid': pid, 'name': name,
                               'player_count': len(game.players)}, exclude=ws)


async def handle_start_game(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    if game.phase != Phase.WAITING: return

    import random as _rand
    _all_strats = ['Aggressive','Balanced','Conservative','Hoarder',
                   'Adaptive','AntiDragon','Diplomat','Bluffer','Avenger',
                   'Maximalist','Minimalist','Opportunist','Purist','DragonHunter']
    _rand.shuffle(_all_strats)
    expected_total = getattr(game, '_human_count', 4)
    actual_humans  = len(game.players)
    ai_needed      = max(1, expected_total - actual_humans)
    game.max_players = actual_humans + ai_needed
    game.fill_with_ai(_all_strats[:ai_needed])

    if not hasattr(game, '_num_decks') or game._num_decks is None:
        game._num_decks = getattr(game, '_stored_num_decks', 1)

    events = game.start_game()
    log.warning(f"Game started in room {meta['room_id']}")
    await broadcast_events(meta['room_id'], events)
    gc.collect()


async def handle_declare(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.player_declare(meta['pid'], int(data.get('steps', 3)), data.get('element', 'Hearts'))
    await broadcast_events(meta['room_id'], events)


async def handle_pick_cards(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.player_pick_cards(meta['pid'], [int(c) for c in data.get('card_cids', [])])
    for ev in events:
        if ev.get('type') == 'cards_picked' and ev.get('pid') in game.players:
            ev['name'] = game.players[ev['pid']].name
    await broadcast_events(meta['room_id'], events)


async def handle_reorder_hand(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.player_reorder_hand(meta['pid'], [int(c) for c in data.get('cid_list', [])])
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
    power = data.get('power', '')
    choice = data.get('choice', '')
    if power == 'time' and choice in ('back', 'forward', 'nothing'):
        events = game.time_dragon_chosen(meta['pid'], choice)
        await broadcast_events(meta['room_id'], events)


async def handle_joker_power_chosen(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.joker_power_chosen(meta['pid'], data.get('power', ''))
    await broadcast_events(meta['room_id'], events)


async def handle_space_dragon_swap(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return
    events = game.space_dragon_swap_chosen(meta['pid'], data.get('target_pid') or None)
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
    game    = rooms.get_room(room_id)
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Room not found'})
        return
    room_sockets.setdefault(room_id, set()).add(ws)
    socket_meta[ws] = {'pid': None, 'room_id': room_id, 'name': name, 'is_spectator': True}
    spec_count = _spectator_count(room_id)
    await send(ws, {'type': 'spectating', 'room_id': room_id,
                    'spectator_count': spec_count, 'state': game.public_state()})
    await broadcast(room_id, {'type': 'spectator_update', 'spectator_count': spec_count}, exclude=ws)


_rejoin_ts: Dict[tuple, float] = {}
_room_last_active: Dict[str, float] = {}  # room_id → monotonic time last socket left

async def handle_rejoin(ws, data):
    room_id = data.get('room_id', '').upper()
    pid     = data.get('pid', '')
    key = (room_id, pid)
    now = time.monotonic()
    if now - _rejoin_ts.get(key, 0.0) < 3.0:
        return
    _rejoin_ts[key] = now
    game = rooms.get_room(room_id)
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
    await send(ws, {'type': 'rejoined', 'room_id': room_id, 'pid': pid,
                    'state': game.player_state(pid)})
    log.warning(f"Rejoined: {name} ({pid}) in room {room_id}")


async def handle_list_rooms(ws, data):
    room_list = rooms.list_rooms()
    for r in room_list:
        r['spectator_count'] = _spectator_count(r['room_id'])
    await send(ws, {'type': 'rooms', 'rooms': room_list})


async def handle_ping(ws, data):
    await send(ws, {'type': 'pong'})


HANDLERS = {
    'ping': handle_ping,
    'create_room': handle_create_room,
    'join_room': handle_join_room,
    'rejoin': handle_rejoin,
    'spectate_room': handle_spectate_room,
    'start_game': handle_start_game,
    'declare': handle_declare,
    'pick_cards': handle_pick_cards,
    'reorder_hand': handle_reorder_hand,
    'ready_arrange': handle_ready_arrange,
    'reveal': handle_reveal,
    'joker_power': handle_joker_power,
    'joker_power_chosen': handle_joker_power_chosen,
    'space_dragon_swap': handle_space_dragon_swap,
    'portal_target': handle_portal_target,
    'queen_fury_target': handle_queen_fury_target,
    'princess_choose_tamer': handle_princess_choose_tamer,
    'get_state': handle_get_state,
    'list_rooms': handle_list_rooms,
}


async def connection_handler(ws, path):
    peer = getattr(ws, 'remote_address', '?')
    log.warning(f"Connected: {peer}")
    try:
        async for raw in ws:
            try:
                data = json.loads(raw)
                msg_type = data.get('type', '')
                if msg_type != 'ping':
                    log.warning(f"MSG [{msg_type}] from {peer}")
                handler = HANDLERS.get(msg_type)
                if handler:
                    await handler(ws, data)
                else:
                    await send(ws, {'type': 'error', 'msg': f'Unknown type: {msg_type}'})
            except json.JSONDecodeError:
                await send(ws, {'type': 'error', 'msg': 'Invalid JSON'})
            except Exception as e:
                log.exception(f"Handler error [{msg_type}]: {e}")
                await send(ws, {'type': 'error', 'msg': str(e)})
    except websockets.exceptions.ConnectionClosedError:
        pass  # Client disconnected without a close frame — normal for browser refreshes
    except Exception as e:
        log.warning(f"Connection error: {e}")
    finally:
        meta = socket_meta.pop(ws, {})
        room_id = meta.get('room_id')
        pid     = meta.get('pid', 'unknown')
        role    = 'spectator' if meta.get('is_spectator') else 'player'
        name    = meta.get('name', 'unknown')
        log.warning(f"Disconnected: {name} ({role})")
        if room_id and room_id in room_sockets:
            room_sockets[room_id].discard(ws)
            if not room_sockets[room_id]:
                room_sockets.pop(room_id, None)
                _room_last_active[room_id] = time.monotonic()
            elif meta.get('is_spectator'):
                await broadcast(room_id, {
                    'type': 'spectator_update',
                    'spectator_count': _spectator_count(room_id),
                })


async def _cleanup_loop():
    """Periodically delete abandoned rooms and force GC to prevent memory accumulation."""
    EMPTY_TTL   = 1200  # delete room 20 min after last socket leaves
    GAMEOVER_TTL = 120  # delete game_over rooms after 2 min
    INTERVAL    = 300   # run every 5 minutes
    while True:
        await asyncio.sleep(INTERVAL)
        try:
            now = time.monotonic()
            to_delete = []
            for info in rooms.list_rooms():
                rid = info.get('room_id')
                if not rid:
                    continue
                has_sockets = bool(room_sockets.get(rid))
                if has_sockets:
                    continue
                last = _room_last_active.get(rid, 0.0)
                game = rooms.get_room(rid)
                ttl = GAMEOVER_TTL if (game and getattr(game, 'phase', None) == Phase.GAME_OVER) else EMPTY_TTL
                if now - last >= ttl:
                    to_delete.append(rid)
            for rid in to_delete:
                try:
                    rooms.delete_room(rid)
                    _room_last_active.pop(rid, None)
                    _rejoin_ts.pop(rid, None)  # won't match tuple keys but harmless
                    log.warning(f"Cleanup: removed abandoned room {rid}")
                except Exception as ex:
                    log.warning(f"Cleanup: failed to remove room {rid}: {ex}")
            gc.collect()
            if to_delete:
                log.warning(f"Cleanup: removed {len(to_delete)} rooms, GC done")
        except Exception as ex:
            log.warning(f"Cleanup loop error: {ex}")


# ---------------------------------------------------------------------------
# Pre-load every file we serve at import time so _http_handler never touches
# the disk during a request (blocking file I/O on the asyncio event loop was
# freezing the server in production).
# ---------------------------------------------------------------------------
def _load_file(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except Exception as e:
        log.warning(f"Failed to pre-load {path}: {e}")
        return None

def _build_html_cache():
    raw = _load_file("index.html")
    if raw is None:
        return None
    html = raw.decode("utf-8")
    html = html.replace(
        'https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js',
        '/qrcode.min.js'
    ).replace(
        'https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700;900&family=Crimson+Text:ital,wght@0,400;0,600;1,400&display=swap',
        '/fonts.css'
    )
    return html.encode("utf-8")

_HTML_BODY   = _build_html_cache()
_HTML_HEADERS = [
    ("Content-Type",  "text/html; charset=utf-8"),
    ("Content-Length", str(len(_HTML_BODY)) if _HTML_BODY else "0"),
    ("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"),
    ("Pragma",        "no-cache"),
]

_STATIC_CACHE: dict = {}
_STATIC_TYPES = {
    '/qrcode.min.js': ('qrcode.min.js',   'application/javascript'),
    '/fonts.css':     ('static/fonts.css', 'text/css'),
    '/music/celestial-dawn.mp3':    ('static/music/celestial-dawn.mp3',    'audio/mpeg'),
    '/music/battle-of-legends.mp3': ('static/music/battle-of-legends.mp3', 'audio/mpeg'),
}
for _url, (_fpath, _ctype) in _STATIC_TYPES.items():
    _data = _load_file(_fpath)
    if _data is not None:
        _STATIC_CACHE[_url] = (_ctype, _data)

_FONTS_DIR = 'static/fonts'
if os.path.isdir(_FONTS_DIR):
    for _fname in os.listdir(_FONTS_DIR):
        _data = _load_file(os.path.join(_FONTS_DIR, _fname))
        if _data is not None:
            _STATIC_CACHE[f'/fonts/{_fname}'] = ('font/truetype', _data)

log.warning(f"Pre-loaded {len(_STATIC_CACHE)} static assets + HTML ({len(_HTML_BODY) if _HTML_BODY else 0} bytes)")


async def _http_handler(path, request_headers):
    """Serve pre-cached files; return None to let websockets handle WS upgrades."""
    log.warning(f"HTTP path type={type(path).__name__} repr={repr(path)[:120]}")
    try:
        upgrade = request_headers.get("Upgrade", "").lower()
    except Exception:
        upgrade = ""
    if upgrade == "websocket":
        return None

    # Safely coerce path to string regardless of websockets version
    raw_path = path.path if hasattr(path, 'path') else str(path)
    clean_path = raw_path.split('?')[0]

    if clean_path in _STATIC_CACHE:
        ctype, body = _STATIC_CACHE[clean_path]
        return (200, [
            ("Content-Type",   ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control",  "public, max-age=86400"),
        ], body)

    # Everything else → main game HTML
    if _HTML_BODY is None:
        return (404, [], b"index.html not found")
    return (200, _HTML_HEADERS, _HTML_BODY)


async def main():
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5000))
    log.warning(f"Dragon Tamer Server starting on {host}:{port} (HTTP + WS)")
    loop = asyncio.get_event_loop()
    stop = loop.create_future()
    def _request_stop():
        if not stop.done():
            stop.set_result(None)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            pass
    async with websockets.serve(
        connection_handler, host, port,
        process_request=_http_handler,
        ping_interval=20,
        ping_timeout=60,
        reuse_port=True,
    ):
        log.warning(f"server listening on {host}:{port}")
        loop.create_task(_cleanup_loop())
        await stop
        log.warning("Server shutting down cleanly.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        log.critical(f"FATAL: Server crashed at top level: {e}", exc_info=True)
        import sys
        sys.exit(1)


