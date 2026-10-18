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


def public_groups(state: dict | None) -> bool:
    """Old integrations expose public groups; new ones explicitly declare visibility."""
    return (state or {}).get("attributes", {}).get("group_labels_visible", True) is True


def display_labels(state: dict | None) -> dict[str, str] | None:
    """Local variant numbers are not public queue numbers or stable identities."""
    if public_groups(state):
        return None
    return {g: f"Варіант {index + 1}" for index, g in enumerate(state_groups(state))}


def display_group(value: str, state: dict | None) -> str:
    labels = display_labels(state)
    if labels is None:
        return value
    return ", ".join(labels.get(g, "Графік адреси") for g in canonical_groups(value))


def presentation_context(context: dict, state: dict | None) -> dict:
    """Replace opaque IDs only at the presentation boundary, including overrides."""
    result = dict(context)
    result["groups_public"] = public_groups(state)
    if not result["groups_public"]:
        for key in ("group", "old_group", "new_group", "selected_group"):
            if key in result:
                result[key] = display_group(result[key], state)
        if "groups" in result:
            result["groups"] = [display_group(g, state) for g in result["groups"]]
    return result
