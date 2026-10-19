"""Built-in default message templates (Ukrainian)."""

DEFAULT_TEMPLATES: dict[str, str] = {
    "welcome": """\
⚡ Цей бот автоматизує сповіщення про графіки відключень і стан електропостачання. Також він може нагадувати про покази лічильника та передавати їх у ДТЕК.

Хочете налаштувати такого бота для себе або свого будинку? Код та інструкції для Home Assistant доступні на GitHub:
🤖 <a href="https://github.com/groove-max/ha-dtek-telegram-bot">Telegram-бот</a>
🏠 <a href="https://github.com/groove-max/ha-dtek-monitor">Інтеграція ДТЕК</a>

Ваш Telegram ID: <code>{{ telegram_user_id }}</code>""",
    # ── Feature 1: Schedule Change ──
    "schedule_change": """\
⚡️ {{ display_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}
🔄 Оновлення графіка ВІДКЛЮЧЕНЬ:

{% if groups|default([])|length > 1 and not selected_group %}{% if groups_public|default(true) %}ℹ️ Адреса має кілька груп. Графіки наведені окремо; група вашої квартири потребує уточнення.{% else %}ℹ️ Адреса має кілька варіантів графіка. Вони наведені окремо; відповідність вашій квартирі потребує уточнення.{% endif %}

{% endif %}⛔ Коли за графіком НЕ буде світла:
{% for line in schedule_lines %}{{ line }}
{% endfor %}
⚠️ ВАЖЛИВО: при АВАРІЙНИХ відключеннях графіки можуть НЕ діяти.""",
    "schedule_empty": """\
⚡️ {{ display_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}
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

{% if groups_public|default(true) %}🔄 Група графіку змінена: {{ old_group }} → {{ new_group }}
{% else %}🔄 Оновлено прив’язку адреси до графіка.
{% endif %}
{% if schedule_lines %}
⛔ Графік відключень{% if groups_public|default(true) %} для нової групи{% endif %}:
{% for line in schedule_lines %}{{ line }}
{% endfor %}
⚠️ При аварійних відключеннях графіки можуть не діяти.
{% elif schedule_complete|default(false) %}
✅ Відключення за графіком не плануються.
{% endif %}""",
    # ── Feature 4: Voltage Quality ──
    "voltage_low": """\
⚡️ {{ display_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

🟡 Низька напруга {% if phase_label %}на фазі {{ phase_label }}{% else %}в мережі{% endif %}: {{ voltage | round(1) }} В
🔧 Можливі перезапуски техніки / нестабільна робота.
🕒 {{ timestamp }}""",
    "voltage_high": """\
⚡️ {{ display_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

🟠 Завищена напруга {% if phase_label %}на фазі {{ phase_label }}{% else %}в мережі{% endif %}: {{ voltage | round(1) }} В
🔧 Ризик для чутливої техніки.
🕒 {{ timestamp }}""",
    "voltage_normal": """\
⚡️ {{ display_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

✅ Напруга {% if phase_label %}на фазі {{ phase_label }} {% endif %}в нормі: {{ voltage | round(1) }} В
🕒 {{ timestamp }}""",
    # ── Feature 5: Power Presence ──
    "power_telemetry_lost": """⚡️ {{ display_name }}

❔ Немає достовірних даних про живлення.
Датчик недоступний або дані застаріли. Можливе відключення світла чи збій зв’язку; відключення не підтверджено.
🕒 {{ timestamp }}""",
    "power_telemetry_restored": """⚡️ {{ display_name }}

✅ Дані про живлення знову надходять.
{% if house_state == 'off' %}⛔ Підтверджено відсутність напруги.{% elif house_state == 'partial' %}🟠 Напруга є принаймні на частині фаз.{% else %}⚡ Напруга є.{% endif %}

ℹ️ Сам факт відключення під час втрати даних невідомий.
🕒 {{ timestamp }}""",
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
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

⏳ Можливе планове відключення за графіком (через {{ minutes }} хв).
⛔ Від: {{ start }}
{% if end_confirmed|default(true) %}✅ До: {{ end }}
{% else %}🕓 Завершення за графіком: невідомо
{% endif %}{{ '' -}}
🕒 {{ timestamp }}""",
    # ── Feature 7: Status Message ──
    "status_on": """\
🟢 {{ short_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

✅ Електропостачання: наявне
{% if voltage is not none or phases %}
{% if phases %}
{{ phases | format_phase_summary }}
{% else %}
🔌 Напруга: {{ voltage | round(1) }} В
{% endif %}
{% endif %}
{% if next_outage %}
📅 Найближче планове:
{{ next_outage }}
{% endif %}
🕒 Оновлено: {{ timestamp }}""",
    "status_unknown": """\
⚪️ {{ short_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

❔ Стан живлення невідомий: немає актуальних вимірювань.
{% if next_outage %}
📅 Найближче планове:
{{ next_outage }}
{% endif %}
🕒 Оновлено: {{ timestamp }}""",
    "status_partial": """\
🟠 {{ short_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

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
📅 Найближче планове:
{{ next_outage }}
{% endif %}
🕒 Оновлено: {{ timestamp }}""",
    "status_off": """\
🔴 {{ short_name }}
🔵 {% if groups_public|default(true) %}Група {% endif %}{{ group }}

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
