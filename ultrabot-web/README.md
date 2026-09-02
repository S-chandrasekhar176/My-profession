# UltraBot Web

## Quick Start

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

Server starts at http://localhost:8000

## API Docs

Open http://localhost:8000/docs for Swagger UI

## First Steps

1. Login: POST /api/auth/login with `{"username": "admin", "password": "admin"}`
2. Set virtual capital: PUT /api/settings/capital with `{"virtual_capital": 100000}`
3. Start engine: POST /api/engine/start with `{"mode": "paper", "broker": "paper"}`
4. Check dashboard: GET /api/dashboard
5. Watch for opportunities: GET /api/opportunities
