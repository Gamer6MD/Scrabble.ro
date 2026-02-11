import os
import json
import uuid
import random
import logging
import re
from flask import Flask, request, jsonify
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

# Initializare Flask
app = Flask(__name__)

# Configuratii Firebase din variabilele de mediu Vercel
firebase_config_raw = os.environ.get("__firebase_config", "").strip()
app_id = os.environ.get("__app_id", "scrabble-ro").strip()
service_account_raw = os.environ.get("FIREBASE_SERVICE_ACCOUNT", "").strip()

firebase_config = {}
if firebase_config_raw:
    try:
        firebase_config = json.loads(firebase_config_raw)
    except Exception as e:
        logging.error(f"Eroare la parsarea __firebase_config: {e}")

def clean_service_account_json(raw_json):
    """Curăță și procesează JSON-ul Service Account pentru a evita erori de parsare."""
    cleaned = raw_json.strip()
    
    # Elimină ghilimelele exterioare dacă există
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned = cleaned[1:-1]
    
    # PROBLEMA: Vercel trimite newline-uri reale în JSON, ceea ce invalidează JSON-ul
    # SOLUTIE: Transformăm newline-urile reale în secvențe escape \n
    cleaned = re.sub(r'(?<!\\)\n', r'\\n', cleaned)
    cleaned = cleaned.replace('\\\\n', '\\n')
    
    # Elimină alte caractere de control problematice
    cleaned = re.sub(r'[\x00-\x09\x0B-\x0C\x0E-\x1F\x7F]', '', cleaned)
    
    return cleaned

# Initializam Firebase Admin
db = None
try:
    if not firebase_admin._apps:
        if service_account_raw:
            try:
                clean_json = clean_service_account_json(service_account_raw)
                cert_dict = json.loads(clean_json)
                
                if all(k in cert_dict for k in ['project_id', 'private_key', 'client_email']):
                    cred = credentials.Certificate(cert_dict)
                    firebase_admin.initialize_app(cred)
                    logging.info(f"Firebase Admin: Initializat cu succes pentru proiectul: {cert_dict.get('project_id')}")
                else:
                    logging.error("Service Account JSON incomplet.")
            except Exception as e:
                logging.error(f"Eroare la procesarea Service Account JSON: {e}")

        if not firebase_admin._apps and firebase_config.get("projectId"):
            firebase_admin.initialize_app(options={'projectId': firebase_config.get("projectId")})
            logging.info("Firebase Admin: Initializat via Project ID.")
            
        if not firebase_admin._apps:
            firebase_admin.initialize_app()
            logging.info("Firebase Admin: Initializat implicit.")
    
    db = firestore.client()
except Exception as e:
    logging.error(f"Eroare generala initializare Firebase: {e}")

# --- DICȚIONARE ---
AVAILABLE_DICTIONARIES = {
    "loc-flexiuni-5.0.txt": {
        "name": "LOC Flexiuni 5.0",
        "description": "Dicționar standard românesc (~679,000 cuvinte)",
        "file": "loc-flexiuni-5.0.txt"
    },
    "loc-flexiuni-6.0.txt": {
        "name": "LOC Flexiuni 6.0",
        "description": "Dicționar extins românesc (~706,000 cuvinte)",
        "file": "loc-flexiuni-6.0.txt"
    }
}

# Cache pentru dicționare încărcate
dictionary_cache = {}

def load_dictionary(filename):
    """Încarcă dicționarul în memorie (o singură dată)."""
    if filename in dictionary_cache:
        return dictionary_cache[filename]
    
    try:
        dict_path = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(dict_path):
            with open(dict_path, 'r', encoding='utf-8') as f:
                words = set()
                for line in f:
                    word = line.strip().lower()
                    if word:
                        words.add(word)
                dictionary_cache[filename] = words
                logging.info(f"Dicționar '{filename}' încărcat: {len(words)} cuvinte")
                return words
        else:
            logging.warning(f"Fișierul dicționar '{filename}' nu a fost găsit.")
            return set()
    except Exception as e:
        logging.error(f"Eroare la încărcarea dicționarului '{filename}': {e}")
        return set()

def validate_word(word, dictionary_file):
    """Verifică dacă un cuvânt există în dicționar."""
    word = word.strip().lower()
    words_set = load_dictionary(dictionary_file)
    return word in words_set

# --- CONSTANTE JOC ---
DEFAULT_LETTER_DISTRIBUTION = {
    'A': (1, 11), 'B': (9, 2), 'C': (1, 5), 'D': (2, 4), 'E': (1, 9), 
    'F': (8, 2), 'G': (9, 2), 'H': (10, 1), 'I': (1, 10), 'J': (10, 1), 
    'L': (1, 4), 'M': (4, 3), 'N': (1, 6), 'O': (1, 5), 'P': (2, 4), 
    'R': (1, 7), 'S': (1, 6), 'T': (1, 7), 'U': (1, 6), 'V': (8, 2), 
    'X': (10, 1), 'Z': (10, 1), '?': (0, 2)
}

def get_session_doc(session_id):
    if not db: return None
    return db.collection('artifacts').document(app_id).collection('public').document('data').collection('sessions').document(session_id)

def create_initial_state(player_name, player_id, settings=None):
    """Creează starea inițială a jocului cu setările specificate."""
    
    # Setări implicite
    max_players = settings.get('max_players', 4) if settings else 4
    rack_size = settings.get('rack_size', 7) if settings else 7
    bag_size = settings.get('bag_size', 100) if settings else 100
    dictionary = settings.get('dictionary', 'loc-flexiuni-5.0.txt') if settings else 'loc-flexiuni-5.0.txt'
    
    # Creează punga cu literele distribuite corect
    bag = []
    total_letters = 0
    for char, (points, count) in DEFAULT_LETTER_DISTRIBUTION.items():
        if total_letters + count <= bag_size:
            bag.extend([char] * count)
            total_letters += count
        else:
            remaining = bag_size - total_letters
            if remaining > 0:
                bag.extend([char] * remaining)
            break
    
    random.shuffle(bag)
    player_rack = [bag.pop() for _ in range(min(rack_size, len(bag))) if bag]
    
    # Convertim board-ul într-un format compatibil cu Firestore
    board_map = {}
    for i in range(15):
        for j in range(15):
            board_map[f"{i}_{j}"] = None
    
    return {
        "board": board_map,
        "bag": bag,
        "players": {
            player_id: {
                "id": player_id,
                "name": player_name,
                "score": 0,
                "rack": player_rack,
                "online": True,
                "is_host": True
            }
        },
        "turn_order": [player_id],
        "current_turn_index": 0,
        "game_started": False,
        "game_settings": {
            "max_players": max_players,
            "rack_size": rack_size,
            "bag_size": bag_size,
            "dictionary": dictionary
        },
        "chat_history": [f"System: {player_name} a creat sesiunea."],
        "last_update": datetime.utcnow().isoformat()
    }

# --- API ENDPOINTS ---

@app.route('/api/dictionaries', methods=['GET'])
def get_dictionaries():
    """Returnează lista dicționarelor disponibile."""
    return jsonify({
        "dictionaries": AVAILABLE_DICTIONARIES,
        "loaded_count": {k: len(v) for k, v in dictionary_cache.items()}
    })

@app.route('/api/dictionary/check', methods=['POST'])
def check_word():
    """Verifică dacă un cuvânt există în dicționar."""
    try:
        data = request.json or {}
        word = data.get('word', '').strip()
        dictionary = data.get('dictionary', 'loc-flexiuni-5.0.txt')
        
        if not word:
            return jsonify({"valid": False, "error": "Cuvântul este gol"})
        
        is_valid = validate_word(word, dictionary)
        return jsonify({"word": word.lower(), "valid": is_valid, "dictionary": dictionary})
    except Exception as e:
        return jsonify({"valid": False, "error": str(e)}), 400

@app.route('/api/session/create', methods=['POST'])
def create_session():
    if not db:
        return jsonify({"error": "Baza de date nu este conectata. Verificati setarile Service Account."}), 500
        
    try:
        data = request.json or {}
        player_name = data.get('name', 'Player')
        player_id = data.get('player_id') or str(uuid.uuid4())
        session_id = str(uuid.uuid4())[:8]
        
        # Setările sesiunii (cu valori implicite)
        settings = {
            'max_players': data.get('max_players', 4),
            'rack_size': data.get('rack_size', 7),
            'bag_size': data.get('bag_size', 100),
            'dictionary': data.get('dictionary', 'loc-flexiuni-5.0.txt')
        }
        
        state = create_initial_state(player_name, player_id, settings)
        get_session_doc(session_id).set(state)
        
        return jsonify({
            "session_id": session_id, 
            "player_id": player_id,
            "settings": settings
        })
    except Exception as e:
        logging.error(f"Eroare Firestore la crearea sesiunii: {e}")
        return jsonify({"error": f"Eroare Firestore: {str(e)}"}), 500

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
        settings = state.get('game_settings', {})
        max_players = settings.get('max_players', 4)
        rack_size = settings.get('rack_size', 7)
        
        if player_id not in state['players']:
            if len(state['players']) >= max_players:
                return jsonify({"error": f"Sesiune plina (max {max_players} jucători)"}), 400
            
            bag = state['bag']
            rack = [bag.pop() for _ in range(min(rack_size, len(bag))) if bag]
            state['players'][player_id] = {
                "id": player_id,
                "name": player_name,
                "score": 0,
                "rack": rack,
                "online": True,
                "is_host": False
            }
            state['turn_order'].append(player_id)
            state['bag'] = bag
            state['chat_history'].append(f"System: {player_name} s-a alaturat.")
            
        state['last_update'] = datetime.utcnow().isoformat()
        doc_ref.set(state)
        
        return jsonify({
            "status": "ok", 
            "player_id": player_id,
            "settings": settings
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online", 
        "firebase_ready": len(firebase_admin._apps) > 0,
        "db_connected": db is not None,
        "service_account_present": len(service_account_raw) > 0,
        "app_id": app_id,
        "dictionaries_loaded": list(dictionary_cache.keys())
    })

if __name__ == '__main__':
    app.run(debug=True)
