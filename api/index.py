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

# Configuratii Firebase - Curatam spatiile albe si verificam validitatea
firebase_config_raw = os.environ.get("__firebase_config", "").strip()
app_id = os.environ.get("__app_id", "scrabble-ro").strip()
service_account_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()

firebase_config = {}
if firebase_config_raw:
    try:
        firebase_config = json.loads(firebase_config_raw)
    except Exception as e:
        logging.error(f"Eroare la parsarea __firebase_config: {e}")

# Initializam Firebase Admin
db = None
try:
    if not firebase_admin._apps:
        # Prioritate 1: Service Account explicit (cel mai sigur pentru scriere)
        if service_account_raw and (service_account_raw.startswith('{') or service_account_raw.startswith('[')):
            try:
                # Curatare suplimentara pentru caractere ascunse
                clean_json = service_account_raw.replace('\n', '').replace('\r', '')
                service_account_info = json.loads(clean_json)
                cred = credentials.Certificate(service_account_info)
                firebase_admin.initialize_app(cred)
                logging.info("Firebase Admin: Initializat cu succes via Service Account.")
            except Exception as e:
                logging.error(f"Eroare la incarcarea Service Account JSON: {e}")

        # Prioritate 2: Daca nu avem Service Account, folosim Project ID (doar pt Read de obicei)
        if not firebase_admin._apps:
            project_id = firebase_config.get("projectId")
            if project_id:
                firebase_admin.initialize_app(options={'projectId': project_id})
                logging.info(f"Firebase Admin: Initializat via Project ID: {project_id}")
            else:
                # Fallback final: Application Default Credentials (da eroare pe Vercel de obicei)
                firebase_admin.initialize_app()
                logging.info("Firebase Admin: Initializat via implicit (ADC).")
    
    # Obtinem clientul Firestore
    db = firestore.client()
except Exception as e:
    logging.error(f"Eroare generala initializare Firebase: {e}")

# --- CONSTANTE JOC ---
LETTER_DISTRIBUTION = {
    'A': (1, 11), 'B': (9, 2), 'C': (1, 5), 'D': (2, 4), 'E': (1, 9), 
    'F': (8, 2), 'G': (9, 2), 'H': (10, 1), 'I': (1, 10), 'J': (10, 1), 
    'L': (1, 4), 'M': (4, 3), 'N': (1, 6), 'O': (1, 5), 'P': (2, 4), 
    'R': (1, 7), 'S': (1, 6), 'T': (1, 7), 'U': (1, 6), 'V': (8, 2), 
    'X': (10, 1), 'Z': (10, 1), '?': (0, 2)
}

def get_session_doc(session_id):
    if not db: return None
    # Structura: /artifacts/scrabble-ro/public/data/sessions/{session_id}
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

@app.route('/api/session/create', methods=['POST'])
def create_session():
    if not db:
        return jsonify({"error": "Baza de date neinitializata. Verificati configuratia Firebase."}), 500
        
    try:
        data = request.json or {}
        player_name = data.get('name', 'Player')
        player_id = data.get('player_id') or str(uuid.uuid4())
        session_id = str(uuid.uuid4())[:8]
        
        state = create_initial_state(player_name, player_id)
        get_session_doc(session_id).set(state)
        return jsonify({"session_id": session_id, "player_id": player_id})
    except Exception as e:
        logging.error(f"Eroare la scrierea in Firestore: {e}")
        return jsonify({"error": f"Eroare la scrierea in baza de date: {str(e)}"}), 500

@app.route('/api/session/join', methods=['POST'])
def join_session():
    if not db:
        return jsonify({"error": "Baza de date neinitializata"}), 500
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
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online", 
        "firebase_initialized": firebase_admin._apps is not None and len(firebase_admin._apps) > 0,
        "db_connected": db is not None,
        "config_present": len(firebase_config_raw) > 0,
        "service_account_present": len(service_account_raw) > 0,
        "app_id": app_id
    })

if __name__ == '__main__':
    app.run(debug=True)
