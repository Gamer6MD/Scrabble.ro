import os
import json
import uuid
import random
import logging
from flask import Flask, request, jsonify
from firebase_admin import credentials, firestore, initialize_app
from datetime import datetime

# Initializare Flask
app = Flask(__name__)

# Configuratii Firebase - Necesare pentru persistenta pe Vercel (Serverless)
firebase_config = json.loads(os.environ.get("__firebase_config", "{}"))
app_id = os.environ.get("__app_id", "scrabble-ro")

# Initializam Firebase Admin SDK
try:
    if not firebase_config:
        logging.warning("Firebase config is missing. Data persistence will fail.")
    
    # In mediul Vercel furnizat, initialize_app() foloseste credentialele implicite ale sistemului
    initialize_app()
    db = firestore.client()
except Exception as e:
    logging.info(f"Firebase initialization info: {e}")
    db = firestore.client()

# --- CONSTANTE JOC ---
LETTER_DISTRIBUTION = {
    'A': (1, 11), 'B': (9, 2), 'C': (1, 5), 'D': (2, 4), 'E': (1, 9), 
    'F': (8, 2), 'G': (9, 2), 'H': (10, 1), 'I': (1, 10), 'J': (10, 1), 
    'L': (1, 4), 'M': (4, 3), 'N': (1, 6), 'O': (1, 5), 'P': (2, 4), 
    'R': (1, 7), 'S': (1, 6), 'T': (1, 7), 'U': (1, 6), 'V': (8, 2), 
    'X': (10, 1), 'Z': (10, 1), '?': (0, 2)
}

# --- UTILS FIRESTORE (Respectand regulile de path-uri) ---
def get_session_doc(session_id):
    # Rule 1: Folosim path-ul strict pentru date publice
    return db.document(f"artifacts/{app_id}/public/data/sessions/{session_id}")

# --- LOGICA JOC ---

def create_initial_state(player_name, player_id):
    # Creeaza sacul de litere
    bag = []
    for char, (points, count) in LETTER_DISTRIBUTION.items():
        bag.extend([char] * count)
    random.shuffle(bag)
    
    # Extrage literele pentru primul jucator
    player_rack = [bag.pop() for _ in range(7) if bag]
    
    return {
        "board": [[None for _ in range(15)] for _ in range(15)],
        "bag": bag,
        "players": {
            player_id: {
                "id": player_id,
                "name": player_name,
                "score": 0,
                "rack": player_rack,
                "online": True
            }
        },
        "turn_order": [player_id],
        "current_turn_index": 0,
        "game_started": False,
        "chat_history": [f"System: {player_name} a creat sesiunea."],
        "last_update": datetime.utcnow().isoformat()
    }

# --- RUTE API ---

@app.route('/api/session/create', methods=['POST'])
def create_session():
    # Endpoint pentru crearea unei noi sesiuni de joc
    data = request.json
    player_name = data.get('name', 'Player')
    player_id = data.get('player_id', str(uuid.uuid4()))
    session_id = str(uuid.uuid4())[:8]
    
    state = create_initial_state(player_name, player_id)
    get_session_doc(session_id).set(state)
    
    return jsonify({"session_id": session_id, "player_id": player_id})

@app.route('/api/session/join', methods=['POST'])
def join_session():
    # Adauga un jucator nou intr-o sesiune existenta
    data = request.json
    session_id = data.get('session_id')
    player_name = data.get('name', 'Player')
    player_id = data.get('player_id', str(uuid.uuid4()))
    
    doc_ref = get_session_doc(session_id)
    doc = doc_ref.get()
    
    if not doc.exists:
        return jsonify({"error": "Sesiunea nu exista"}), 404
    
    state = doc.to_dict()
    if player_id not in state['players']:
        if len(state['players']) >= 4:
            return jsonify({"error": "Sesiune plina"}), 400
        
        bag = state['bag']
        rack = [bag.pop() for _ in range(7) if bag]
        state['players'][player_id] = {
            "id": player_id,
            "name": player_name,
            "score": 0,
            "rack": rack,
            "online": True
        }
        state['turn_order'].append(player_id)
        state['bag'] = bag
        state['chat_history'].append(f"System: {player_name} s-a alaturat.")
        
    state['last_update'] = datetime.utcnow().isoformat()
    doc_ref.set(state)
    
    return jsonify({"status": "ok", "player_id": player_id})

@app.route('/api/session/state', methods=['GET'])
def get_state():
    # Returneaza starea actuala a jocului din baza de date
    session_id = request.args.get('session_id')
    doc = get_session_doc(session_id).get()
    if not doc.exists:
        return jsonify({"error": "Sesiunea nu a fost gasita"}), 404
    return jsonify(doc.to_dict())

@app.route('/api/game/move', methods=['POST'])
def make_move():
    # Proceseaza mutarea unui jucator
    data = request.json
    session_id = data.get('session_id')
    player_id = data.get('player_id')
    placements = data.get('placements') # [[r, c, litera], ...]
    
    doc_ref = get_session_doc(session_id)
    state = doc_ref.get().to_dict()
    
    # Verificare rand
    current_player_id = state['turn_order'][state['current_turn_index']]
    if player_id != current_player_id:
        return jsonify({"error": "Nu este randul tau"}), 400
    
    # Aplicare mutare pe tabla si actualizare rack
    for r, c, char, is_joker in placements:
        state['board'][r][c] = char
        if char in state['players'][player_id]['rack']:
            state['players'][player_id]['rack'].remove(char)
            
    # Completare rack din sac
    while len(state['players'][player_id]['rack']) < 7 and state['bag']:
        state['players'][player_id]['rack'].append(state['bag'].pop())
        
    # Schimbare rand
    state['current_turn_index'] = (state['current_turn_index'] + 1) % len(state['turn_order'])
    state['last_update'] = datetime.utcnow().isoformat()
    
    doc_ref.set(state)
    return jsonify({"status": "success"})

if __name__ == '__main__':
    app.run(debug=True)
