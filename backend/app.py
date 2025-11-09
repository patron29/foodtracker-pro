from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
import os
import requests
import json

app = Flask(__name__, static_folder='../frontend', static_url_path='')
CORS(app)

# USDA FoodData Central API (No API key required for basic access)
USDA_API_URL = 'https://api.nal.usda.gov/fdc/v1/foods/search'
USDA_API_KEY = os.environ.get('USDA_API_KEY', 'DEMO_KEY')  # Works without key

DB_NAME = 'foodtracker.db'

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT UNIQUE NOT NULL,
                  password TEXT NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS food_entries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER NOT NULL,
                  meal_name TEXT NOT NULL,
                  calories INTEGER NOT NULL,
                  protein INTEGER DEFAULT 0,
                  carbs INTEGER DEFAULT 0,
                  fats INTEGER DEFAULT 0,
                  notes TEXT,
                  logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS friends
                 (user_id INTEGER NOT NULL,
                  friend_id INTEGER NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (user_id, friend_id),
                  FOREIGN KEY (user_id) REFERENCES users (id),
                  FOREIGN KEY (friend_id) REFERENCES users (id))''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS tokens
                 (token TEXT PRIMARY KEY,
                  user_id INTEGER NOT NULL,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users (id))''')
    
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_token(token):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT user_id FROM tokens WHERE token = ? AND datetime(created_at, "+30 days") > datetime("now")', (token,))
    result = c.fetchone()
    conn.close()
    return result['user_id'] if result else None

@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('INSERT INTO users (username, password) VALUES (?, ?)',
                  (username, hash_password(password)))
        conn.commit()
        user_id = c.lastrowid
        
        token = secrets.token_hex(32)
        c.execute('INSERT INTO tokens (token, user_id) VALUES (?, ?)', (token, user_id))
        conn.commit()
        
        return jsonify({'token': token, 'username': username, 'user_id': user_id})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Username already exists'}), 400
    finally:
        conn.close()

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, username FROM users WHERE username = ? AND password = ?',
              (username, hash_password(password)))
    user = c.fetchone()
    
    if not user:
        conn.close()
        return jsonify({'error': 'Invalid credentials'}), 401
    
    token = secrets.token_hex(32)
    c.execute('INSERT INTO tokens (token, user_id) VALUES (?, ?)', (token, user['id']))
    conn.commit()
    conn.close()
    
    return jsonify({'token': token, 'username': user['username'], 'user_id': user['id']})

@app.route('/api/food', methods=['POST'])
def add_food():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    data = request.json
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''INSERT INTO food_entries 
                 (user_id, meal_name, calories, protein, carbs, fats, notes)
                 VALUES (?, ?, ?, ?, ?, ?, ?)''',
              (user_id, data['meal_name'], data['calories'], 
               data.get('protein', 0), data.get('carbs', 0), 
               data.get('fats', 0), data.get('notes', '')))
    
    conn.commit()
    entry_id = c.lastrowid
    conn.close()
    
    return jsonify({'id': entry_id, 'message': 'Food logged successfully'})

@app.route('/api/food', methods=['GET'])
def get_food():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    date = request.args.get('date')
    conn = get_db()
    c = conn.cursor()
    
    if date:
        c.execute('''SELECT * FROM food_entries 
                     WHERE user_id = ? AND date(logged_at) = date(?)
                     ORDER BY logged_at DESC''', (user_id, date))
    else:
        c.execute('''SELECT * FROM food_entries 
                     WHERE user_id = ?
                     ORDER BY logged_at DESC LIMIT 50''', (user_id,))
    
    entries = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(entries)

@app.route('/api/food/stats', methods=['GET'])
def get_stats():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    date = request.args.get('date', datetime.now().strftime('%Y-%m-%d'))
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''SELECT 
                 SUM(calories) as total_calories,
                 SUM(protein) as total_protein,
                 SUM(carbs) as total_carbs,
                 SUM(fats) as total_fats
                 FROM food_entries
                 WHERE user_id = ? AND date(logged_at) = date(?)''',
              (user_id, date))
    
    stats = dict(c.fetchone())
    conn.close()
    
    return jsonify(stats)

@app.route('/api/users/search', methods=['GET'])
def search_users():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    query = request.args.get('q', '')
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''SELECT id, username FROM users 
                 WHERE username LIKE ? AND id != ?
                 LIMIT 10''', (f'%{query}%', user_id))
    
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(users)

@app.route('/api/friends', methods=['POST'])
def add_friend():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    friend_id = request.json.get('friend_id')
    conn = get_db()
    c = conn.cursor()
    
    try:
        c.execute('INSERT INTO friends (user_id, friend_id) VALUES (?, ?)',
                  (user_id, friend_id))
        c.execute('INSERT INTO friends (user_id, friend_id) VALUES (?, ?)',
                  (friend_id, user_id))
        conn.commit()
        return jsonify({'message': 'Friend added'})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Already friends'}), 400
    finally:
        conn.close()

@app.route('/api/friends', methods=['GET'])
def get_friends():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''SELECT u.id, u.username 
                 FROM users u
                 JOIN friends f ON u.id = f.friend_id
                 WHERE f.user_id = ?''', (user_id,))
    
    friends = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(friends)

@app.route('/api/friends/<int:friend_id>/food', methods=['GET'])
def get_friend_food(friend_id):
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?',
              (user_id, friend_id))
    if not c.fetchone():
        conn.close()
        return jsonify({'error': 'Not friends'}), 403
    
    date = request.args.get('date')
    if date:
        c.execute('''SELECT * FROM food_entries 
                     WHERE user_id = ? AND date(logged_at) = date(?)
                     ORDER BY logged_at DESC''', (friend_id, date))
    else:
        c.execute('''SELECT * FROM food_entries 
                     WHERE user_id = ?
                     ORDER BY logged_at DESC LIMIT 50''', (friend_id,))
    
    entries = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(entries)

@app.route('/api/feed', methods=['GET'])
def get_feed():
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''SELECT f.*, u.username 
                 FROM food_entries f
                 JOIN users u ON f.user_id = u.id
                 WHERE f.user_id IN (
                     SELECT friend_id FROM friends WHERE user_id = ?
                 )
                 ORDER BY f.logged_at DESC
                 LIMIT 50''', (user_id,))
    
    feed = [dict(row) for row in c.fetchall()]
    conn.close()
    
    return jsonify(feed)

@app.route('/api/food/search', methods=['GET'])
def search_food():
    """Search USDA FoodData Central database"""
    token = request.headers.get('Authorization')
    if not token:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_id = verify_token(token)
    if not user_id:
        return jsonify({'error': 'Invalid token'}), 401
    
    query = request.args.get('query', request.args.get('q', ''))  # Support both 'query' and 'q'
    
    if len(query) < 2:
        return jsonify({'results': []})
    
    try:
        # Query USDA API
        params = {
            'api_key': USDA_API_KEY,
            'query': query,
            'pageSize': 20,
            'dataType': ['Foundation', 'SR Legacy']
        }
        
        response = requests.get(USDA_API_URL, params=params, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            
            # Format results for frontend
            results = []
            for food in data.get('foods', []):
                nutrients = {}
                for nutrient in food.get('foodNutrients', []):
                    name = nutrient.get('nutrientName', '').lower()
                    value = nutrient.get('value', 0)
                    
                    if 'energy' in name or 'calor' in name:
                        nutrients['calories'] = round(value)
                    elif 'protein' in name:
                        nutrients['protein'] = round(value, 1)
                    elif 'carbohydrate' in name:
                        nutrients['carbs'] = round(value, 1)
                    elif 'total lipid' in name or 'fat' in name:
                        nutrients['fats'] = round(value, 1)
                
                # Only include foods with complete nutrition data
                if all(k in nutrients for k in ['calories', 'protein', 'carbs', 'fats']):
                    results.append({
                        'id': food.get('fdcId'),
                        'name': food.get('description', ''),
                        'brand': food.get('brandOwner', 'USDA'),
                        'serving': '100g',
                        'calories': nutrients['calories'],
                        'protein': nutrients['protein'],
                        'carbs': nutrients['carbs'],
                        'fats': nutrients['fats'],
                        'verified': True
                    })
            
            return jsonify({'results': results[:10]})
        else:
            # Return fallback demo data if API fails
            return get_demo_food_results(query)
            
    except Exception as e:
        # Return fallback demo data on error
        return get_demo_food_results(query)

def get_demo_food_results(query):
    """Fallback demo food database"""
    demo_foods = [
        {'name': 'Chicken Breast, Grilled', 'brand': 'Generic', 'calories': 165, 'protein': 31, 'carbs': 0, 'fats': 3.6, 'serving': '100g', 'verified': True},
        {'name': 'Chicken Breast, Raw', 'brand': 'USDA', 'calories': 120, 'protein': 22.5, 'carbs': 0, 'fats': 2.6, 'serving': '100g', 'verified': True},
        {'name': 'Banana, Medium', 'brand': 'Fresh', 'calories': 105, 'protein': 1.3, 'carbs': 27, 'fats': 0.4, 'serving': '1 medium', 'verified': True},
        {'name': 'Greek Yogurt, Plain', 'brand': 'Generic', 'calories': 100, 'protein': 17, 'carbs': 6, 'fats': 0.7, 'serving': '170g', 'verified': True},
        {'name': 'Oatmeal, Cooked', 'brand': 'Generic', 'calories': 71, 'protein': 2.5, 'carbs': 12, 'fats': 1.5, 'serving': '100g', 'verified': True},
        {'name': 'Salmon, Grilled', 'brand': 'Atlantic', 'calories': 206, 'protein': 22, 'carbs': 0, 'fats': 13, 'serving': '100g', 'verified': True},
        {'name': 'Brown Rice, Cooked', 'brand': 'Generic', 'calories': 111, 'protein': 2.6, 'carbs': 23, 'fats': 0.9, 'serving': '100g', 'verified': True},
        {'name': 'Eggs, Large', 'brand': 'Generic', 'calories': 72, 'protein': 6, 'carbs': 0.4, 'fats': 5, 'serving': '1 large egg', 'verified': True},
        {'name': 'Almonds, Raw', 'brand': 'Generic', 'calories': 579, 'protein': 21, 'carbs': 22, 'fats': 50, 'serving': '100g', 'verified': True},
        {'name': 'Avocado', 'brand': 'Fresh', 'calories': 160, 'protein': 2, 'carbs': 8.5, 'fats': 15, 'serving': '100g', 'verified': True},
        {'name': 'Sweet Potato, Baked', 'brand': 'Fresh', 'calories': 90, 'protein': 2, 'carbs': 21, 'fats': 0.2, 'serving': '100g', 'verified': True},
        {'name': 'Broccoli, Steamed', 'brand': 'Fresh', 'calories': 35, 'protein': 2.4, 'carbs': 7, 'fats': 0.4, 'serving': '100g', 'verified': True},
    ]
    
    query_lower = query.lower()
    results = [f for f in demo_foods if query_lower in f['name'].lower()]
    
    return jsonify({'results': results})

# Serve frontend
@app.route('/')
def serve_frontend():
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    try:
        return send_from_directory(app.static_folder, path)
    except:
        return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)
