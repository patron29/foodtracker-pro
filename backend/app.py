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
        
        return jsonify({'token': token, 'username': username, 'user_id': user_id}), 201
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
    
    return jsonify({'id': entry_id, 'message': 'Food logged successfully'}), 201

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
    """Fallback demo food database with restaurant items"""
    demo_foods = [
        # Basic Foods
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
        
        # Chick-fil-A
        {'name': 'Chick-fil-A Chicken Sandwich', 'brand': 'Chick-fil-A', 'calories': 440, 'protein': 28, 'carbs': 41, 'fats': 17, 'serving': '1 sandwich', 'verified': True},
        {'name': 'Chick-fil-A Spicy Chicken Sandwich', 'brand': 'Chick-fil-A', 'calories': 450, 'protein': 28, 'carbs': 43, 'fats': 19, 'serving': '1 sandwich', 'verified': True},
        {'name': 'Chick-fil-A Grilled Chicken Sandwich', 'brand': 'Chick-fil-A', 'calories': 320, 'protein': 28, 'carbs': 42, 'fats': 5, 'serving': '1 sandwich', 'verified': True},
        {'name': 'Chick-fil-A Nuggets 8-count', 'brand': 'Chick-fil-A', 'calories': 250, 'protein': 27, 'carbs': 11, 'fats': 11, 'serving': '8 pieces', 'verified': True},
        {'name': 'Chick-fil-A Nuggets 12-count', 'brand': 'Chick-fil-A', 'calories': 380, 'protein': 40, 'carbs': 16, 'fats': 17, 'serving': '12 pieces', 'verified': True},
        {'name': 'Chick-fil-A Waffle Fries Medium', 'brand': 'Chick-fil-A', 'calories': 360, 'protein': 5, 'carbs': 45, 'fats': 18, 'serving': '1 medium', 'verified': True},
        {'name': 'Chick-fil-A Mac & Cheese', 'brand': 'Chick-fil-A', 'calories': 450, 'protein': 19, 'carbs': 45, 'fats': 21, 'serving': '1 medium', 'verified': True},
        {'name': 'Chick-fil-A Fruit Cup', 'brand': 'Chick-fil-A', 'calories': 50, 'protein': 1, 'carbs': 13, 'fats': 0, 'serving': '1 medium', 'verified': True},
        {'name': 'Chick-fil-A Chicken Cool Wrap', 'brand': 'Chick-fil-A', 'calories': 350, 'protein': 37, 'carbs': 30, 'fats': 14, 'serving': '1 wrap', 'verified': True},
        
        # McDonald\'s
        {'name': 'McDonald\'s Big Mac', 'brand': 'McDonald\'s', 'calories': 550, 'protein': 25, 'carbs': 45, 'fats': 30, 'serving': '1 burger', 'verified': True},
        {'name': 'McDonald\'s Quarter Pounder with Cheese', 'brand': 'McDonald\'s', 'calories': 520, 'protein': 30, 'carbs': 41, 'fats': 26, 'serving': '1 burger', 'verified': True},
        {'name': 'McDonald\'s McChicken', 'brand': 'McDonald\'s', 'calories': 400, 'protein': 14, 'carbs': 39, 'fats': 21, 'serving': '1 sandwich', 'verified': True},
        {'name': 'McDonald\'s Chicken McNuggets 10-piece', 'brand': 'McDonald\'s', 'calories': 420, 'protein': 24, 'carbs': 25, 'fats': 24, 'serving': '10 pieces', 'verified': True},
        {'name': 'McDonald\'s Medium Fries', 'brand': 'McDonald\'s', 'calories': 320, 'protein': 4, 'carbs': 43, 'fats': 15, 'serving': '1 medium', 'verified': True},
        {'name': 'McDonald\'s Egg McMuffin', 'brand': 'McDonald\'s', 'calories': 310, 'protein': 17, 'carbs': 30, 'fats': 13, 'serving': '1 sandwich', 'verified': True},
        {'name': 'McDonald\'s Sausage McMuffin with Egg', 'brand': 'McDonald\'s', 'calories': 480, 'protein': 21, 'carbs': 30, 'fats': 30, 'serving': '1 sandwich', 'verified': True},
        {'name': 'McDonald\'s Hash Browns', 'brand': 'McDonald\'s', 'calories': 140, 'protein': 2, 'carbs': 15, 'fats': 9, 'serving': '1 hash brown', 'verified': True},
        
        # Starbucks
        {'name': 'Starbucks Grande Latte', 'brand': 'Starbucks', 'calories': 190, 'protein': 13, 'carbs': 18, 'fats': 7, 'serving': '16 fl oz', 'verified': True},
        {'name': 'Starbucks Grande Cappuccino', 'brand': 'Starbucks', 'calories': 120, 'protein': 8, 'carbs': 12, 'fats': 4, 'serving': '16 fl oz', 'verified': True},
        {'name': 'Starbucks Bacon Egg & Cheese', 'brand': 'Starbucks', 'calories': 450, 'protein': 21, 'carbs': 44, 'fats': 21, 'serving': '1 sandwich', 'verified': True},
        {'name': 'Starbucks Egg White Bites', 'brand': 'Starbucks', 'calories': 170, 'protein': 13, 'carbs': 13, 'fats': 7, 'serving': '2 bites', 'verified': True},
        {'name': 'Starbucks Blueberry Muffin', 'brand': 'Starbucks', 'calories': 350, 'protein': 5, 'carbs': 54, 'fats': 13, 'serving': '1 muffin', 'verified': True},
        
        # Chipotle
        {'name': 'Chipotle Chicken Burrito Bowl', 'brand': 'Chipotle', 'calories': 630, 'protein': 42, 'carbs': 62, 'fats': 24, 'serving': '1 bowl', 'verified': True},
        {'name': 'Chipotle Steak Burrito Bowl', 'brand': 'Chipotle', 'calories': 650, 'protein': 41, 'carbs': 62, 'fats': 27, 'serving': '1 bowl', 'verified': True},
        {'name': 'Chipotle Chicken Burrito', 'brand': 'Chipotle', 'calories': 1025, 'protein': 58, 'carbs': 123, 'fats': 35, 'serving': '1 burrito', 'verified': True},
        {'name': 'Chipotle Chips & Guacamole', 'brand': 'Chipotle', 'calories': 770, 'protein': 11, 'carbs': 84, 'fats': 45, 'serving': '1 serving', 'verified': True},
        {'name': 'Chipotle Sofritas Bowl', 'brand': 'Chipotle', 'calories': 555, 'protein': 20, 'carbs': 68, 'fats': 25, 'serving': '1 bowl', 'verified': True},
        
        # Subway
        {'name': 'Subway Turkey Breast 6-inch', 'brand': 'Subway', 'calories': 280, 'protein': 18, 'carbs': 46, 'fats': 3.5, 'serving': '6-inch', 'verified': True},
        {'name': 'Subway Italian BMT 6-inch', 'brand': 'Subway', 'calories': 410, 'protein': 19, 'carbs': 46, 'fats': 16, 'serving': '6-inch', 'verified': True},
        {'name': 'Subway Chicken Teriyaki 6-inch', 'brand': 'Subway', 'calories': 370, 'protein': 25, 'carbs': 57, 'fats': 5, 'serving': '6-inch', 'verified': True},
        {'name': 'Subway Meatball Marinara 6-inch', 'brand': 'Subway', 'calories': 480, 'protein': 23, 'carbs': 52, 'fats': 18, 'serving': '6-inch', 'verified': True},
        
        # Panera Bread
        {'name': 'Panera Broccoli Cheddar Soup Bowl', 'brand': 'Panera', 'calories': 360, 'protein': 13, 'carbs': 26, 'fats': 23, 'serving': '1 bowl', 'verified': True},
        {'name': 'Panera Caesar Salad with Chicken', 'brand': 'Panera', 'calories': 520, 'protein': 37, 'carbs': 24, 'fats': 31, 'serving': '1 salad', 'verified': True},
        {'name': 'Panera Turkey Sandwich', 'brand': 'Panera', 'calories': 500, 'protein': 29, 'carbs': 52, 'fats': 19, 'serving': '1 sandwich', 'verified': True},
        
        # Taco Bell
        {'name': 'Taco Bell Crunchy Taco', 'brand': 'Taco Bell', 'calories': 170, 'protein': 8, 'carbs': 13, 'fats': 10, 'serving': '1 taco', 'verified': True},
        {'name': 'Taco Bell Chicken Quesadilla', 'brand': 'Taco Bell', 'calories': 510, 'protein': 27, 'carbs': 37, 'fats': 28, 'serving': '1 quesadilla', 'verified': True},
        {'name': 'Taco Bell Burrito Supreme', 'brand': 'Taco Bell', 'calories': 380, 'protein': 13, 'carbs': 51, 'fats': 13, 'serving': '1 burrito', 'verified': True},
        {'name': 'Taco Bell Chalupa Supreme', 'brand': 'Taco Bell', 'calories': 350, 'protein': 13, 'carbs': 30, 'fats': 21, 'serving': '1 chalupa', 'verified': True},
        
        # Pizza
        {'name': 'Domino\'s Hand Tossed Pepperoni Pizza', 'brand': 'Domino\'s', 'calories': 280, 'protein': 12, 'carbs': 30, 'fats': 12, 'serving': '1 slice', 'verified': True},
        {'name': 'Pizza Hut Pepperoni Pan Pizza', 'brand': 'Pizza Hut', 'calories': 290, 'protein': 11, 'carbs': 29, 'fats': 14, 'serving': '1 slice', 'verified': True},
        {'name': 'Papa John\'s Pepperoni Pizza', 'brand': 'Papa John\'s', 'calories': 300, 'protein': 13, 'carbs': 31, 'fats': 13, 'serving': '1 slice', 'verified': True},
        
        # Wendy\'s
        {'name': 'Wendy\'s Dave\'s Single', 'brand': 'Wendy\'s', 'calories': 570, 'protein': 30, 'carbs': 39, 'fats': 34, 'serving': '1 burger', 'verified': True},
        {'name': 'Wendy\'s Spicy Chicken Sandwich', 'brand': 'Wendy\'s', 'calories': 510, 'protein': 28, 'carbs': 50, 'fats': 22, 'serving': '1 sandwich', 'verified': True},
        {'name': 'Wendy\'s Chicken Nuggets 10-piece', 'brand': 'Wendy\'s', 'calories': 450, 'protein': 22, 'carbs': 29, 'fats': 28, 'serving': '10 pieces', 'verified': True},
        {'name': 'Wendy\'s Baconator', 'brand': 'Wendy\'s', 'calories': 960, 'protein': 57, 'carbs': 38, 'fats': 66, 'serving': '1 burger', 'verified': True},
        
        # Shake Shack
        {'name': 'Shake Shack ShackBurger', 'brand': 'Shake Shack', 'calories': 530, 'protein': 26, 'carbs': 41, 'fats': 30, 'serving': '1 burger', 'verified': True},
        {'name': 'Shake Shack Chicken Shack', 'brand': 'Shake Shack', 'calories': 550, 'protein': 32, 'carbs': 44, 'fats': 27, 'serving': '1 sandwich', 'verified': True},
        {'name': 'Shake Shack Fries', 'brand': 'Shake Shack', 'calories': 470, 'protein': 6, 'carbs': 60, 'fats': 23, 'serving': '1 order', 'verified': True},
        
        # In-N-Out
        {'name': 'In-N-Out Double-Double', 'brand': 'In-N-Out', 'calories': 670, 'protein': 37, 'carbs': 39, 'fats': 41, 'serving': '1 burger', 'verified': True},
        {'name': 'In-N-Out Cheeseburger', 'brand': 'In-N-Out', 'calories': 480, 'protein': 22, 'carbs': 39, 'fats': 27, 'serving': '1 burger', 'verified': True},
        {'name': 'In-N-Out Fries', 'brand': 'In-N-Out', 'calories': 395, 'protein': 7, 'carbs': 54, 'fats': 18, 'serving': '1 order', 'verified': True},
        
        # Five Guys
        {'name': 'Five Guys Hamburger', 'brand': 'Five Guys', 'calories': 700, 'protein': 39, 'carbs': 39, 'fats': 43, 'serving': '1 burger', 'verified': True},
        {'name': 'Five Guys Little Hamburger', 'brand': 'Five Guys', 'calories': 480, 'protein': 26, 'carbs': 39, 'fats': 26, 'serving': '1 burger', 'verified': True},
        {'name': 'Five Guys Fries Regular', 'brand': 'Five Guys', 'calories': 950, 'protein': 13, 'carbs': 131, 'fats': 41, 'serving': '1 order', 'verified': True},
        
        # Panda Express
        {'name': 'Panda Express Orange Chicken', 'brand': 'Panda Express', 'calories': 490, 'protein': 25, 'carbs': 51, 'fats': 21, 'serving': '1 serving', 'verified': True},
        {'name': 'Panda Express Beijing Beef', 'brand': 'Panda Express', 'calories': 470, 'protein': 13, 'carbs': 52, 'fats': 24, 'serving': '1 serving', 'verified': True},
        {'name': 'Panda Express Fried Rice', 'brand': 'Panda Express', 'calories': 520, 'protein': 11, 'carbs': 85, 'fats': 16, 'serving': '1 serving', 'verified': True},
        {'name': 'Panda Express Broccoli Beef', 'brand': 'Panda Express', 'calories': 150, 'protein': 9, 'carbs': 13, 'fats': 7, 'serving': '1 serving', 'verified': True},
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