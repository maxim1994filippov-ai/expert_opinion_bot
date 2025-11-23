# users_manager.py
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, List

USERS_FILE = "users.json"

def _load() -> Dict[str, Any]:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save(data: Dict[str, Any]):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(chat_id: int) -> Optional[Dict[str, Any]]:
    data = _load()
    return data.get(str(chat_id))

def add_or_update_user(chat_id: int, email: str, password: str):
    data = _load()
    u = data.get(str(chat_id), {})
    u["email"] = email
    u["password"] = password
    if "stats" not in u:
        u["stats"] = []
    data[str(chat_id)] = u
    _save(data)

def remove_user(chat_id: int):
    data = _load()
    if str(chat_id) in data:
        data.pop(str(chat_id))
        _save(data)

def add_record(chat_id: int, title: str, points: int):
    data = _load()
    key = str(chat_id)
    if key not in data:
        data[key] = {"email": None, "password": None, "stats": []}
    stats = data[key].get("stats", [])
    stats.append({
        "title": title,
        "points": points,
        "date": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    })
    data[key]["stats"] = stats
    _save(data)

def summary(chat_id: int) -> Dict[str, any]:
    data = _load()
    u = data.get(str(chat_id), {})
    stats = u.get("stats", [])
    total_surveys = len(stats)
    total_points = sum(s.get("points", 0) for s in stats)
    last5 = list(reversed(stats))[:5]
    return {
        "total_surveys": total_surveys,
        "total_points": total_points,
        "last5": last5
    }

def has_credentials(chat_id: int) -> bool:
    u = get_user(chat_id)
    return bool(u and u.get("email") and u.get("password"))
