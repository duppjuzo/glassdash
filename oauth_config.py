"""OAuth application credentials belong to user state, never release assets."""
import json
import os
from app_paths import data_dir


def google_client():
    values = {}
    try:
        values = json.loads((data_dir()/'google_oauth.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        pass
    if not isinstance(values, dict):
        values = {}
    client_id = os.environ.get('GLASSDASH_GOOGLE_CLIENT_ID') or values.get('client_id')
    secret = os.environ.get('GLASSDASH_GOOGLE_CLIENT_SECRET') or values.get('client_secret')
    if not isinstance(client_id, str) or not isinstance(secret, str) or not client_id.strip() or not secret.strip():
        return None
    return {'client_id': client_id.strip(), 'client_secret': secret.strip()}


def save_google_client(client_id, secret):
    path = data_dir()/'google_oauth.json'
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps({'client_id':client_id.strip(), 'client_secret':secret.strip()},indent=2),encoding='utf-8')
    temp.replace(path)
