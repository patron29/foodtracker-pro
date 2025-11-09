"""
Basic test suite for FoodTracker backend
Run with: pytest tests/
"""

import sys
sys.path.insert(0, '../')

import pytest
from app import app, init_db

@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    with app.test_client() as client:
        with app.app_context():
            init_db()
        yield client

def test_health_check(client):
    """Test basic health check"""
    response = client.get('/')
    assert response.status_code == 200

def test_register_user(client):
    """Test user registration"""
    response = client.post('/api/register', json={
        'username': 'testuser',
        'password': 'testpass123'
    })
    assert response.status_code == 201

def test_login_user(client):
    """Test user login"""
    # First register
    client.post('/api/register', json={
        'username': 'testuser2',
        'password': 'testpass123'
    })
    
    # Then login
    response = client.post('/api/login', json={
        'username': 'testuser2',
        'password': 'testpass123'
    })
    assert response.status_code == 200
    data = response.get_json()
    assert 'token' in data

def test_food_search_requires_auth(client):
    """Test that food search requires authentication"""
    response = client.get('/api/food/search?query=chicken')
    assert response.status_code == 401

def test_food_search_with_auth(client):
    """Test food search with valid token"""
    # Register and login
    client.post('/api/register', json={
        'username': 'testuser3',
        'password': 'testpass123'
    })
    
    login_response = client.post('/api/login', json={
        'username': 'testuser3',
        'password': 'testpass123'
    })
    token = login_response.get_json()['token']
    
    # Search for food
    response = client.get('/api/food/search?query=chicken', 
                         headers={'Authorization': token})
    assert response.status_code == 200
    data = response.get_json()
    assert 'results' in data

def test_log_food(client):
    """Test logging food entry"""
    # Register and login
    client.post('/api/register', json={
        'username': 'testuser4',
        'password': 'testpass123'
    })
    
    login_response = client.post('/api/login', json={
        'username': 'testuser4',
        'password': 'testpass123'
    })
    token = login_response.get_json()['token']
    
    # Log food
    response = client.post('/api/food', 
                          headers={'Authorization': token},
                          json={
                              'meal_name': 'Test Meal',
                              'calories': 300,
                              'protein': 25,
                              'carbs': 30,
                              'fats': 10
                          })
    assert response.status_code == 201

def test_get_diary(client):
    """Test retrieving food diary"""
    # Register and login
    client.post('/api/register', json={
        'username': 'testuser5',
        'password': 'testpass123'
    })
    
    login_response = client.post('/api/login', json={
        'username': 'testuser5',
        'password': 'testpass123'
    })
    token = login_response.get_json()['token']
    
    # Get diary
    response = client.get('/api/food', 
                         headers={'Authorization': token})
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
