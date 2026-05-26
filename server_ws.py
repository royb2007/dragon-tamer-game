"""
Dragon Tamer — WebSocket Game Server v2.0
Replaces server.py with a WebSocket-based server matching index.html protocol.
"""

import json
import random
import threading
import uuid
from flask import Flask, send_file, jsonify
from flask_sock import Sock
from game_engine import RoomManager, Phase

app = Flask(__name__)
sock = Sock(app)
rm   = RoomManager()

# room_id → set of WebSocket clients (with metadata)
rooms_clients: dict[str, list[dict]] = {}  # [{ws, pid, name, spectator}]
lock = threading.Lock()

# ── Helpers ────────────────────────────────────────────────────────────────

def broadcast(room_id: str, msg: dict, exclude_ws=None):
    with lock:
        clients = list(rooms_clients.get(room_id, []))
    dead = []
    for c in clients:
        if c['ws'] is exclude_ws:
            continue
        try:
            c['ws'].send(json.dumps(msg, default=str))
        except Exception:
            dead.append(c)
    if dead:
        with lock:
            for c in dead:
                rooms_clients.get(room_id, []).remove(c) if c in rooms_clients.get(room_id, []) else None

def send_to(ws, msg: dict):
    try:
        ws.send(json.dumps(msg, default=str))
    except Exception:
        pass

def broadcast_all(room_id: str, events: list):
    """Broadcast a list of engine events to all clients in a room."""
    for e in events:
        broadcast(room_id, e)

def handle_pending(ws, pid, room_id, events):
    """Handle any pause events that need human response, broadcast the rest."""
    for e in events:
        t = e['type']
        if t == 'portal_choose_target' and e['portal_pid'] == pid:
            send_to(ws, e)
        elif t == 'love_choose_tamer' and e['princess_pid'] == pid:
            send_to(ws, e)
        elif t == 'space_dragon_choose_swap' and e['space_pid'] == pid:
            send_to(ws, e)
        elif t == 'forced_wake_choose' and e['pid'] == pid:
            send_to(ws, e)
        else:
            broadcast(room_id, e)

def game_state_for(game, pid):
    p = game.players.get(pid)
    return {
        'type': 'state',
        'phase': game.phase.value if hasattr(game.phase, 'value') else str(game.phase),
        'round': game.round,
        'declared_steps': game.declared_steps,
        'declared_el': game.declared_el,
        'order': game.order,
        'players': {
            pid2: {
                'name': p2.name,
                'dragon_count': p2.dragon_count,
                'hand_count': len(p2.hand),
                'battle_count': len(p2.battle),
                'sleeping': [[t.to_dict(), d.to_dict()] for t, d in p2.sleeping],
                'out': p2.out,
                'is_ai': p2.is_ai,
                'skip': p2.pid in game._skipped_this_round,
            }
            for pid2, p2 in game.players.items()
        },
        'me': {
            'pid': pid,
            'hand': [c.to_dict() for c in p.hand] if p else [],
            'battle': [c.to_dict() for c in p.battle] if p else [],
            'sleeping': [[t.to_dict(), d.to_dict()] for t, d in p.sleeping] if p else [],
            'dragon_count': p.dragon_count if p else 0,
        } if p else None,
        'lead_pid': game._lead_pid() if game.phase != Phase.WAITING else None,
    }

# ── WebSocket handler ──────────────────────────────────────────────────────

@sock.route("/")
def ws_handler(ws):
    pid     = None
    room_id = None
    client  = {'ws': ws, 'pid': None, 'name': 'Unknown', 'spectator': False}

    try:
        while True:
            raw = ws.receive()
            if raw is None:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                send_to(ws, {'type': 'error', 'msg': 'Invalid JSON'})
                continue

            t = msg.get('type')

            # ── Create room ──────────────────────────────────────────
            if t == 'create_room':
                room_id = str(uuid.uuid4())[:6].upper()
                n_ai    = int(msg.get('ai_opponents', 3))
                name    = msg.get('name', 'Player')
                pid     = str(uuid.uuid4())[:8]

                game = rm.create_room(room_id, 1 + n_ai)
                game.add_player(pid, name, is_ai=False)

                with lock:
                    rooms_clients[room_id] = []
                client.update({'pid': pid, 'name': name})
                with lock:
                    rooms_clients[room_id].append(client)

                # Fill AI
                for i in range(n_ai):
                    strategies = ['Aggressive','Balanced','Conservative','Hoarder',
                                  'Adaptive','DragonHunter','Purist','Maximalist',
                                  'Minimalist','Opportunist']
                    s = random.choice(strategies)
                    game.add_player(f'ai_{i}', s, is_ai=True, ai_strategy=s)

                send_to(ws, {'type': 'room_created', 'room_id': room_id,
                             'pid': pid, 'name': name})

                # Auto-start
                events = game.start_game()
                send_to(ws, game_state_for(game, pid))
                handle_pending(ws, pid, room_id, events)

            # ── Join room ─────────────────────────────────────────────
            elif t == 'join_room':
                room_id = msg.get('room_id', '').upper()
                name    = msg.get('name', 'Player')
                pid     = str(uuid.uuid4())[:8]
                game    = rm.get_room(room_id)

                if not game:
                    send_to(ws, {'type': 'error', 'msg': f'Room {room_id} not found'})
                    continue

                result = game.add_player(pid, name, is_ai=False)
                if result.get('error'):
                    send_to(ws, {'type': 'error', 'msg': result['error']})
                    continue

                client.update({'pid': pid, 'name': name})
                with lock:
                    rooms_clients.setdefault(room_id, []).append(client)

                send_to(ws, {'type': 'room_joined', 'room_id': room_id,
                             'pid': pid, 'name': name})
                broadcast(room_id, {'type': 'player_joined', 'pid': pid,
                                    'name': name}, exclude_ws=ws)
                send_to(ws, game_state_for(game, pid))

            # ── Rejoin ────────────────────────────────────────────────
            elif t == 'rejoin':
                pid     = msg.get('pid')
                room_id = msg.get('room_id', '').upper()
                game    = rm.get_room(room_id)
                if not game or pid not in game.players:
                    send_to(ws, {'type': 'error', 'msg': 'Cannot rejoin'})
                    continue
                client.update({'pid': pid})
                with lock:
                    rooms_clients.setdefault(room_id, []).append(client)
                send_to(ws, {'type': 'rejoined', 'pid': pid, 'room_id': room_id})
                send_to(ws, game_state_for(game, pid))

            # ── Spectate ──────────────────────────────────────────────
            elif t == 'spectate_room':
                room_id = msg.get('room_id', '').upper()
                game    = rm.get_room(room_id)
                if not game:
                    send_to(ws, {'type': 'error', 'msg': 'Room not found'})
                    continue
                client.update({'spectator': True})
                with lock:
                    rooms_clients.setdefault(room_id, []).append(client)
                send_to(ws, {'type': 'spectating', 'room_id': room_id})
                send_to(ws, game_state_for(game, None))

            # ── List rooms ────────────────────────────────────────────
            elif t == 'list_rooms':
                rooms = []
                for rid, g in rm.rooms.items():
                    rooms.append({
                        'room_id': rid,
                        'players': len(g.players),
                        'phase': str(g.phase),
                        'round': g.round,
                    })
                send_to(ws, {'type': 'rooms', 'rooms': rooms})

            # ── Game actions (require room + pid) ─────────────────────
            else:
                if not room_id or not pid:
                    send_to(ws, {'type': 'error', 'msg': 'Not in a room'})
                    continue
                game = rm.get_room(room_id)
                if not game:
                    send_to(ws, {'type': 'error', 'msg': 'Room gone'})
                    continue

                if t == 'start_game':
                    events = game.start_game()
                    broadcast_all(room_id, events)
                    for c in rooms_clients.get(room_id, []):
                        if not c['spectator'] and c['pid']:
                            send_to(c['ws'], game_state_for(game, c['pid']))

                elif t == 'declare':
                    events = game.player_declare(
                        pid, int(msg['steps']), msg['element'])
                    handle_pending(ws, pid, room_id, events)

                elif t == 'sleeping':
                    events = game.player_sleeping_choice(
                        pid, msg['action'],
                        tamer_cid=msg.get('tamer_cid'),
                        dragon_cid=msg.get('dragon_cid'),
                        pair_index=msg.get('pair_index'))
                    handle_pending(ws, pid, room_id, events)

                elif t == 'pick_cards':
                    events = game.player_pick_cards(pid, msg['card_cids'])
                    handle_pending(ws, pid, room_id, events)

                elif t == 'reveal':
                    events = game.reveal_step(pid)
                    handle_pending(ws, pid, room_id, events)

                elif t == 'portal_target':
                    events = game.portal_target_chosen(pid, msg['target_pid'])
                    handle_pending(ws, pid, room_id, events)

                elif t == 'love_tamer':
                    events = game.princess_choose_tamer(pid, msg['tamer_pid'])
                    handle_pending(ws, pid, room_id, events)

                elif t == 'joker_power':
                    power  = msg.get('power')
                    choice = msg.get('choice')
                    if power == 'space':
                        events = game.space_dragon_swap_chosen(pid, choice)
                    elif power == 'forced_wake':
                        events = game.forced_wake_chosen(pid, choice)
                    else:
                        events = []
                    handle_pending(ws, pid, room_id, events)

                else:
                    send_to(ws, {'type': 'error', 'msg': f'Unknown action: {t}'})

    except Exception as ex:
        print(f'WS error [{pid}@{room_id}]: {ex}')
    finally:
        if room_id:
            with lock:
                clients = rooms_clients.get(room_id, [])
                if client in clients:
                    clients.remove(client)

# ── HTTP routes ────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/health')
def health():
    return jsonify({'ok': True, 'rooms': len(rm.rooms),
                    'win_dragons': __import__('game_engine').WIN_DRAGONS,
                    'engine': 'v3.4', 'server': 'v2.0-ws'})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f'Dragon Tamer WebSocket Server starting on port {port}')
    app.run(host='0.0.0.0', port=port, debug=False)
