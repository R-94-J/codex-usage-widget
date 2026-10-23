"""Read local Codex usage metadata only. No network or credential access."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

UTC = timezone.utc
CHINA = timezone(timedelta(hours=8), 'Asia/Shanghai')
COUNTERS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
            'output_tokens', 'reasoning_output_tokens')
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MAX_LINE = 8 * 1024 * 1024


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Undated/naive timestamps must not silently enter today's totals.
        return result.astimezone(UTC) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def normalized_usage(raw: Mapping | None) -> dict[str, int] | None:
    if not isinstance(raw, Mapping):
        return None
    result = {}
    for key in COUNTERS:
        value = raw.get(key, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError('invalid nonnegative integer usage')
        result[key] = value
    if (result['cached_input_tokens'] + result['cache_write_input_tokens'] > result['input_tokens']
            or result['reasoning_output_tokens'] > result['output_tokens']):
        raise ValueError('invalid cache/reasoning partition')
    result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    return result


def _difference(current: Mapping[str, int], previous: Mapping[str, int]) -> tuple[dict, bool]:
    result = {key: max(0, current[key] - previous[key]) for key in COUNTERS}
    precise = all(current[key] >= previous[key] for key in COUNTERS)
    # A provider can start reporting cache-write totals midway through a session.
    # Preserve authoritative input/output counts, but never price invented detail.
    cached = min(result['cached_input_tokens'], result['input_tokens'])
    write = min(result['cache_write_input_tokens'], result['input_tokens'] - cached)
    reasoning = min(result['reasoning_output_tokens'], result['output_tokens'])
    if (cached, write, reasoning) != (result['cached_input_tokens'],
                                     result['cache_write_input_tokens'],
                                     result['reasoning_output_tokens']):
        precise = False
    result.update(cached_input_tokens=cached, cache_write_input_tokens=write,
                  reasoning_output_tokens=reasoning)
    result['total_tokens'] = result['input_tokens'] + result['output_tokens']
    return result, precise


def sum_usage(events) -> dict[str, int]:
    result = {key: 0 for key in (*COUNTERS, 'total_tokens')}
    for event in events:
        for key in result:
            result[key] += event.usage.get(key, 0)
    return result


@dataclass(frozen=True)
class UsageEvent:
    session_id: str
    timestamp: datetime | None
    model: str
    provider: str
    service_tier: str | None
    usage: dict[str, int]
    precise: bool
    key: str


@dataclass(frozen=True)
class SessionUsage:
    id: str
    model: str
    provider: str
    created_at: datetime | None
    latest_activity: datetime
    is_subagent: bool
    events: tuple[UsageEvent, ...]
    source: str = ''
    forked_from_id: str | None = None

    @property
    def total_tokens(self) -> int:
        return sum(e.usage['total_tokens'] for e in self.events)


@dataclass(frozen=True)
class UsageSnapshot:
    sessions: tuple[SessionUsage, ...]
    today_events: tuple[UsageEvent, ...]
    updated_at: datetime
    errors: tuple[str, ...]
    files_scanned: int
    bytes_read: int

    @property
    def today_tokens(self) -> int:
        return sum(e.usage['total_tokens'] for e in self.today_events)


@dataclass
class _Cursor:
    path: Path
    identity: tuple[int, int]
    offset: int = 0
    stamp: tuple[int, int] = (-1, -1)
    checkpoint: bytes = b''
    session_id: str = ''
    model: str = 'unknown'
    provider: str = ''
    source: str = ''
    created_at: datetime | None = None
    latest_activity: datetime = EPOCH
    is_subagent: bool = False
    forked_from_id: str | None = None
    service_tier: str | None = None
    turn_id: str = ''
    previous: dict[str, int] | None = None
    events: list[UsageEvent] = field(default_factory=list)
    seen: set[str] = field(default_factory=set)
    seen_records: set[str] = field(default_factory=set)
    pending_last_only: dict[str, int] = field(default_factory=lambda: normalized_usage({}))
    malformed: int = 0
    uncertain: int = 0

    def activity(self, timestamp: datetime | None):
        if timestamp:
            self.latest_activity = max(self.latest_activity, timestamp)

    def append_event(self, usage: dict, timestamp: datetime | None,
                     precise: bool, cumulative: dict | None, kind: str):
        if usage['total_tokens'] == 0:
            return
        signature = json.dumps([self.session_id, timestamp.isoformat() if timestamp else None,
                                self.turn_id, self.model, cumulative or usage, kind], sort_keys=True)
        key = hashlib.sha256(signature.encode()).hexdigest()
        if key in self.seen:
            return
        self.seen.add(key)
        self.events.append(UsageEvent(self.session_id, timestamp, self.model, self.provider,
                                      self.service_tier, usage, precise, key))
        self.activity(timestamp)
        if not precise:
            self.uncertain += 1

    def consume_usage(self, info: dict, timestamp: datetime | None):
        cumulative = normalized_usage(info.get('total_token_usage'))
        last = normalized_usage(info.get('last_token_usage'))
        if cumulative is None and last is None:
            return
        # Replays must be rejected BEFORE changing the cumulative checkpoint.
        # A genuine reset has a new timestamp, even if its counters match history.
        identity = json.dumps([timestamp.isoformat() if timestamp else None, cumulative, last], sort_keys=True)
        identity = hashlib.sha256(identity.encode()).hexdigest()
        if identity in self.seen_records:
            return
        self.seen_records.add(identity)
        inherited = (self.forked_from_id and self.created_at and timestamp
                     and timestamp < self.created_at)
        if inherited:
            if cumulative is not None:
                self.previous = cumulative
            return
        if cumulative is None:
            self.append_event(last, timestamp, timestamp is not None, None, 'request')
            for key in self.pending_last_only:
                self.pending_last_only[key] += last[key]
            return
        if self.previous == cumulative:
            return  # A stale snapshot does not consume pending last-only calls.
        previous = self.previous
        self.previous = cumulative
        reset = (previous is not None and
                 (cumulative['input_tokens'] < previous['input_tokens'] or
                  cumulative['output_tokens'] < previous['output_tokens']))
        if reset and last is not None and all(cumulative[k] >= last[k] for k in COUNTERS):
            # The new last request is known. Only the earlier part of the reset
            # snapshot can overlap already-observed last-only requests. Do not
            # subtract an old-epoch pending call from this new request itself.
            baseline, _ = _difference(cumulative, last)
            remaining, _ = _difference(baseline, self.pending_last_only)
            if baseline['total_tokens'] and self.pending_last_only['total_tokens']:
                # Without a reset event, overlap placement is ambiguous. Report
                # incomplete coverage rather than assert another charge/baseline.
                self.uncertain += 1
            self.pending_last_only = normalized_usage({})
            if not self.forked_from_id:
                self.append_event(remaining, None, False, cumulative, 'baseline')
            self.append_event(last, timestamp, timestamp is not None, cumulative, 'request')
            return
        if previous is None or reset:
            remaining, sane = _difference(cumulative, self.pending_last_only)
            self.pending_last_only, _ = _difference(self.pending_last_only, cumulative)
            if last is not None and all(remaining[k] >= last[k] for k in COUNTERS):
                baseline, _ = _difference(remaining, last)
                if not self.forked_from_id:
                    self.append_event(baseline, None, False, cumulative, 'baseline')
                self.append_event(last, timestamp, sane and timestamp is not None, cumulative, 'request')
            elif not self.forked_from_id:
                # Initial fork snapshots may be entirely inherited. Keep their
                # checkpoint, but never assign their unknown baseline to the child.
                self.append_event(remaining, None, False, cumulative, 'baseline')
            return
        delta, sane = _difference(cumulative, previous)
        new_usage, reconciled = _difference(delta, self.pending_last_only)
        self.pending_last_only, _ = _difference(self.pending_last_only, delta)
        exact = last is not None and all(new_usage[k] == last[k] for k in COUNTERS)
        self.append_event(new_usage, timestamp, sane and reconciled and exact and timestamp is not None,
                          cumulative, 'request')

    def consume(self, raw: bytes):
        # Conversation and tool output are not decoded or retained. Check the header,
        # not occurrences of record names buried inside quoted conversation content.
        header = raw[:180]
        if not any(x in header for x in (b'"session_meta"', b'"turn_context"', b'"event_msg"')):
            return
        if not raw.rstrip().endswith(b'}'):
            self.malformed += 1
            return
        if b'"event_msg"' in header and b'"token_count"' not in raw[:650]:
            return
        try:
            record = json.loads(raw)
            if not isinstance(record, dict):
                return
            body = record.get('payload')
            if not isinstance(body, dict):
                return
            timestamp = parse_time(record.get('timestamp'))
            kind = record.get('type')
            if kind == 'session_meta':
                self.session_id = str(body.get('id') or self.path.stem)
                self.created_at = parse_time(body.get('timestamp')) or timestamp
                self.activity(self.created_at)
                source = body.get('source')
                self.source = source if isinstance(source, str) else 'subagent' if isinstance(source, dict) and 'subagent' in source else 'unknown'
                self.is_subagent = self.source == 'subagent' or 'subagent' in str(body.get('originator','')).lower()
                self.provider = str(body.get('model_provider') or '')
                self.forked_from_id = body.get('forked_from_id') or None
            elif kind == 'turn_context':
                self.model = str(body.get('model') or self.model)
                self.provider = str(body.get('model_provider') or self.provider)
                self.service_tier = body.get('service_tier')
                self.turn_id = str(body.get('turn_id') or '')
                self.activity(timestamp)
            elif kind == 'event_msg' and body.get('type') == 'token_count':
                if isinstance(body.get('info'), dict):
                    self.consume_usage(body['info'], timestamp)
        except (ValueError, TypeError, OverflowError, UnicodeError):
            self.malformed += 1


class UsageCollector:
    """Single-thread-owned tailer. Returned snapshots are separate immutable records."""

    def __init__(self, home: Path | str, utc_offset_minutes: int = 480):
        self.home = Path(home)
        self.zone = timezone(timedelta(minutes=utc_offset_minutes))
        self._cursors: dict[Path, _Cursor] = {}

    def _read(self, path: Path) -> int:
        stat = path.stat()
        identity = (stat.st_dev, stat.st_ino)
        stamp = (stat.st_mtime_ns, stat.st_size)
        cursor = self._cursors.get(path)
        if cursor is not None and identity == cursor.identity and stamp == cursor.stamp:
            return 0
        with path.open('rb') as stream:
            reset = cursor is None or cursor.identity != identity or stat.st_size < cursor.offset
            if not reset and cursor.offset:
                stream.seek(max(0, cursor.offset-len(cursor.checkpoint)))
                if stream.read(len(cursor.checkpoint)) != cursor.checkpoint:
                    reset = True
            if reset:
                cursor = _Cursor(path, identity, session_id=path.stem)
                self._cursors[path] = cursor
            stream.seek(cursor.offset)
            bytes_read = 0
            while True:
                raw = stream.readline(MAX_LINE+1)
                if not raw:
                    break
                bytes_read += len(raw)
                if len(raw) > MAX_LINE:
                    while raw and not raw.endswith(b'\n'):
                        raw = stream.readline(MAX_LINE+1)
                        bytes_read += len(raw)
                    if not raw.endswith(b'\n'):
                        break
                    cursor.offset = stream.tell()
                    continue
                if not raw.endswith(b'\n'):
                    # Keep the offset at the previous complete UTF-8 record.
                    break
                cursor.offset = stream.tell()
                cursor.consume(raw)
            if cursor.offset:
                stream.seek(max(0, cursor.offset-256))
                cursor.checkpoint = stream.read(min(cursor.offset, 256))
            cursor.stamp = stamp
            return bytes_read

    def poll(self, now: datetime | None = None) -> UsageSnapshot:
        now = now or datetime.now(UTC)
        if not now.tzinfo:
            now = now.replace(tzinfo=self.zone)
        today = now.astimezone(self.zone).date()
        paths = set()
        errors = []
        bytes_read = 0
        for dirname in ('sessions', 'archived_sessions'):
            try:
                paths.update((self.home/dirname).rglob('*.jsonl'))
            except OSError as exc:
                errors.append(f'{dirname}: 无法枚举日志 ({type(exc).__name__})')
        for path in sorted(paths):
            try:
                bytes_read += self._read(path)
            except OSError as exc:
                errors.append(f'{path.name}: 暂时无法读取 ({type(exc).__name__})')
        for missing in self._cursors.keys()-paths:
            del self._cursors[missing]
        groups: dict[str, list[_Cursor]] = {}
        for cursor in self._cursors.values():
            groups.setdefault(cursor.session_id, []).append(cursor)
            if cursor.malformed:
                errors.append(f'{cursor.path.name}: 跳过 {cursor.malformed} 条损坏记录')
            if cursor.uncertain:
                errors.append(f'{cursor.path.name}: {cursor.uncertain} 条用量缺少完整逐次明细')
        sessions = []
        for sid, cursors in groups.items():
            latest = max(cursors, key=lambda c: c.latest_activity)
            unique = {}
            for cursor in cursors:
                for event in cursor.events:
                    unique[event.key] = event
            events = tuple(sorted(unique.values(), key=lambda e:(e.timestamp or EPOCH,e.key)))
            sessions.append(SessionUsage(sid,latest.model,latest.provider,latest.created_at,
                                         latest.latest_activity,latest.is_subagent,events,
                                         latest.source,latest.forked_from_id))
        sessions.sort(key=lambda s:(s.latest_activity,s.id), reverse=True)
        today_events = tuple(e for s in sessions for e in s.events
                             if e.timestamp and e.timestamp.astimezone(self.zone).date() == today)
        return UsageSnapshot(tuple(sessions),today_events,now,tuple(errors),len(paths),bytes_read)
