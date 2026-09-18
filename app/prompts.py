SYSTEM_PROMPT = """You interpret synthetic campus operator notes for a 24-hour energy schedule.

Return exactly one interpretation for every note, in note_index order. Each note maps to
exactly one of: solar_reduction, minimum_battery_reserve, no_charge_window,
no_discharge_window, max_grid_window, or no_op.

Rules:
- A note is no_op when it does not change the current 24-hour schedule, including energy-themed
  statements explicitly about yesterday, tomorrow, next month, or general advice.
- Time windows are whole-hour, start-inclusive and end-exclusive. 1 PM until 3 PM is [13,14].
- A window crossing midnight is returned in ascending order: 10 PM until 2 AM is [0,1,22,23].
- "All day" is every hour 0 through 23. Applicable directives never have an empty hours list.
- Hours must be unique integers from 0 through 23 in ascending order.
- solar_reduction.factor is the usable fraction remaining. Reduced by 80% and reduced to 20%
  both mean factor 0.2.
- Convert a percentage battery reserve to kWh using the supplied battery capacity.
- Do not invent demand, tariff, battery values, hours, unsupported rules, or a second directive
  for one note.
- Apply a directive only when the note provides reasonable semantic evidence. Do not invent a
  directive merely because missing a real rule might be costly.
- Use null for fields that do not belong to the selected directive.
- Keep explanations short and do not expose private reasoning.
"""


def user_prompt(notes: list[str], capacity_kwh: float, feedback: str | None = None) -> str:
    lines = [
        f"Battery capacity: {capacity_kwh} kWh",
        "Interpret these notes for the current 24-hour schedule:",
    ]
    lines.extend(f"[{index}] {note}" for index, note in enumerate(notes))
    if feedback:
        lines.extend(
            [
                "",
                "The previous interpretation failed deterministic validation or produced an "
                "infeasible schedule. Re-read the notes; do not weaken physics or invent values.",
                f"Diagnostic: {feedback}",
            ]
        )
    return "\n".join(lines)
