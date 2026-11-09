"""Conservative restoration estimates from a verified hourly schedule."""

from provider_notice import forecast_suspended
from groups import event_group
from outage_calendar import normalize_calendar_events, parse_event_time, verified_coverage_end


def active_schedule_end(events, groups, now):
    """A single end only when every relevant group is currently in the same outage."""
    ends = {}
    for event in normalize_calendar_events(events):
        start, end = parse_event_time(event["start"]), parse_event_time(event["end"])
        group = event_group(event)
        if group in groups and start <= now < end:
            ends.setdefault(group, set()).add(end)
    if set(ends) != set(groups) or not groups:
        return None
    values = set().union(*ends.values())
    return next(iter(values)) if len(values) == 1 else None


def changed_restoration_end(events, previous, groups, group_state, now, *, started=None, config=None):
    """Never turn missing coverage, midnight clipping or mixed groups into an ETA."""
    attrs = (group_state or {}).get("attributes", {})
    if forecast_suspended(attrs, config, now=now):
        return None
    end = active_schedule_end(events, groups, now)
    if end is not None and forecast_suspended(attrs, config, now=now, at=end):
        return None
    horizon = verified_coverage_end(group_state, groups, now)
    if end is None or horizon is None or end >= horizon:
        return None
    old_end = active_schedule_end(previous or [], groups, now)
    if old_end is None and started is not None and started <= now:
        # An extension may be published just after the previous ETA passed.
        old_end = active_schedule_end(previous or [], groups, started)
    if old_end is None or old_end == end:
        return None
    return end
