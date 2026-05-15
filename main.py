import asyncio
import websockets
import json
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from game_engine import GameEngine

# Replit 2026: must bind to 0.0.0.0, read PORT from environment
PORT = int(os.environ.get("PORT", 8080))
HOST = "0.0.0.0"

connected_players = {}

# ── Health check HTTP server ──────────────────────────────────────────────────
# Replit deployment health check hits GET / and expects 200 within 5 seconds.

class GameHTTPHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            try:
                with open("index.html", "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except FileNotFoundError:
                self._send_text(200, "Dragon Tamer is running!")
        else:
            self._send_text(404, "Not found")

    def _send_text(self, code, text):
        body = text.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # silence HTTP access logs


def start_http_server():
    """Run HTTP server in a background thread on PORT+1."""
    http_port = PORT + 1
    server = HTTPServer((HOST, http_port), GameHTTPHandler)
    print(f"HTTP server (health + UI) on port {http_port}")
    server.serve_forever()


# ── WebSocket handler ─────────────────────────────────────────────────────────

async def handle_player(websocket):
    player_id = id(websocket)
    game = GameEngine()
    connected_players[player_id] = {"websocket": websocket, "game": game}
    print(f"Player {player_id} connected. Total: {len(connected_players)}")

    try:
        await websocket.send(json.dumps({
            "type": "init",
            "message": "Welcome to Dragon Tamer!",
            "state": game.get_state()
        }))

        async for message in websocket:
            try:
                data = json.loads(message)
            except json.JSONDecodeError:
                continue
            action = data.get("action")
            result = game.process_action(action, data)
            await websocket.send(json.dumps({
                "type": "update",
                "result": result,
                "state": game.get_state()
            }))

    except websockets.exceptions.ConnectionClosedOK:
        print(f"Player {player_id} disconnected cleanly.")
    except websockets.exceptions.ConnectionClosedError as e:
        print(f"Player {player_id} disconnected with error: {e}")
    finally:
        connected_players.pop(player_id, None)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    print(f"Dragon Tamer WebSocket server starting on {HOST}:{PORT}")

    # Start HTTP server in background thread (health check + serves index.html)
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    async with websockets.serve(handle_player, HOST, PORT):
        print(f"✅ Server running! WebSocket on :{PORT}, HTTP on :{PORT+1}")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
