"""
Dragon Tamer — WebSocket Server
Python 3.10+ | websockets 12+

Run locally:  python server.py
Deploy:       Railway / Render (see README)
"""
import asyncio
import json
import logging
import os
import uuid
import http
from typing import Dict, Set

import websockets
from websockets.server import WebSocketServerProtocol

from game_engine import RoomManager, Phase

logging.basicConfig(level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════
# CONNECTION REGISTRY
# ══════════════════════════════════════════════════════
rooms  = RoomManager()

# room_id → set of websockets (players + spectators)
room_sockets: Dict[str, Set[WebSocketServerProtocol]] = {}
# websocket → {pid, room_id, name, is_spectator}
socket_meta:  Dict[WebSocketServerProtocol, dict] = {}


def _spectator_count(room_id: str) -> int:
    return sum(1 for ws, m in socket_meta.items()
               if m.get('room_id') == room_id and m.get('is_spectator'))


def _get_room_sockets(room_id: str) -> Set[WebSocketServerProtocol]:
    return room_sockets.get(room_id, set())


async def broadcast(room_id: str, message: dict,
                    exclude: WebSocketServerProtocol = None):
    """Send a message to every socket in a room."""
    data = json.dumps(message)
    targets = {ws for ws in _get_room_sockets(room_id) if ws is not exclude}
    if targets:
        await asyncio.gather(*[ws.send(data) for ws in targets],
                              return_exceptions=True)


async def send(ws: WebSocketServerProtocol, message: dict):
    """Send a message to one socket."""
    try:
        await ws.send(json.dumps(message))
    except Exception:
        pass


async def broadcast_events(room_id: str, events: list,
                            private_ws: WebSocketServerProtocol = None):
    """
    Broadcast a list of engine events.
    hand_update events are sent only to the relevant player.
    """
    game = rooms.get_room(room_id)
    if not game:
        return

    # Build pid → ws map
    pid_to_ws: Dict[str, WebSocketServerProtocol] = {}
    for ws, meta in socket_meta.items():
        if meta.get('room_id') == room_id:
            pid_to_ws[meta['pid']] = ws

    for event in events:
        if event['type'] == 'hand_update':
            # Send only to the relevant player
            target_ws = pid_to_ws.get(event['pid'])
            if target_ws:
                await send(target_ws, event)
        else:
            # Broadcast to all
            await broadcast(room_id, event)


# ══════════════════════════════════════════════════════
# MESSAGE HANDLERS
# ══════════════════════════════════════════════════════
async def handle_create_room(ws, data):
    room_id  = data.get('room_id') or str(uuid.uuid4())[:8].upper()
    pid      = data.get('pid') or str(uuid.uuid4())[:8]
    name     = data.get('name', 'Player')
    ai_count = int(data.get('ai_count', 3))
    max_pl   = 1 + ai_count   # 1 human + N AI opponents

    if rooms.get_room(room_id):
        await send(ws, {'type': 'error', 'msg': 'Room already exists'})
        return

    game = rooms.create_room(room_id, max_pl)
    game.add_player(pid, name)
    game.fill_with_ai(['Aggressive','Balanced','Conservative','Hoarder',
                        'Adaptive','AntiDragon','Diplomat','Bluffer','Avenger'][:ai_count])

    room_sockets[room_id] = {ws}
    socket_meta[ws] = {'pid': pid, 'room_id': room_id, 'name': name}

    await send(ws, {
        'type': 'room_created',
        'room_id': room_id,
        'pid': pid,
        'state': game.player_state(pid),
    })
    log.info(f"Room {room_id} created by {name} ({pid})")


async def handle_join_room(ws, data):
    room_id = data.get('room_id', '').upper()
    pid     = data.get('pid') or str(uuid.uuid4())[:8]
    name    = data.get('name', 'Player')

    game = rooms.get_room(room_id)
    if not game:
        await send(ws, {'type': 'error', 'msg': 'Room not found'})
        return

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
        return  # silently ignore duplicate start_game calls

    events = game.start_game()
    await broadcast_events(meta['room_id'], events)
    log.info(f"Game started in room {meta['room_id']}")


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


async def handle_reveal(ws, data):
    meta = socket_meta.get(ws, {})
    game = rooms.get_room(meta.get('room_id', ''))
    if not game: return

    events = game.reveal_step(meta['pid'])
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


async def handle_list_rooms(ws, data):
    room_list = rooms.list_rooms()
    for r in room_list:
        r['spectator_count'] = _spectator_count(r['room_id'])
    await send(ws, {'type': 'rooms', 'rooms': room_list})


# ══════════════════════════════════════════════════════
# MAIN HANDLER
# ══════════════════════════════════════════════════════
HANDLERS = {
    'create_room':   handle_create_room,
    'join_room':     handle_join_room,
    'spectate_room': handle_spectate_room,
    'start_game':    handle_start_game,
    'declare':       handle_declare,
    'pick_cards':    handle_pick_cards,
    'reveal':        handle_reveal,
    'get_state':     handle_get_state,
    'list_rooms':    handle_list_rooms,
}


async def connection_handler(ws: WebSocketServerProtocol):
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
        # Cleanup on disconnect
        meta = socket_meta.pop(ws, {})
        room_id = meta.get('room_id')
        if room_id and room_id in room_sockets:
            room_sockets[room_id].discard(ws)
            if not room_sockets[room_id]:
                room_sockets.pop(room_id, None)
            elif meta.get('is_spectator'):
                # Notify remaining sockets that spectator count changed
                await broadcast(room_id, {
                    'type': 'spectator_update',
                    'spectator_count': _spectator_count(room_id),
                })
        log.info(f"Disconnected: {meta.get('name', 'unknown')} "
                 f"({'spectator' if meta.get('is_spectator') else 'player'})")


async def _http_handler(path, request_headers):
    """Serve index.html for plain HTTP requests; let WS upgrades through."""
    if request_headers.get("Upgrade", "").lower() == "websocket":
        return None  # hand off to websocket handler
    try:
        with open("index.html", "rb") as f:
            body = f.read()
        headers = [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ]
        return http.HTTPStatus.OK, headers, body
    except FileNotFoundError:
        return http.HTTPStatus.NOT_FOUND, [], b"Not found"


async def main():
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', 5000))

    log.info(f"Dragon Tamer Server starting on {host}:{port} (HTTP + WS)")
    async with websockets.serve(
        connection_handler, host, port,
        process_request=_http_handler
    ):
        await asyncio.Future()  # run forever


if __name__ == '__main__':
    asyncio.run(main())
