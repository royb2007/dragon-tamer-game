"""
Dragon Tamer — All Fixes Script
Run in Replit Shell: python3 all_fixes.py
"""
import re

results = []

# ══════════════════════════════════════════════════════
# game_engine.py FIXES
# ══════════════════════════════════════════════════════
with open('game_engine.py', 'r') as f:
    ge = f.read()

# FIX E — CID reset
old = """def build_deck() -> List[Card]:
    global _card_counter
    _card_counter = 0
    deck = []"""
new = """def build_deck() -> List[Card]:
    global _card_counter
    deck = []"""
if old in ge:
    ge = ge.replace(old, new)
    results.append("✅ FIX E: CID counter no longer resets between games")
else:
    results.append("⚠️  FIX E: pattern not found — check manually")

# FIX A — Step-skipping: replace timestamp debounce with step-based dedup
old = """        # Debounce: Replit's WS proxy triple-sends each message from the browser.
        now = time.time()
        if now - getattr(self, '_last_reveal_ts', 0.0) < 0.4:
            return []
        self._last_reveal_ts = now"""
new = """        # Dedup: ignore if current_step hasn't changed since last call.
        # This is bulletproof against Replit's triple-send regardless of timing.
        last_step = getattr(self, '_last_reveal_step', -1)
        if last_step == self.current_step:
            return []
        self._last_reveal_step = self.current_step"""
if old in ge:
    ge = ge.replace(old, new)
    results.append("✅ FIX A: Step-skipping fixed (step-based dedup)")
else:
    results.append("⚠️  FIX A: pattern not found — check manually")

# FIX C — Dragon duel: proper tie resolution
old = """    # ── Dragon always beats every non-Tamer card ─────────────────────────
    # If a Dragon is in play and no Tamer challenged it, Dragon wins.
    # We pick the highest-ranked Dragon (Ace > Joker by effective_rank).
    if has_dragon:
        dragon_entries = [e for e in valid if e['card'].is_dragon]
        best_dragon = max(dragon_entries,
                          key=lambda e: e['card'].effective_rank(el))
        result['winner_pid'] = best_dragon['pid']
        # Fall through (do NOT return yet) so Space Dragon / Portal can fire below"""
new = """    # ── Dragon always beats every non-Tamer card ─────────────────────────
    # If a Dragon is in play and no Tamer challenged it, Dragon wins.
    # Multiple dragons at same rank = duel → leader wins the tie.
    if has_dragon:
        dragon_entries = [e for e in valid if e['card'].is_dragon]
        best_rank = max(e['card'].effective_rank(el) for e in dragon_entries)
        top_dragons = [e for e in dragon_entries
                       if e['card'].effective_rank(el) == best_rank]
        if len(top_dragons) == 1:
            result['winner_pid'] = top_dragons[0]['pid']
        else:
            # True duel — leader wins the tie
            result['winner_pid'] = lead_pid
            result['special_events'].append(
                f"⚔️ Dragon Duel! Equal rank — leader wins the clash!")
        # Fall through (do NOT return yet) so Space Dragon / Portal can fire below"""
if old in ge:
    ge = ge.replace(old, new)
    results.append("✅ FIX C: Dragon duel logic (leader wins ties)")
else:
    results.append("⚠️  FIX C: pattern not found — check manually")

# FIX B — Space Dragon: broadcast seat change event
old = """    def resolve_space_dragon(self, pid: str, choice: str) -> List[dict]:
        \"\"\"Apply the human player's Space Dragon choice and continue the round.\"\"\"
        if self.pending_space_dragon != pid:
            return []
        name = self.players[pid].name
        if choice == 'change':
            self._do_space_dragon_seat_change(pid)
            self._log(f"🌌 {name} warps through space — seat order changed!")
        else:
            self._log(f"🌌 {name} lets the Space Dragon power fade unused.")
        self.pending_space_dragon = None
        # Re-enable button on client side by emitting the deferred phase event
        if self.current_step >= self.declared_steps:
            return self._end_round()
        return [{'type': 'phase_change', 'phase': Phase.REVEAL,
                 'next_step': self.current_step + 1}]"""
new = """    def resolve_space_dragon(self, pid: str, choice: str) -> List[dict]:
        \"\"\"Apply the human player's Space Dragon choice and continue the round.\"\"\"
        if self.pending_space_dragon != pid:
            return []
        name = self.players[pid].name
        events = []
        if choice == 'change':
            self._do_space_dragon_seat_change(pid)
            self._log(f"🌌 {name} warps through space — seat order changed!")
            # Broadcast new seat order so all clients update their table
            events.append({'type': 'seat_changed',
                           'new_order': self.order,
                           'lead_pid': self._lead_pid()})
        else:
            self._log(f"🌌 {name} lets the Space Dragon power fade unused.")
        self.pending_space_dragon = None
        if self.current_step >= self.declared_steps:
            return events + self._end_round()
        return events + [{'type': 'phase_change', 'phase': Phase.REVEAL,
                          'next_step': self.current_step + 1}]"""
if old in ge:
    ge = ge.replace(old, new)
    results.append("✅ FIX B: Space Dragon broadcasts seat_changed event")
else:
    results.append("⚠️  FIX B: pattern not found — check manually")

# Add accum tracking to public_dict for pile display
old = """    def public_dict(self) -> dict:
        return {
            'pid': self.pid, 'name': self.name,
            'dragon_count': self.dragon_count,
            'hand_count': len(self.hand),
            'battle_count': len(self.battle),
            'sleeping_count': len(self.sleeping),
            'out': self.out, 'is_ai': self.is_ai,
        }"""
new = """    def public_dict(self) -> dict:
        return {
            'pid': self.pid, 'name': self.name,
            'dragon_count': self.dragon_count,
            'hand_count': len(self.hand),
            'battle_count': len(self.battle),
            'sleeping_count': len(self.sleeping),
            'accum_count': len(self.accum),
            'accum': [c.to_dict() for c in self.accum],
            'out': self.out, 'is_ai': self.is_ai,
        }"""
if old in ge:
    ge = ge.replace(old, new)
    results.append("✅ FIX PILE: accum cards now included in public_dict")
else:
    results.append("⚠️  FIX PILE: public_dict pattern not found")

with open('game_engine.py', 'w') as f:
    f.write(ge)

# ══════════════════════════════════════════════════════
# index.html FIXES
# ══════════════════════════════════════════════════════
with open('index.html', 'r') as f:
    html = f.read()

# FIX D — dedup key includes round number
old = "      const revKey = msg.step + ':' + (msg.entries||[]).map(e=>e.card?.cid).sort().join(',');"
new = "      const revKey = (gameState?.round||0) + ':' + msg.step + ':' + (msg.entries||[]).map(e=>e.card?.cid).sort().join(',');"
if old in html:
    html = html.replace(old, new)
    results.append("✅ FIX D: dedup key includes round number")
else:
    results.append("⚠️  FIX D: dedup key pattern not found")

# FIX B — handle seat_changed in frontend
old = "    case 'game_over':\n      showGameOver(msg); break;"
new = """    case 'seat_changed':
      if(gameState){ gameState.order=msg.new_order; if(msg.lead_pid) gameState.lead_pid=msg.lead_pid; }
      renderTable();
      addLog('🌌 Seat order changed!','imp');
      break;

    case 'game_over':
      showGameOver(msg); break;"""
if "case 'game_over':\n      showGameOver(msg); break;" in html:
    html = html.replace("case 'game_over':\n      showGameOver(msg); break;", new)
    results.append("✅ FIX B client: seat_changed handler added")
else:
    results.append("⚠️  FIX B client: game_over pattern not found")

# DECLARATION PANEL — always visible, disabled by default
# Replace the declareSection HTML
old_decl_section = """    <!-- Player Declaration (leader declares; others wait) -->
    <div class="section hidden" id="declareSection">
      <div class="section-title">👑 Player Declaration</div>
      <div class="declare-leader-line" id="declareLeaderLine">— Waiting —</div>
      <!-- Controls shown only to the leader -->
      <div id="declareControls" class="hidden">
        <div class="declare-grid">
          <div class="control-row">
            <label>Steps</label>
            <div class="btn-group" id="stepsGroup">
              <button class="step-btn" onclick="setSteps(1)">1</button>
              <button class="step-btn" onclick="setSteps(2)">2</button>
              <button class="step-btn active" onclick="setSteps(3)">3</button>
              <button class="step-btn" onclick="setSteps(4)">4</button>
              <button class="step-btn" onclick="setSteps(5)">5</button>
            </div>
          </div>
          <div class="control-row">
            <label>Element</label>
            <div class="btn-group" id="suitGroup">
              <button class="suit-btn active" onclick="setSuit('Hearts')" title="Fire">♥</button>
              <button class="suit-btn" onclick="setSuit('Clubs')" title="Water">♣</button>
              <button class="suit-btn" onclick="setSuit('Diamonds')" title="Air">♦</button>
              <button class="suit-btn" onclick="setSuit('Spades')" title="Earth">♠</button>
            </div>
          </div>
        </div>
      </div>
      <div id="declareWait" class="waiting-declare hidden"></div>
    </div>"""

new_decl_section = """    <!-- Player Declaration — always visible -->
    <div class="section" id="declareSection">
      <div class="section-title">👑 Player Declaration</div>
      <div class="declare-leader-line" id="declareLeaderLine">— Waiting for game to start —</div>
      <div class="declare-grid">
        <div class="control-row">
          <label>Steps</label>
          <div class="btn-group" id="stepsGroup">
            <button class="step-btn" id="step1" onclick="setSteps(1)">1</button>
            <button class="step-btn" id="step2" onclick="setSteps(2)">2</button>
            <button class="step-btn" id="step3" onclick="setSteps(3)">3</button>
            <button class="step-btn" id="step4" onclick="setSteps(4)">4</button>
            <button class="step-btn" id="step5" onclick="setSteps(5)">5</button>
          </div>
        </div>
        <div class="control-row">
          <label>Element</label>
          <div class="btn-group" id="suitGroup">
            <button class="suit-btn" id="suitH" onclick="setSuit('Hearts')" title="Fire">♥</button>
            <button class="suit-btn" id="suitC" onclick="setSuit('Clubs')" title="Water">♣</button>
            <button class="suit-btn" id="suitD" onclick="setSuit('Diamonds')" title="Air">♦</button>
            <button class="suit-btn" id="suitS" onclick="setSuit('Spades')" title="Earth">♠</button>
          </div>
        </div>
      </div>
      <div id="declareWait" class="waiting-declare hidden"></div>
    </div>"""

if old_decl_section in html:
    html = html.replace(old_decl_section, new_decl_section)
    results.append("✅ FIX PANEL: Declaration panel always visible")
else:
    results.append("⚠️  FIX PANEL: declaration section pattern not found")

# Add CSS for disabled declaration buttons
old_css = """.step-btn.active,.suit-btn.active{background:var(--gold);color:var(--navy);border-color:var(--gold);}"""
new_css = """.step-btn.active,.suit-btn.active{background:var(--gold);color:var(--navy);border-color:var(--gold);}
.step-btn.inactive,.suit-btn.inactive{opacity:.28;cursor:not-allowed;pointer-events:none;}
.step-btn.display-only,.suit-btn.display-only{opacity:.6;cursor:default;pointer-events:none;}"""
if old_css in html:
    html = html.replace(old_css, new_css)
    results.append("✅ FIX PANEL CSS: disabled/display-only styles added")
else:
    results.append("⚠️  FIX PANEL CSS: pattern not found")

# Add CSS for card pile display on table
old_css2 = """.seat-meta{font-size:9px;color:rgba(253,246,227,.38);font-family:'Cinzel',serif;}"""
new_css2 = """.seat-meta{font-size:9px;color:rgba(253,246,227,.38);font-family:'Cinzel',serif;}
.seat-pile{display:flex;flex-wrap:wrap;gap:1px;max-width:80px;margin-top:2px;justify-content:center;}
.pile-card-mini{width:14px;height:20px;border-radius:2px;border:1px solid rgba(184,134,11,.5);
  font-size:6px;display:flex;align-items:center;justify-content:center;
  font-family:'Cinzel',serif;font-weight:700;flex-shrink:0;}
.pile-card-mini.h,.pile-card-mini.d{background:#f0e4c0;color:#7B1515;}
.pile-card-mini.c,.pile-card-mini.s{background:#1A1E2E;color:#e8e0d0;}
.pile-card-mini.js{background:#2A1A4E;color:#C39BD3;}
.pile-card-mini.jt{background:#2E1600;color:#E8A020;}"""
if old_css2 in html:
    html = html.replace(old_css2, new_css2)
    results.append("✅ FIX PILE CSS: pile card styles added")
else:
    results.append("⚠️  FIX PILE CSS: seat-meta pattern not found")

# Update setPhase to control declaration panel state
old_phase = """  declSec.classList.add('hidden');"""
new_phase = """  // Declaration panel is always visible — just update its state
  updateDeclarePanel(newPhase, data);"""
if old_phase in html:
    html = html.replace(old_phase, new_phase, 1)
    results.append("✅ FIX PANEL PHASE: setPhase updated")
else:
    results.append("⚠️  FIX PANEL PHASE: pattern not found")

# Remove the old isMe/declareControls logic from setPhase leader_declare block
old_leader = """    // Player Declaration section
    declSec.classList.remove('hidden');
    const leaderLine = document.getElementById('declareLeaderLine');
    const ctrl = document.getElementById('declareControls');
    const wait = document.getElementById('declareWait');
    leaderLine.textContent = `Leader: ${nameOf(leaderPid)}`;
    if(isMe){
      ctrl.classList.remove('hidden'); wait.classList.add('hidden');
    } else {
      ctrl.classList.add('hidden'); wait.classList.remove('hidden');
      wait.textContent = `Waiting for ${nameOf(leaderPid)} to declare...`;
    }"""
new_leader = """    const leaderLine = document.getElementById('declareLeaderLine');
    if(leaderLine) leaderLine.textContent = isMe
      ? `👑 You are the Leader`
      : `⏳ Waiting for ${nameOf(leaderPid)} to declare...`;"""
if old_leader in html:
    html = html.replace(old_leader, new_leader)
    results.append("✅ FIX PANEL LEADER: leader declare block updated")
else:
    results.append("⚠️  FIX PANEL LEADER: leader block not found")

# Add updateDeclarePanel function before connect()
old_init = """// ══════════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════════
connect();"""
new_init = """// ══════════════════════════════════════════════════════
//  DECLARATION PANEL
// ══════════════════════════════════════════════════════
function updateDeclarePanel(phase, data) {
  const isLeader = (data.leader_pid || gameState?.lead_pid) === myPid;
  const isActive = phase === 'leader_declare' && isLeader;
  const leaderPid = data.leader_pid || gameState?.lead_pid;

  // Buttons: active only if I'm leader in declare phase
  document.querySelectorAll('.step-btn').forEach(b => {
    b.classList.toggle('inactive', !isActive);
  });
  document.querySelectorAll('.suit-btn').forEach(b => {
    b.classList.toggle('inactive', !isActive);
  });

  // If AI declared or game not started — show declared values greyed
  if(phase === 'pick_cards' || phase === 'reveal') {
    const steps = gameState?.declared_steps || 0;
    const el = gameState?.declared_el || '';
    // Highlight declared values in display-only mode
    document.querySelectorAll('.step-btn').forEach((b,i) => {
      b.classList.remove('active','inactive','display-only');
      if(i+1 === steps) b.classList.add('display-only','active');
      else b.classList.add('inactive');
    });
    document.querySelectorAll('.suit-btn').forEach((b,i) => {
      const suits = ['Hearts','Clubs','Diamonds','Spades'];
      b.classList.remove('active','inactive','display-only');
      if(suits[i] === el) b.classList.add('display-only','active');
      else b.classList.add('inactive');
    });
    const ll = document.getElementById('declareLeaderLine');
    if(ll) ll.textContent = `👑 ${nameOf(leaderPid)} · ${steps} step${steps>1?'s':''} · ${SUIT_EL[el]||el} ${SUIT_SYM[el]||''}`;
  } else if(phase === 'waiting' || phase === 'end_round') {
    // All inactive, no highlight
    document.querySelectorAll('.step-btn,.suit-btn').forEach(b => {
      b.classList.remove('active','display-only');
      b.classList.add('inactive');
    });
    const ll = document.getElementById('declareLeaderLine');
    if(ll) ll.textContent = '— Waiting —';
  }
}

// ══════════════════════════════════════════════════════
//  INIT
// ══════════════════════════════════════════════════════
connect();"""
if old_init in html:
    html = html.replace(old_init, new_init)
    results.append("✅ FIX PANEL FN: updateDeclarePanel function added")
else:
    results.append("⚠️  FIX PANEL FN: INIT pattern not found")

# Update declaration event to refresh panel
old_decl_ev = """      // ── Always sync declared_steps, declared_el AND lead_pid from declaration event ──
      if(gameState){
        gameState.declared_steps=msg.steps;
        gameState.declared_el=msg.element;
        gameState.lead_pid=msg.pid;
      }
      addLog(`👑 ${nameOf(msg.pid)} declares: ${msg.steps} steps · ${SUIT_EL[msg.element]} ${SUIT_SYM[msg.element]}`,'imp');
      renderTable();
      const _ds=document.getElementById('declSummary');
      if(_ds){
        const _sym={'Hearts':'♥','Clubs':'♣','Diamonds':'♦','Spades':'♠'};
        const _nm={'Hearts':'Fire','Clubs':'Water','Diamonds':'Air','Spades':'Earth'};
        _ds.textContent=`👑 ${nameOf(msg.pid)} · ${msg.steps} step${msg.steps>1?'s':''} · ${_nm[msg.element]} ${_sym[msg.element]}`;
        _ds.classList.remove('hidden');
      }"""
new_decl_ev = """      if(gameState){
        gameState.declared_steps=msg.steps;
        gameState.declared_el=msg.element;
        gameState.lead_pid=msg.pid;
      }
      addLog(`👑 ${nameOf(msg.pid)} declares: ${msg.steps} steps · ${SUIT_EL[msg.element]} ${SUIT_SYM[msg.element]}`,'imp');
      renderTable();
      updateDeclarePanel('pick_cards', {leader_pid: msg.pid});"""
if old_decl_ev in html:
    html = html.replace(old_decl_ev, new_decl_ev)
    results.append("✅ FIX PANEL DECL: declaration event updates panel")
else:
    results.append("⚠️  FIX PANEL DECL: declaration event pattern not found")

# Update renderTable to show accum pile per player
old_rt = """    seat.innerHTML=`
      <div class="seat-avatar" style="width:${avSize}px;height:${avSize}px;font-size:${Math.round(avSize*.38)}px;">
        ${isLeader?'<span class="seat-crown">👑</span>':''}${initials}
      </div>
      <div class="seat-name">${p.name}</div>
      ${dragonStr?`<div class="seat-dragons">${dragonStr}</div>`:''}
      <div class="seat-meta">${metaStr}</div>`;"""
new_rt = """    // Build accum pile HTML
    const accumCards = p.accum || [];
    let pileHTML = '';
    if(accumCards.length > 0){
      const shown = accumCards.slice(-6); // show last 6 won cards
      const pips = shown.map(c => {
        let cls = 'pile-card-mini ';
        if(c.is_joker) cls += c.joker_type==='space'?'js':'jt';
        else cls += (c.suit||'')[0].toLowerCase();
        const r = c.is_joker ? (c.joker_type==='space'?'🌌':'⏳')
                             : c.label.replace(/[♥♣♦♠]/g,'');
        return `<div class="${cls}">${r}</div>`;
      }).join('');
      pileHTML = `<div class="seat-pile">${pips}${accumCards.length>6?`<div style="font-size:8px;color:var(--gold-light);">+${accumCards.length-6}</div>`:''}</div>`;
    }
    seat.innerHTML=`
      <div class="seat-avatar" style="width:${avSize}px;height:${avSize}px;font-size:${Math.round(avSize*.38)}px;">
        ${isLeader?'<span class="seat-crown">👑</span>':''}${initials}
      </div>
      <div class="seat-name">${p.name}</div>
      ${dragonStr?`<div class="seat-dragons">${dragonStr}</div>`:''}
      ${pileHTML}
      <div class="seat-meta">${metaStr}</div>`;"""
if old_rt in html:
    html = html.replace(old_rt, new_rt)
    results.append("✅ FIX PILE RENDER: accum pile shown per player on table")
else:
    results.append("⚠️  FIX PILE RENDER: seat innerHTML pattern not found")

with open('index.html', 'w') as f:
    f.write(html)

# ══════════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════════
print("\n" + "="*55)
print("Dragon Tamer — Fix Results")
print("="*55)
for r in results:
    print(r)

passed = sum(1 for r in results if r.startswith("✅"))
failed = sum(1 for r in results if r.startswith("⚠️"))
print(f"\n{passed} fixes applied, {failed} need manual check")
print("\nRestart server: kill 1 && python server.py")
