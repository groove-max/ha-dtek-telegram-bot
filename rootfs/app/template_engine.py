"""Jinja2 template engine with file-based overrides."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import re
import hashlib
import os
import tempfile

from jinja2 import BaseLoader, DictLoader, Environment, StrictUndefined, TemplateNotFound

from messages import DEFAULT_TEMPLATES
from template_sensors import dependencies
from utils import format_datetime, format_duration, format_phase_summary

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path("/data/templates")
TEMPLATE_NAME_RE = re.compile(r"^[a-z0-9_]+$")


class _FallbackLoader(BaseLoader):
    """Loads templates from /data/templates/ with fallback to built-in defaults."""

    def __init__(self, templates_dir: Path, defaults: dict[str, str]) -> None:
        self._dir = templates_dir
        self._fallback = DictLoader(defaults)

    def get_source(
        self, environment: Environment, template: str
    ) -> tuple[str, str | None, Callable[[], bool] | None]:
        # Try file override first
        file_path = self._dir / f"{template}.j2"
        if file_path.is_file():
            source = file_path.read_text(encoding="utf-8")
            mtime = file_path.stat().st_mtime
            return source, str(file_path), lambda: file_path.stat().st_mtime == mtime

        # Fallback to built-in
        try:
            return self._fallback.get_source(environment, template)
        except TemplateNotFound:
            raise TemplateNotFound(template)


class TemplateEngine:
    """Renders message templates with Jinja2."""

    def __init__(self, templates_dir: Path = TEMPLATES_DIR) -> None:
        self._dir = templates_dir
        loader = _FallbackLoader(templates_dir, DEFAULT_TEMPLATES)
        self._env = Environment(
            loader=loader,
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self._env.filters["format_duration"] = format_duration
        self._env.filters["format_datetime"] = format_datetime
        self._env.filters["format_phase_summary"] = format_phase_summary

    def render(self, template_name: str, *, profile_id: str = "", **context: object) -> str:
        """Render a template by name with the given context."""
        try:
            tmpl = self._env.from_string(self.get_template_details(template_name, profile_id=profile_id)["source"])
            return tmpl.render(**context).strip()
        except Exception:
            logger.exception("Failed to render template '%s'", template_name)
            return f"[Template error: {template_name}]"

    def render_source(self, source: str, **context: object) -> str:
        """Render a raw template source without persisting it."""
        try:
            tmpl = self._env.from_string(source)
            return tmpl.render(**context).strip()
        except Exception:
            logger.exception("Failed to render raw template source")
            return "[Template error: draft source]"

    def export_defaults(self) -> None:
        """Copy built-in templates to /data/templates/ without overwriting."""
        self._dir.mkdir(parents=True, exist_ok=True)
        exported = 0
        for name, source in DEFAULT_TEMPLATES.items():
            file_path = self._dir / f"{name}.j2"
            if not file_path.exists():
                file_path.write_text(source, encoding="utf-8")
                exported += 1
        logger.info(
            "Exported %d/%d default templates to %s",
            exported,
            len(DEFAULT_TEMPLATES),
            self._dir,
        )

    def clear_cache(self) -> None:
        """Clear cached templates so newly saved overrides take effect immediately."""
        self._env.cache.clear()

    @property
    def templates_dir(self) -> Path:
        """Filesystem directory used for template overrides."""
        return self._dir

    def list_templates(self, *, profile_id: str = "") -> list[dict[str, str]]:
        """Return template metadata for built-ins and file overrides."""
        names = set(DEFAULT_TEMPLATES)
        if self._dir.exists():
            for path in self._dir.glob("*.j2"):
                names.add(path.stem)

        if profile_id:
            names.update(p.stem for p in self._override_path("status_on", profile_id).parent.glob("*.j2"))
        items: list[dict[str, str]] = []
        for name in sorted(names):
            if profile_id and name == "welcome":
                continue
            details = self.get_template_details(name, profile_id=profile_id)
            items.append(details)
        return items

    def _override_path(self, template_name: str, profile_id: str = "") -> Path:
        self._validate_template_name(template_name)
        if profile_id:
            if template_name == "welcome":
                raise ValueError("Привітання спільне для всіх профілів.")
            folder = self._dir / "profiles" / hashlib.sha256(profile_id.encode()).hexdigest()
        else:
            folder = self._dir
        return folder / f"{template_name}.j2"

    def get_template_details(self, template_name: str, *, profile_id: str = "") -> dict[str, str]:
        """Return source, path, and origin for a template."""
        self._validate_template_name(template_name)
        file_path = self._override_path(template_name, profile_id)
        if profile_id and not file_path.is_file():
            return {**self.get_template_details(template_name), "profile_id": profile_id, "inherited": True}
        if file_path.is_file():
            return {
                "name": template_name,
                "origin": "profile" if profile_id else "override",
                "profile_id": profile_id,
                "inherited": False,
                "path": str(file_path),
                "source": file_path.read_text(encoding="utf-8"),
            }
        return {
            "name": template_name,
            "origin": "built_in",
            "path": "",
            "source": DEFAULT_TEMPLATES.get(template_name, ""),
        }

    def save_override(self, template_name: str, source: str, *, profile_id: str = "") -> Path:
        """Write a template override to /data/templates and clear the cache."""
        self._validate_template_name(template_name)
        dependencies(source, public=template_name == "welcome")
        file_path = self._override_path(template_name, profile_id)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=file_path.parent, prefix=".template-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(source)
            os.replace(temp, file_path)
        finally:
            Path(temp).unlink(missing_ok=True)
        self.clear_cache()
        logger.info("Saved template override %s", file_path)
        return file_path

    def delete_override(self, template_name: str, *, profile_id: str = "") -> bool:
        """Delete a template override and revert to the built-in version."""
        self._validate_template_name(template_name)
        file_path = self._override_path(template_name, profile_id)
        if not file_path.exists():
            return False
        file_path.unlink()
        self.clear_cache()
        logger.info("Deleted template override %s", file_path)
        return True

    @staticmethod
    def _validate_template_name(template_name: str) -> None:
        if not TEMPLATE_NAME_RE.fullmatch(template_name):
            raise ValueError(f"Invalid template name: {template_name!r}")
