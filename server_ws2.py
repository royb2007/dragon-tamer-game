"""
Dragon Tamer — WebSocket Server v3.0
Uses the 'websockets' library — matches original requirements.txt
"""
import asyncio
import json
import random
import uuid
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import websockets
from websockets.server import serve
from game_engine import RoomManager, Phase

rm = RoomManager()

# room_id -> list of {ws, pid, name, spectator}
rooms_clients = {}
lock = threading.Lock()

def broadcast_sync(room_id, msg):
    pass  # handled in async

async def send(ws, msg):
    try:
        await ws.send(json.dumps(msg, default=str))
    except Exception:
        pass

async def broadcast(room_id, msg, exclude=None):
    with lock:
        clients = list(rooms_clients.get(room_id, []))
    for c in clients:
        if c['ws'] is exclude:
            continue
        try:
            await c['ws'].send(json.dumps(msg, default=str))
        except Exception:
            pass

async def broadcast_events(room_id, events, pid=None, ws=None):
    for e in events:
        t = e['type']
        # Pause events go only to the relevant human player
        if t == 'portal_choose_target':
            if ws and e['portal_pid'] == pid:
                await send(ws, e)
            else:
                await broadcast(room_id, e)
        elif t == 'love_choose_tamer':
            if ws and e['princess_pid'] == pid:
                await send(ws, e)
            else:
                await broadcast(room_id, e)
        elif t == 'space_dragon_choose_swap':
            if ws and e['space_pid'] == pid:
                await send(ws, e)
            else:
                await broadcast(room_id, e)
        elif t == 'forced_wake_choose':
            if ws and e['pid'] == pid:
                await send(ws, e)
            else:
                await broadcast(room_id, e)
        else:
            await broadcast(room_id, e)

def game_state(game, pid):
    p = game.players.get(pid)
    return {
        'type': 'state',
        'phase': game.phase.value if hasattr(game.phase,'value') else str(game.phase),
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
                'sleeping': [[t.to_dict(),d.to_dict()] for t,d in p2.sleeping],
                'out': p2.out,
                'is_ai': p2.is_ai,
                'skip': p2.pid in game._skipped_this_round,
            }
            for pid2,p2 in game.players.items()
        },
        'me': {
            'pid': pid,
            'hand': [c.to_dict() for c in p.hand],
            'battle': [c.to_dict() for c in p.battle],
            'sleeping': [[t.to_dict(),d.to_dict()] for t,d in p.sleeping],
            'dragon_count': p.dragon_count,
        } if p else None,
        'lead_pid': game._lead_pid() if hasattr(game,'_lead_pid') and game.phase != Phase.WAITING else None,
    }

async def handler(ws):
    pid = None
    room_id = None
    client = {'ws': ws, 'pid': None, 'name': 'Unknown', 'spectator': False}

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                await send(ws, {'type':'error','msg':'Invalid JSON'})
                continue

            t = msg.get('type')

            if t == 'create_room':
                n_ai   = int(msg.get('ai_opponents', 3))
                name   = msg.get('name', 'Player')
                pid    = str(uuid.uuid4())[:8]
                room_id = str(uuid.uuid4())[:6].upper()

                game = rm.create_room(room_id, 1 + n_ai)
                game.add_player(pid, name, is_ai=False)

                strategies = ['Aggressive','Balanced','Conservative','Hoarder',
                              'Adaptive','DragonHunter','Purist','Maximalist',
                              'Minimalist','Opportunist']
                for i in range(n_ai):
                    s = random.choice(strategies)
                    game.add_player(f'ai_{i}', s, is_ai=True, ai_strategy=s)

                client.update({'pid': pid, 'name': name})
                with lock:
                    rooms_clients[room_id] = [client]

                await send(ws, {'type':'room_created','room_id':room_id,'pid':pid,'name':name})
                events = game.start_game()
                await send(ws, game_state(game, pid))
                await broadcast_events(room_id, events, pid, ws)

            elif t == 'join_room':
                room_id = msg.get('room_id','').upper()
                name    = msg.get('name','Player')
                pid     = str(uuid.uuid4())[:8]
                game    = rm.get_room(room_id)
                if not game:
                    await send(ws, {'type':'error','msg':f'Room {room_id} not found'})
                    continue
                result = game.add_player(pid, name, is_ai=False)
                if result.get('error'):
                    await send(ws, {'type':'error','msg':result['error']})
                    continue
                client.update({'pid':pid,'name':name})
                with lock:
                    rooms_clients.setdefault(room_id,[]).append(client)
                await send(ws, {'type':'room_joined','room_id':room_id,'pid':pid,'name':name})
                await broadcast(room_id, {'type':'player_joined','pid':pid,'name':name}, exclude=ws)
                await send(ws, game_state(game, pid))

            elif t == 'rejoin':
                pid     = msg.get('pid')
                room_id = msg.get('room_id','').upper()
                game    = rm.get_room(room_id)
                if not game or pid not in game.players:
                    await send(ws, {'type':'error','msg':'Cannot rejoin'})
                    continue
                client.update({'pid':pid})
                with lock:
                    rooms_clients.setdefault(room_id,[]).append(client)
                await send(ws, {'type':'rejoined','pid':pid,'room_id':room_id})
                await send(ws, game_state(game, pid))

            elif t == 'spectate_room':
                room_id = msg.get('room_id','').upper()
                game    = rm.get_room(room_id)
                if not game:
                    await send(ws, {'type':'error','msg':'Room not found'})
                    continue
                client.update({'spectator':True})
                with lock:
                    rooms_clients.setdefault(room_id,[]).append(client)
                await send(ws, {'type':'spectating','room_id':room_id})
                await send(ws, game_state(game, None))

            elif t == 'list_rooms':
                rooms = [{'room_id':rid,'players':len(g.players),
                          'phase':str(g.phase),'round':g.round}
                         for rid,g in rm.rooms.items()]
                await send(ws, {'type':'rooms','rooms':rooms})

            else:
                if not room_id or not pid:
                    await send(ws, {'type':'error','msg':'Not in a room'})
                    continue
                game = rm.get_room(room_id)
                if not game:
                    await send(ws, {'type':'error','msg':'Room gone'})
                    continue

                if t == 'declare':
                    events = game.player_declare(pid, int(msg['steps']), msg['element'])
                    await broadcast_events(room_id, events, pid, ws)

                elif t == 'sleeping':
                    events = game.player_sleeping_choice(
                        pid, msg['action'],
                        tamer_cid=msg.get('tamer_cid'),
                        dragon_cid=msg.get('dragon_cid'),
                        pair_index=msg.get('pair_index'))
                    await broadcast_events(room_id, events, pid, ws)

                elif t == 'pick_cards':
                    events = game.player_pick_cards(pid, msg['card_cids'])
                    await broadcast_events(room_id, events, pid, ws)

                elif t == 'reveal':
                    events = game.reveal_step(pid)
                    await broadcast_events(room_id, events, pid, ws)

                elif t == 'portal_target':
                    events = game.portal_target_chosen(pid, msg['target_pid'])
                    await broadcast_events(room_id, events, pid, ws)

                elif t == 'love_tamer':
                    events = game.princess_choose_tamer(pid, msg['tamer_pid'])
                    await broadcast_events(room_id, events, pid, ws)

                elif t == 'joker_power':
                    power  = msg.get('power')
                    choice = msg.get('choice')
                    if power == 'space':
                        events = game.space_dragon_swap_chosen(pid, choice)
                    elif power == 'forced_wake':
                        events = game.forced_wake_chosen(pid, choice)
                    else:
                        events = []
                    await broadcast_events(room_id, events, pid, ws)

    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as ex:
        print(f'WS error [{pid}@{room_id}]: {ex}')
    finally:
        if room_id:
            with lock:
                clients = rooms_clients.get(room_id, [])
                if client in clients:
                    clients.remove(client)

# ── HTTP server for index.html ─────────────────────────────────────────────

class GameHTTPHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.path = '/index.html'
        if self.path == '/health':
            body = json.dumps({'ok':True,'engine':'v3.4','server':'v3.0-ws'}).encode()
            self.send_response(200)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',len(body))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()
    def log_message(self, *args): pass

async def main():
    import os
    port = int(os.environ.get('PORT', 5000))
    print(f'Dragon Tamer Server v3.0 starting on port {port}')

    # HTTP server in background thread
    http = HTTPServer(('0.0.0.0', 8080), GameHTTPHandler)
    t = threading.Thread(target=http.serve_forever, daemon=True)
    t.start()
    print('HTTP server on port 8080')

    # WebSocket server on main port
    async with serve(handler, '0.0.0.0', port):
        print(f'WebSocket server on port {port}')
        await asyncio.Future()  # run forever

if __name__ == '__main__':
    asyncio.run(main())
