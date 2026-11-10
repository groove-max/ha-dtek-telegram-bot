"""Read only the HA entities explicitly referenced by a message template."""

import asyncio
import re

from jinja2 import Environment, nodes, TemplateError

FUNCTIONS = {"states", "state_attr", "has_value", "is_state"}
ENTITY = re.compile(r"^(?:sensor|binary_sensor)\.[a-z0-9_]+$")
MISSING = "немає даних"


def dependencies(source: str, *, public: bool = False) -> set[str]:
    """Literal entity IDs make dependencies explicit and bounded."""
    if len(source) > 64000:
        raise ValueError("Шаблон завеликий (максимум 64000 символів).")
    try:
        tree = Environment().parse(source)
    except TemplateError as exc:
        raise ValueError(f"Помилка шаблону, рядок {exc.lineno}: {exc.message}") from exc
    entities = set()
    for call in tree.find_all(nodes.Call):
        if not isinstance(call.node, nodes.Name) or call.node.name not in FUNCTIONS:
            continue
        if public:
            raise ValueError("Публічне привітання не може містити дані домашніх сенсорів.")
        if not call.args or not isinstance(call.args[0], nodes.Const) or not isinstance(call.args[0].value, str) or not ENTITY.fullmatch(call.args[0].value):
            raise ValueError("Вкажіть sensor або binary_sensor явно в лапках, наприклад states('sensor.battery').")
        entities.add(call.args[0].value)
    if len(entities) > 32:
        raise ValueError("Один шаблон може використовувати до 32 сенсорів.")
    return entities


async def sensor_context(source: str, ha, *, public: bool = False) -> dict:
    entities = dependencies(source, public=public)
    snapshots = {}
    semaphore = asyncio.Semaphore(8)

    async def read(entity):
        try:
            async with semaphore:
                data = await ha.get_state(entity)
            if isinstance(data, dict):
                snapshots[entity] = data
        except Exception:
            # An optional sensor must not suppress an outage notification.
            pass

    if entities and ha is not None:
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*(read(entity) for entity in entities))
        except TimeoutError:
            pass

    def has_value(entity):
        return snapshots.get(entity, {}).get("state") not in (None, "", "unknown", "unavailable")

    # Notifications use Telegram plain text. Do not reparse sensor content as
    # Jinja or HTML, or HTML-escape it into visible entities like &amp;.
    def states(entity, default=MISSING):
        return str(snapshots[entity]["state"]) if has_value(entity) else default

    def state_attr(entity, attribute, default=None):
        if not has_value(entity):
            return default
        value = snapshots[entity].get("attributes", {}).get(attribute)
        return default if value is None else value

    def is_state(entity, value):
        return has_value(entity) and snapshots[entity]["state"] == value

    return dict(states=states, state_attr=state_attr, has_value=has_value, is_state=is_state)
