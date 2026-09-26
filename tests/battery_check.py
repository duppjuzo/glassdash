import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from battery_stats import BatteryStats

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder)/'stats.json'
    s = BatteryStats(path)
    def sample(t, watts=10, ac=False, remaining=50000, charging=False):
        d = dict(total=watts, ac=ac, charging=charging, remaining_mwh=remaining)
        s.update(d, 60000, tick=t, wall=100000+t)
        return d
    assert sample(0)['since_full'] is None
    d = sample(2, 20)
    assert d['battery_use_seconds'] == 2 and d['average_w'] == 15
    assert 0 < d['remaining_seconds'] < 18000
    assert sample(1002)['battery_use_seconds'] == 2  # Sleep excluded.
    assert sample(1004, ac=True, watts=None)['remaining_seconds'] is None
    d = sample(1006, ac=True, watts=None, remaining=60000)
    assert d['since_full'] == 0 and d['battery_use_seconds'] == 0
    assert sample(1008, ac=True, watts=None, remaining=60000)['since_full'] == 2
    sample(1010, remaining=58000)
    d = sample(1012, remaining=58000)
    assert d['battery_use_seconds'] == 2 and d['average_w'] == 10
    s.save()
    restored = BatteryStats(path)
    assert restored.state['seconds'] == 2 and restored.state['full_at'] == 101006
    assert restored.previous is None and restored.state['partial']
    d = sample(1014, ac=True, watts=None, remaining=60000)
    assert d['since_full'] == 0 and d['average_w'] is None
print('PASS: integration, sleep gap, AC, full-charge reset/latch, persistence, unknown history')
