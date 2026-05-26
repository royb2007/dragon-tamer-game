"""
Dragon Tamer — Game Server v1.0
Flask REST + SSE server bridging the game_engine to frontend clients.

Routes:
  POST /room/create          Create a room
  POST /room/<id>/join       Join as human or AI
  POST /room/<id>/start      Start the game
  GET  /room/<id>/state      Full game state (for the calling player)
  GET  /room/<id>/stream     SSE stream of events
  POST /room/<id>/declare    Leader declares steps + element
  POST /room/<id>/sleep      Player sleeping choice
  POST /room/<id>/pick       Player picks battle cards
  POST /room/<id>/reveal     Trigger next step reveal
  POST /room/<id>/portal     Human chooses portal steal target
  POST /room/<id>/love       Princess chooses tamer
  POST /room/<id>/swap       Space Dragon swap choice
  POST /room/<id>/wake       Forced wake choice
  GET  /rooms                List all active rooms
  DELETE /room/<id>          Delete a room
"""

import json
import queue
import threading
import time
import uuid
from functools import wraps
from flask import Flask, request, jsonify, Response, g, send_file
from game_engine import RoomManager, Phase

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

# ── State ──────────────────────────────────────────────────────────────────
rm       = RoomManager()
# event_queues[room_id][client_id] = Queue()
event_queues: dict[str, dict[str, queue.Queue]] = {}
lock = threading.Lock()

# ── Helpers ────────────────────────────────────────────────────────────────

def get_game(room_id):
    game = rm.get_room(room_id)
    if not game:
        return None, jsonify({'error': f'Room {room_id} not found'}), 404
    return game, None, None

def broadcast(room_id: str, events: list):
    """Push engine events to every SSE subscriber of a room."""
    if not events:
        return
    with lock:
        queues = event_queues.get(room_id, {})
    payload = json.dumps(events, default=str)
    for q in queues.values():
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass

def require_json(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not request.is_json:
            return jsonify({'error': 'Content-Type must be application/json'}), 400
        return f(*args, **kwargs)
    return wrapper

def handle_events(room_id, events):
    """Broadcast events and return them as JSON response."""
    broadcast(room_id, events)
    return jsonify({'ok': True, 'events': events})

# ── Room management ─────────────────────────────────────────────────────────

@app.route('/rooms', methods=['GET'])
def list_rooms():
    return jsonify(rm.list_rooms())

@app.route('/room/create', methods=['POST'])
@require_json
def create_room():
    data       = request.get_json()
    room_id    = data.get('room_id') or str(uuid.uuid4())[:8]
    max_players = int(data.get('max_players', 6))
    if rm.get_room(room_id):
        return jsonify({'error': f'Room {room_id} already exists'}), 409
    rm.create_room(room_id, max_players)
    with lock:
        event_queues[room_id] = {}
    return jsonify({'ok': True, 'room_id': room_id, 'max_players': max_players})

@app.route('/room/<room_id>', methods=['DELETE'])
def delete_room(room_id):
    rm.delete_room(room_id)
    with lock:
        event_queues.pop(room_id, None)
    return jsonify({'ok': True})

# ── Players ─────────────────────────────────────────────────────────────────

@app.route('/room/<room_id>/join', methods=['POST'])
@require_json
def join_room(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code

    data     = request.get_json()
    pid      = data.get('pid') or str(uuid.uuid4())[:8]
    name     = data.get('name', pid)
    is_ai    = bool(data.get('is_ai', False))
    strategy = data.get('ai_strategy', 'Balanced')

    result = game.add_player(pid, name, is_ai=is_ai, ai_strategy=strategy)
    if result.get('error'):
        return jsonify(result), 400
    broadcast(room_id, [{'type': 'player_joined', 'pid': pid, 'name': name, 'is_ai': is_ai}])
    return jsonify({'ok': True, 'pid': pid, 'name': name})

@app.route('/room/<room_id>/fill_ai', methods=['POST'])
def fill_ai(room_id):
    """Fill remaining seats with AI players."""
    game, err, code = get_game(room_id)
    if err: return err, code
    game.fill_with_ai()
    broadcast(room_id, [{'type': 'room_filled'}])
    return jsonify({'ok': True, 'players': len(game.players)})

# ── Game flow ────────────────────────────────────────────────────────────────

@app.route('/room/<room_id>/start', methods=['POST'])
def start_game(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    events = game.start_game()
    return handle_events(room_id, events)

@app.route('/room/<room_id>/state', methods=['GET'])
def get_state(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    pid = request.args.get('pid')
    state = game.player_state(pid) if pid else game.public_state()
    return jsonify(state)

# ── Player actions ───────────────────────────────────────────────────────────

@app.route('/room/<room_id>/declare', methods=['POST'])
@require_json
def declare(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data    = request.get_json()
    pid     = data['pid']
    steps   = int(data['steps'])
    element = data['element']           # 'Hearts' | 'Diamonds' | 'Clubs' | 'Spades'
    events  = game.player_declare(pid, steps, element)
    return handle_events(room_id, events)

@app.route('/room/<room_id>/sleep', methods=['POST'])
@require_json
def sleep(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data        = request.get_json()
    pid         = data['pid']
    action      = data['action']        # 'sleep' | 'wake' | 'pass'
    tamer_cid   = data.get('tamer_cid')
    dragon_cid  = data.get('dragon_cid')
    pair_index  = data.get('pair_index')
    events = game.player_sleeping_choice(pid, action,
                                         tamer_cid=tamer_cid,
                                         dragon_cid=dragon_cid,
                                         pair_index=pair_index)
    return handle_events(room_id, events)

@app.route('/room/<room_id>/pick', methods=['POST'])
@require_json
def pick(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data      = request.get_json()
    pid       = data['pid']
    card_cids = data['card_cids']       # list of int card ids
    events    = game.player_pick_cards(pid, card_cids)
    return handle_events(room_id, events)

@app.route('/room/<room_id>/reveal', methods=['POST'])
@require_json
def reveal(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data   = request.get_json()
    pid    = data.get('pid') or game._lead_pid()
    events = game.reveal_step(pid)

    # Handle any paused states (portal / love / space dragon / forced wake)
    all_events = list(events)
    for e in events:
        if e['type'] == 'portal_choose_target':
            # AI auto-resolve or return pause for human
            if not game.players[e['portal_pid']].is_ai:
                return handle_events(room_id, all_events)
        elif e['type'] == 'love_choose_tamer':
            if not game.players[e['princess_pid']].is_ai:
                return handle_events(room_id, all_events)
        elif e['type'] == 'space_dragon_choose_swap':
            if not game.players[e['space_pid']].is_ai:
                return handle_events(room_id, all_events)
        elif e['type'] == 'forced_wake_choose':
            if not game.players[e['pid']].is_ai:
                return handle_events(room_id, all_events)

    return handle_events(room_id, all_events)

@app.route('/room/<room_id>/portal', methods=['POST'])
@require_json
def portal(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data       = request.get_json()
    pid        = data['pid']            # portal owner
    target_pid = data['target_pid']    # chosen steal target
    events     = game.portal_target_chosen(pid, target_pid)
    return handle_events(room_id, events)

@app.route('/room/<room_id>/love', methods=['POST'])
@require_json
def love(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data       = request.get_json()
    pid        = data['pid']            # princess pid
    tamer_pid  = data['tamer_pid']     # chosen tamer
    events     = game.princess_choose_tamer(pid, tamer_pid)
    return handle_events(room_id, events)

@app.route('/room/<room_id>/swap', methods=['POST'])
@require_json
def swap(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data       = request.get_json()
    pid        = data['pid']            # space dragon winner
    target_pid = data.get('target_pid')  # None = pass
    events     = game.space_dragon_swap_chosen(pid, target_pid)
    return handle_events(room_id, events)

@app.route('/room/<room_id>/wake', methods=['POST'])
@require_json
def wake(room_id):
    game, err, code = get_game(room_id)
    if err: return err, code
    data         = request.get_json()
    pid          = data['pid']
    pair_indices = data['pair_indices']  # list of ints
    events       = game.forced_wake_chosen(pid, pair_indices)
    return handle_events(room_id, events)

# ── SSE stream ───────────────────────────────────────────────────────────────

@app.route('/room/<room_id>/stream', methods=['GET'])
def stream(room_id):
    """
    Server-Sent Events stream.
    Connect with:  const es = new EventSource('/room/ROOM_ID/stream?pid=PLAYER_ID')
    Each message is a JSON array of engine events.
    """
    game, err, code = get_game(room_id)
    if err: return err, code

    client_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue(maxsize=200)

    with lock:
        if room_id not in event_queues:
            event_queues[room_id] = {}
        event_queues[room_id][client_id] = q

    # Send current state immediately on connect
    pid   = request.args.get('pid')
    state = game.player_state(pid) if pid else game.public_state()
    q.put_nowait(json.dumps([{'type': 'connected', 'state': state}], default=str))

    def generate():
        try:
            while True:
                try:
                    data = q.get(timeout=25)
                    yield f'data: {data}\n\n'
                except queue.Empty:
                    yield ': ping\n\n'   # keep-alive
        except GeneratorExit:
            pass
        finally:
            with lock:
                event_queues.get(room_id, {}).pop(client_id, None)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Access-Control-Allow-Origin': '*',
        }
    )

# ── Health ───────────────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'ok': True,
        'rooms': len(rm.rooms),
        'win_dragons': __import__('game_engine').WIN_DRAGONS,
    })

@app.route('/', methods=['GET'])
def index():
    return send_file('index.html')

@app.route('/api')
def api_info():
    return jsonify({
        'name': 'Dragon Tamer Game Server',
        'version': '1.0',
        'engine': 'v3.4',
        'routes': [r.rule for r in app.url_map.iter_rules()],
    })

# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('DEBUG', 'false').lower() == 'true'
    print(f'Dragon Tamer Server starting on port {port}')
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
