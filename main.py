import asyncio
import websockets
import json
import os
from game_engine import GameEngine

PORT = int(os.environ.get("PORT", 8080))
HOST = "0.0.0.0"

connected_players = {}

def get_index_html():
    try:
        with open("index.html", "rb") as f:
            return f.read()
    except FileNotFoundError:
        return b"<h1>Dragon Tamer - index.html not found</h1>"

async def handle_connection(websocket):
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
        print(f"Player {player_id} connection error: {e}")
    finally:
        connected_players.pop(player_id, None)


async def health_check(path, request_headers):
    if request_headers.get("Upgrade", "").lower() == "websocket":
        return None
    html = get_index_html()
    headers = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("Content-Length", str(len(html))),
    ]
    return 200, headers, html


async def main():
    print(f"Dragon Tamer starting on {HOST}:{PORT}")
    async with websockets.serve(
        handle_connection,
        HOST,
        PORT,
        process_request=health_check
    ):
        print(f"Server running on port {PORT}")
        print(f"Open the Replit webview to play!")
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
