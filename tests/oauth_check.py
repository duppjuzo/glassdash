"""Offline OAuth regression checks: temporary config and loopback callback only."""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import oauth_config as config
import quota_fetchers as fetchers

with tempfile.TemporaryDirectory() as folder, patch.object(config, 'data_dir', return_value=Path(folder)), patch.dict(os.environ, {}, clear=True):
    assert config.google_client() is None
    with patch('webbrowser.open') as browser:
        assert not fetchers.agy_cloud_login()[0]
        browser.assert_not_called()
    imported = Path(folder)/'desktop.json'
    imported.write_text(json.dumps({'installed': {'client_id': 'test-id', 'client_secret': 'test-secret'}}), encoding='utf-8-sig')
    config.import_google_client(imported)
    assert config.google_client() == {'client_id': 'test-id', 'client_secret': 'test-secret'}
    with patch('webbrowser.open', return_value=False):
        ok, message = fetchers.agy_cloud_login()
        assert not ok and '默认浏览器' in message
    callback_errors = []
    threads = []
    def browser_open(url):
        query = parse_qs(urlparse(url).query)
        assert query['client_id'] == ['test-id']
        def callback():
            try:
                base = query['redirect_uri'][0]
                for suffix in ('?code=fake&state=wrong', '/../favicon.ico'):
                    try:
                        urlopen(base + suffix, timeout=5)
                        raise AssertionError('Invalid callback accepted')
                    except HTTPError as error:
                        assert error.code in (400, 404)
                with urlopen(base + '?' + urlencode({'state': query['state'][0], 'error': 'access_denied'}), timeout=5):
                    pass
            except HTTPError as error:
                if error.code != 400:
                    callback_errors.append(error)
            except Exception as error:
                callback_errors.append(error)
        thread = threading.Thread(target=callback)
        threads.append(thread)
        thread.start()
        return True
    with patch('webbrowser.open', side_effect=browser_open), patch.object(fetchers.requests, 'post') as post:
        ok, message = fetchers.agy_cloud_login()
        assert not ok and 'access_denied' in message
        post.assert_not_called()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert not callback_errors, callback_errors
print('OAuth checks passed: config import, missing config, browser failure, callback validation and cancellation.')
