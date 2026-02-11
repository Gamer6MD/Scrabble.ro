import os
import json
import uuid
import random
import logging
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Initializare Flask
app = Flask(__name__)

# Configuratii Firebase
firebase_config_raw = os.environ.get("__firebase_config", "{}")
firebase_config = json.loads(firebase_config_raw)
app_id = os.environ.get("__app_id", "scrabble-ro")
service_account_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT")

# Initializam Firebase Admin
try:
    if not firebase_admin._apps:
        if service_account_raw:
            # Metoda recomandata: Folosind Service Account JSON din variabila de mediu
            service_account_info = json.loads(service_account_raw)
            cred = credentials.Certificate(service_account_info)
            firebase_admin.initialize_app(cred)
            logging.info("Firebase initialized with Service Account Key")
        else:
            # Fallback la Project ID daca nu avem Service Account (poate da erori 500 la scriere)
            project_id = firebase_config.get("projectId")
            firebase_admin.initialize_app(options={'projectId': project_id})
            logging.info(f"Firebase initialized with Project ID fallback: {project_id}")
    
    db = firestore.client()
except Exception as e:
    logging.error(f"Eroare critica la initializarea Firebase: {e}")
    # Ultimul efort de salvare
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    db = firestore.client()

# --- CONSTANTE JOC ---
LETTER_DISTRIBUTION = {
    'A': (1, 11), 'B': (9, 2), 'C': (1, 5), 'D': (2, 4), 'E': (1, 9), 
    'F': (8, 2), 'G': (9, 2), 'H': (10, 1), 'I': (1, 10), 'J': (10, 1), 
    'L': (1, 4), 'M': (4, 3), 'N': (1, 6), 'O': (1, 5), 'P': (2, 4), 
    'R': (1, 7), 'S': (1, 6), 'T': (1, 7), 'U': (1, 6), 'V': (8, 2), 
    'X': (10, 1), 'Z': (10, 1), '?': (0, 2)
}

def get_session_doc(session_id):
    # Structura corecta a path-ului conform regulilor de securitate si structurii dorite
    return db.collection('artifacts').document(app_id).collection('public').document('data').collection('sessions').document(session_id)

def create_initial_state(player_name, player_id):
    bag = []
    for char, (points, count) in LETTER_DISTRIBUTION.items():
        bag.extend([char] * count)
    random.shuffle(bag)
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

@app.route('/api/session/create', methods=['POST', 'GET'])
def create_session():
    if request.method == 'GET':
        return jsonify({"message": "Foloseste POST pentru a crea o sesiune"}), 405
        
    try:
        data = request.json or {}
        player_name = data.get('name', 'Player')
        player_id = data.get('player_id') or str(uuid.uuid4())
        session_id = str(uuid.uuid4())[:8]
        
        state = create_initial_state(player_name, player_id)
        get_session_doc(session_id).set(state)
        return jsonify({"session_id": session_id, "player_id": player_id})
    except Exception as e:
        logging.error(f"Eroare la crearea sesiunii: {e}")
        return jsonify({"error": str(e), "details": "Verificati Service Account si Firestore Rules"}), 500

@app.route('/api/session/join', methods=['POST'])
def join_session():
    try:
        data = request.json
        session_id = data.get('session_id')
        player_name = data.get('name', 'Player')
        player_id = data.get('player_id') or str(uuid.uuid4())
        
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
    except Exception as e:
        logging.error(f"Eroare la alaturare: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online", 
        "firebase": "initialized" if firebase_admin._apps else "failed",
        "using_service_account": service_account_raw is not None,
        "app_id": app_id
    })

if __name__ == '__main__':
    app.run(debug=True)
