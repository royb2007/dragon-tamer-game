import random

DRAGONS = [
    {"name": "Inferno", "type": "Fire", "attack": 8, "defense": 4, "hp": 20, "emoji": "🔥"},
    {"name": "Tidal", "type": "Water", "attack": 6, "defense": 7, "hp": 22, "emoji": "🌊"},
    {"name": "Thornback", "type": "Earth", "attack": 5, "defense": 9, "hp": 25, "emoji": "🌿"},
    {"name": "Stormwing", "type": "Lightning", "attack": 10, "defense": 3, "hp": 18, "emoji": "⚡"},
    {"name": "Frostclaw", "type": "Ice", "attack": 7, "defense": 6, "hp": 21, "emoji": "❄️"},
    {"name": "Shadowfang", "type": "Dark", "attack": 9, "defense": 5, "hp": 19, "emoji": "🌑"},
]

TYPE_ADVANTAGES = {
    "Fire":      "Earth",
    "Water":     "Fire",
    "Earth":     "Lightning",
    "Lightning": "Water",
    "Ice":       "Dark",
    "Dark":      "Ice",
}

class GameEngine:
    def __init__(self):
        self.reset()

    def reset(self):
        deck = [dict(d) for d in DRAGONS]
        random.shuffle(deck)
        self.player_hand = deck[:3]
        self.enemy_hand  = [dict(random.choice(DRAGONS)) for _ in range(3)]
        self.player_hp   = 30
        self.enemy_hp    = 30
        self.turn        = 1
        self.log         = ["Game started! Choose a dragon to battle."]
        self.game_over   = False
        self.winner      = None

    def get_state(self):
        return {
            "player_hand": self.player_hand,
            "player_hp":   self.player_hp,
            "enemy_hp":    self.enemy_hp,
            "turn":        self.turn,
            "log":         self.log[-5:],   # last 5 log lines
            "game_over":   self.game_over,
            "winner":      self.winner,
        }

    def process_action(self, action, data):
        if self.game_over:
            return {"message": "Game is over. Send 'restart' to play again."}

        if action == "restart":
            self.reset()
            return {"message": "New game started!"}

        if action == "play_card":
            card_index = data.get("card_index", 0)
            if card_index < 0 or card_index >= len(self.player_hand):
                return {"message": "Invalid card index."}

            player_card = self.player_hand[card_index]
            enemy_card  = random.choice(self.enemy_hand)

            # Calculate damage
            p_dmg = self._calc_damage(player_card, enemy_card)
            e_dmg = self._calc_damage(enemy_card, player_card)

            self.enemy_hp  -= p_dmg
            self.player_hp -= e_dmg

            msg = (
                f"Turn {self.turn}: Your {player_card['emoji']}{player_card['name']} "
                f"vs Enemy {enemy_card['emoji']}{enemy_card['name']}. "
                f"You dealt {p_dmg} dmg, took {e_dmg} dmg."
            )
            self.log.append(msg)
            self.turn += 1

            # Check win/lose
            if self.enemy_hp <= 0 and self.player_hp <= 0:
                self.game_over = True
                self.winner = "draw"
                self.log.append("It's a DRAW!")
            elif self.enemy_hp <= 0:
                self.game_over = True
                self.winner = "player"
                self.log.append("🏆 You WIN!")
            elif self.player_hp <= 0:
                self.game_over = True
                self.winner = "enemy"
                self.log.append("💀 You LOSE!")

            return {"message": msg}

        return {"message": f"Unknown action: {action}"}

    def _calc_damage(self, attacker, defender):
        base = attacker["attack"]
        # Type advantage bonus
        if TYPE_ADVANTAGES.get(attacker["type"]) == defender["type"]:
            base = int(base * 1.5)
        # Defense reduction
        damage = max(1, base - defender["defense"] // 2)
        # Small random factor
        damage += random.randint(0, 2)
        return damage
