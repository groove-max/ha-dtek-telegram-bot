"""Canonical address groups and calendar provenance (legacy compatible)."""

from __future__ import annotations


def canonical_groups(value) -> list[str]:
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        return []
    return sorted(
        {
            v.strip()
            for v in value
            if isinstance(v, str)
            and v.strip() not in ("", "—", "unknown", "unavailable")
        }
    )


def state_groups(state: dict | None) -> list[str]:
    if not state or state.get("state") in ("unknown", "unavailable", ""):
        return []
    return canonical_groups(
        state.get("attributes", {}).get("schedule_groups") or state.get("state")
    )


def event_group(event: dict) -> str:
    description = str(event.get("description", ""))
    return (
        description.split("; group=", 1)[1].strip() if "; group=" in description else ""
    )


def select_events(
    events: list[dict], groups: list[str], selected: str = ""
) -> list[dict]:
    """Never label an untagged union calendar with a single chosen group."""
    if selected and selected not in groups:
        raise ValueError("Selected group is no longer present at this address")
    result = []
    for event in events:
        if "usage=display_only" in str(event.get("description", "")):
            raise ValueError("Display-only schedule cannot drive notifications")
        group = event_group(event)
        if group and group not in groups:
            raise ValueError(
                "Group changed while fetching calendar; retry with a fresh snapshot"
            )
        if not group and len(groups) > 1:
            raise ValueError(
                "Multiple groups require calendar events with group provenance"
            )
        if selected and group and group != selected:
            continue
        copy = dict(event)
        if not group and len(groups) == 1:
            copy["description"] = (
                f"{event.get('description', 'Planned')}; group={groups[0]}"
            )
        result.append(copy)
    return result


def presentation_context(context: dict, state: dict | None) -> dict:
    """Display source group IDs; website visibility is separate metadata."""
    result = dict(context)
    # Keep this presentation switch for saved/default templates: group headings
    # and old -> new transitions now apply to every operator, including Kyiv.
    # It does not claim that the website publishes these IDs as queue numbers.
    result["groups_public"] = True
    attrs = (state or {}).get("attributes", {})
    result["group_labels_visible"] = (
        attrs.get("source_group_labels_visible", attrs.get("group_labels_visible", True)) is True
    )
    return result
