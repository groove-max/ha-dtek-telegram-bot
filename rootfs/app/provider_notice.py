"""Consume HA's announcement contract without reinterpreting DTEK's prose."""
from datetime import datetime
from html import escape
from urllib.parse import urlsplit
import re

from utils import TZ_KYIV, now_kyiv


def notice_enabled(config, option=None):
    """Defaults also apply to older profiles that predate these settings."""
    settings = getattr(config, 'provider_notice', None)
    if isinstance(settings, bool):
        return settings
    def value(key):
        return settings.get(key, True) if isinstance(settings, dict) else getattr(settings, key, True)
    return bool(value('enabled') and (not option or value(option)))


def forecast_suspended(attrs, config=None, *, now=None, at=None):
    return notice_enabled(config, 'pause_forecasts') and applicability(attrs, now=now, at=at) is False


def configured_grid(data, config):
    """Hide announcement overlays/captions without changing shared HA data."""
    if notice_enabled(config, 'show_on_images'):
        return data
    def clean(row):
        return {key: value for key, value in row.items()
                if not key.startswith(('provider_notice_', 'schedule_appl'))}
    return {**clean(data), 'days': [clean(day) for day in data.get('days', [])]}


def applicability(attrs, *, now=None, at=None):
    # An older integration has no announcement contract; preserve compatibility.
    if 'schedule_applies' not in attrs:
        return None
    now = now or now_kyiv()
    if attrs.get('provider_notice_error') or attrs.get('provider_notice_state') == 'unavailable':
        return None
    try:
        until = datetime.fromisoformat(attrs.get('provider_notice_valid_until') or '')
        if until.tzinfo is None or now >= until:
            return None
    except (ValueError, TypeError):
        return None
    day = (at or now).astimezone(TZ_KYIV).date().isoformat()
    days = attrs.get('schedule_applicability_days') or {}
    value = days.get(day, attrs.get('schedule_applies') if attrs.get('schedule_applicability_date') == day else None)
    return value if type(value) is bool else None


def notice_kind(attrs, *, now=None, at=None):
    """Keep no notice, unverified retrieval and an explicit warning distinct."""
    if 'provider_notice_state' not in attrs and 'schedule_applies' not in attrs:
        return 'hidden'
    now = now or now_kyiv()
    try:
        until = datetime.fromisoformat(attrs.get('provider_notice_valid_until') or '')
        fresh = until.tzinfo is not None and now < until
    except (ValueError, TypeError):
        fresh = False
    if not fresh or attrs.get('provider_notice_error') or attrs.get('provider_notice_state') == 'unavailable':
        return 'unavailable'
    if not str(attrs.get('provider_notice_text') or '').strip():
        return 'hidden'
    # Grid payloads describe their selected date, which may be tomorrow.
    if at is None and attrs.get('date'):
        try:
            at = datetime.fromisoformat(attrs['date']).replace(tzinfo=TZ_KYIV)
        except (ValueError, TypeError):
            return 'info'
    return 'warning' if applicability(attrs, now=now, at=at) is False else 'info'


def notice_source(attrs):
    source = str(attrs.get('provider_notice_source') or '')
    if len(source.encode('utf-16-le')) // 2 > 2048:
        return ''
    try:
        url = urlsplit(source)
        if (url.scheme == 'https' and not url.username and not url.password and not url.port
                and re.fullmatch(r'(?:www\.)?dtek-(?:oem|kem|krem|dnem)\.com\.ua', url.hostname or '')
                and not any(ord(c) < 32 for c in source)):
            return source
    except ValueError:
        pass
    return ''


def warning(attrs, *, now=None, at=None):
    kind = notice_kind(attrs, now=now, at=at)
    if kind == 'hidden':
        return ''
    if kind == 'unavailable':
        return 'Не вдалося перевірити оголошення ДТЕК.'
    if kind == 'warning':
        return 'ДТЕК повідомляє про екстрені відключення. Графік може не відповідати фактичним відключенням.'
    return 'Є оголошення ДТЕК щодо відключень.'


def image_notice(attrs, *, now=None):
    kind = notice_kind(attrs, now=now)
    if kind == 'hidden':
        return ''
    return 'Не вдалося перевірити оголошення ДТЕК' if kind == 'unavailable' else 'Є оголошення ДТЕК'


def _notice_message(attrs, *, now=None, at=None, general=False, html=False):
    text = warning(attrs, now=now, at=at)
    if not text:
        return ''
    kind = notice_kind(attrs, now=now, at=at)
    if general and kind == 'warning':
        text = 'Є оголошення ДТЕК щодо відключень. Перевірте територію та час дії.'
    icon = '⚠️' if kind == 'warning' and not general else 'ℹ️' if kind == 'unavailable' else '📢'
    format_text = escape if html else str
    result = '\n\n' + icon + ' ' + format_text(text)
    source = notice_source(attrs)
    if source:
        if html:
            return result + '\n<a href="' + escape(source, quote=True) + '">Повідомлення на сайті ДТЕК ↗</a>'
        return result + '\n' + source
    # The original may still be useful when no safe source URL was supplied.
    original = str(attrs.get('provider_notice_text') or '').strip()
    if original and kind != 'unavailable':
        result += '\n' + format_text(original[:300] + ('…' if len(original) > 300 else ''))
    return result


def warning_text(attrs, *, now=None, at=None):
    """Notice for text templates; Telegram recognizes the source URL directly."""
    return _notice_message(attrs, now=now, at=at)


def warning_html(attrs, *, now=None, at=None, general=False):
    """Notice for callers that explicitly send with parse_mode=HTML."""
    return _notice_message(attrs, now=now, at=at, general=general, html=True)


def caption_notice(attrs, *, now=None, at=None):
    """Plain-text caption for automatically delivered/edited schedule photos."""
    text = warning(attrs, now=now, at=at)
    source = notice_source(attrs)
    return ('\n\n📢 ' + text + ('\n' + source if source else '')) if text else ''


def contract_identity(attrs):
    if 'schedule_applies' not in attrs:
        return None
    return [attrs.get(k) for k in ('provider_notice_revision', 'provider_notice_state',
                                  'schedule_applies', 'schedule_applicability_days')]


def append_warning(text, attrs, *, now=None):
    """Append a text notice without changing the template's text semantics."""
    tail = warning_text(attrs, now=now)
    if not tail:
        return text
    # An override may already place the supplied provider_notice variable.
    if tail.strip() in text and len(text.encode('utf-16-le')) // 2 <= 4096:
        return text
    if len((text+tail).encode('utf-16-le')) // 2 <= 4096:
        return text+tail
    # Do not interpret literal <, > or & in existing plain-text overrides.
    budget = 4096 - len(tail.encode('utf-16-le')) // 2 - 1
    short = text.encode('utf-16-le')[:max(0, budget)*2].decode('utf-16-le', errors='ignore')
    return short+'…'+tail
