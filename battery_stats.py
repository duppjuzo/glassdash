"""Persist observed battery sessions; never invent history across sampling gaps."""
import json
import math
import time
from pathlib import Path
from app_paths import state_path


def duration(seconds):
    if seconds is None:
        return '—'
    minutes = max(0, int(seconds // 60))
    if minutes < 1:
        return '<1分钟'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}时{minutes:02d}分' if hours else f'{minutes}分钟'


class BatteryStats:
    def __init__(self, path=None):
        self.path = Path(path) if path else state_path('battery_stats.json')
        self.state = dict(full_at=None, full_latched=False, seconds=0., watt_seconds=0., partial=True)
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            for key in ('seconds', 'watt_seconds'):
                value = float(saved[key])
                if not math.isfinite(value) or value < 0:
                    raise ValueError()
                self.state[key] = value
            full = saved.get('full_at')
            if full is not None and (not isinstance(full, (int, float)) or not math.isfinite(full) or full < 0):
                raise ValueError()
            self.state.update(full_at=full, full_latched=bool(saved.get('full_latched')), partial=True)
        except (OSError, ValueError, KeyError, TypeError):
            self.state = dict(full_at=None, full_latched=False, seconds=0., watt_seconds=0., partial=True)
        self.previous = None
        self.recent_w = None
        self.last_save = 0

    def save(self):
        try:
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.state, indent=2), encoding='utf-8')
            temp.replace(self.path)
        except OSError:
            pass

    def update(self, d, full_capacity, *, wall=None, tick=None):
        wall = time.time() if wall is None else wall
        tick = time.monotonic() if tick is None else tick
        s = self.state
        capacity = d.get('remaining_mwh')
        ratio = capacity/full_capacity if capacity is not None and full_capacity else None
        full_now = d.get('ac') is True and d.get('charging') is False and ratio is not None and ratio >= .99
        reset = full_now and not s['full_latched']
        if reset:
            s.update(full_at=wall, full_latched=True, seconds=0., watt_seconds=0., partial=False)
            self.previous = None
        elif ratio is not None and ratio < .98:
            s['full_latched'] = False
        watts = d.get('total')
        active = d.get('ac') is False and watts is not None and math.isfinite(watts) and 0 < watts < 1000
        if self.previous:
            last_tick, last_w, last_active = self.previous
            dt = tick-last_tick
            if 0 < dt <= 10 and active and last_active:
                s['seconds'] += dt
                s['watt_seconds'] += (watts+last_w)*.5*dt
            elif dt > 10:
                s['partial'] = True
                self.recent_w = None
        if active:
            dt = min(10, max(0, tick-self.previous[0])) if self.previous else 0
            alpha = 1-math.exp(-dt/60)
            self.recent_w = watts if self.recent_w is None else self.recent_w + alpha*(watts-self.recent_w)
        else:
            self.recent_w = None
        self.previous = (tick, watts, active)
        remaining = capacity/1000/self.recent_w*3600 if active and capacity is not None and self.recent_w else None
        d.update(remaining_seconds=remaining,
                 since_full=max(0,wall-s['full_at']) if s['full_at'] is not None else None,
                 battery_use_seconds=s['seconds'],
                 average_w=s['watt_seconds']/s['seconds'] if s['seconds'] else None,
                 stats_partial=s['partial'])
        if reset or tick-self.last_save >= 30:
            self.save()
            self.last_save = tick
