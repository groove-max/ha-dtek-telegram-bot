"""Built-in default message templates (Ukrainian)."""

DEFAULT_TEMPLATES: dict[str, str] = {
    # ── Feature 1: Schedule Change ──
    "schedule_change": """\
⚡️ {{ display_name }}
🔵 Група {{ group }}
🔄 Оновлення графіка ВІДКЛЮЧЕНЬ:

⛔ Коли за графіком НЕ буде світла:
{% for line in schedule_lines %}{{ line }}
{% endfor %}
⚠️ ВАЖЛИВО: при АВАРІЙНИХ відключеннях графіки можуть НЕ діяти.""",
    "schedule_empty": """\
⚡️ {{ display_name }}
🔵 Група {{ group }}
🔄 Оновлення графіка ВІДКЛЮЧЕНЬ:

✅ Відключення за графіком не плануються.

⚠️ ВАЖЛИВО: при АВАРІЙНИХ відключеннях графіки можуть НЕ діяти.""",
    # ── Feature 2: Emergency Outage ──
    "emergency_start": """\
⚡️ {{ display_name }}

❗️ Зафіксовано відключення.
📋 {{ description or outage_type }}
🕦 Початок: {{ start }}
🕦 Відновлення: {{ end }} (оріент.)""",
    "emergency_update": """\
⚡️ {{ display_name }}

{% if reason_changed and end_changed %}🔄 Параметри відключення змінено.
{% elif reason_changed %}🔄 Тип відключення змінено.
{% elif end_changed %}🔄 Час відновлення змінено.
{% endif %}{% if reason_changed %}📋 Причина: {{ new_reason }}
{% endif %}{% if end_changed %}🕦 Новий орієнтовний час: {{ new_end }}
{% endif %}""",
    "emergency_end": """\
⚡️ {{ display_name }}

✅ Електропостачання відновлено (за даними ДТЕК).
⏱ Тривалість: {{ duration }}""",
    # ── Feature 3: Group Change ──
    "group_change": """\
⚡️ {{ display_name }}

🔄 Група графіку змінена: {{ old_group }} → {{ new_group }}
{% if schedule_lines %}
⛔ Графік відключень для нової групи:
{% for line in schedule_lines %}{{ line }}
{% endfor %}
⚠️ При аварійних відключеннях графіки можуть не діяти.
{% else %}
✅ Відключення за графіком не плануються.
{% endif %}""",
    # ── Feature 4: Voltage Quality ──
    "voltage_low": """\
⚡️ {{ display_name }}
🔵 Група {{ group }}

🟡 Низька напруга {% if phase_label %}на фазі {{ phase_label }}{% else %}в мережі{% endif %}: {{ voltage | round(1) }} В
🔧 Можливі перезапуски техніки / нестабільна робота.
🕒 {{ timestamp }}""",
    "voltage_high": """\
⚡️ {{ display_name }}
🔵 Група {{ group }}

🟠 Завищена напруга {% if phase_label %}на фазі {{ phase_label }}{% else %}в мережі{% endif %}: {{ voltage | round(1) }} В
🔧 Ризик для чутливої техніки.
🕒 {{ timestamp }}""",
    "voltage_normal": """\
⚡️ {{ display_name }}
🔵 Група {{ group }}

✅ Напруга {% if phase_label %}на фазі {{ phase_label }} {% endif %}в нормі: {{ voltage | round(1) }} В
🕒 {{ timestamp }}""",
    # ── Feature 5: Power Presence ──
    "power_lost": """\
⚡️ {{ display_name }}

⛔ Світло зникло.
✅ Світло було: {{ duration }}.
🕒 {{ timestamp }}""",
    "power_restored": """\
⚡️ {{ display_name }}

{% if house_state == "partial" %}🟠 Світло з'явилось частково{% else %}✅ Світло з'явилось{% endif %}.
⛔ Без світла було: {{ duration }}.
{% if missing_phases %}
⚠️ Відсутні фази: {{ missing_phases | join(", ") }}
{% endif %}
{% if unknown_phases %}
❔ Невизначені фази: {{ unknown_phases | join(", ") }}
{% endif %}
🕒 {{ timestamp }}""",
    # ── Feature 5+: Phase Monitoring ──
    "phase_lost": """\
⚡️ {{ display_name }}

⚠️ Пропала фаза {{ phase_label }}
{{ phases | format_phase_summary }}
🕒 {{ timestamp }}""",
    "phase_restored": """\
⚡️ {{ display_name }}

✅ Фаза {{ phase_label }} відновлена
{{ phases | format_phase_summary }}
🕒 {{ timestamp }}""",
    # ── Feature 6: Upcoming Outage Warning ──
    "upcoming_outage": """\
⚡️ {{ display_name }}
🔵 Група {{ group }}

⏳ Можливе планове відключення за графіком (через {{ minutes }} хв).
⛔ Від: {{ start }}
✅ До: {{ end }}
🕒 {{ timestamp }}""",
    # ── Feature 7: Status Message ──
    "status_on": """\
🟢 {{ short_name }}
🔵 Група {{ group }}
{% if voltage is not none or phases %}
✅ Електропостачання: норма
{% if phases %}
{{ phases | format_phase_summary }}
{% else %}
🔌 Напруга: {{ voltage | round(1) }} В
{% endif %}
{% endif %}
{% if next_outage %}
📅 Найближче планове: {{ next_outage }}
{% endif %}
🕒 Оновлено: {{ timestamp }}""",
    "status_partial": """\
🟠 {{ short_name }}
🔵 Група {{ group }}

⚠️ Електропостачання: частково наявне
{% if phases %}
{{ phases | format_phase_summary }}
{% endif %}
{% if missing_phases %}
⚠️ Відсутні фази: {{ missing_phases | join(", ") }}
{% endif %}
{% if unknown_phases %}
❔ Невизначені фази: {{ unknown_phases | join(", ") }}
{% endif %}
{% if next_outage %}
📅 Найближче планове: {{ next_outage }}
{% endif %}
🕒 Оновлено: {{ timestamp }}""",
    "status_off": """\
🔴 {{ short_name }}
🔵 Група {{ group }}

⛔ Електропостачання: відсутнє{{ (" " ~ outage_duration) if outage_duration else "" }}
{% if outage_description or outage_type %}
📋 Причина: {{ outage_description or outage_type }}
{% elif dtek_reports_ok %}
📋 Причина: невідома
ℹ️ За даними ДТЕК активного відключення немає.
{% else %}
📋 Причина: невідома
{% endif %}
{% if not dtek_reports_ok and outage_start %}
🕦 З: {{ outage_start }}
{% endif %}
{% if not dtek_reports_ok and outage_end %}
🕦 До: {{ outage_end }} (орієнт.)
{% endif %}
🕒 Оновлено: {{ timestamp }}""",
}
