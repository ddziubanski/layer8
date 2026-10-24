// SPDX-License-Identifier: AGPL-3.0-or-later
/** A schedule's timetable (report and script schedules share it): once, daily, weekly on days, monthly on a day, at a time. */

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export type Frequency = "once" | "daily" | "weekly" | "monthly";

export type TimetableValue = { frequency: Frequency; weekdays: number[]; day_of_month: number | null; time_of_day: string; start_date: string };

export const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const FREQ: Record<string, string> = { once: "Once", daily: "Every day", weekly: "Every week", monthly: "Every month" };

export function describeTimetable(s: { frequency: string; weekdays: number[]; day_of_month: number | null; time_of_day: string; start_date: string }): string {
  const at = `at ${s.time_of_day}`;
  if (s.frequency === "weekly") return `Every ${s.weekdays.map((d) => WEEKDAYS[d]).join(", ")} ${at}`;
  if (s.frequency === "monthly") return `Every month on ${s.day_of_month === -1 ? "the last day" : `day ${s.day_of_month}`} ${at}`;
  if (s.frequency === "once") return `Once, ${s.start_date} ${at}`;
  return `${FREQ[s.frequency]} ${at}`;
}

export function Timetable({ value, onChange }: { value: TimetableValue; onChange: (v: TimetableValue) => void }) {
  const set = (patch: Partial<TimetableValue>) => onChange({ ...value, ...patch });
  return (
    <>
      <label className="field">
        Repeat
        <select value={value.frequency} onChange={(e) => set({ frequency: e.target.value as Frequency, day_of_month: e.target.value === "monthly" ? (value.day_of_month ?? 1) : value.day_of_month })}>
          <option value="once">Once</option>
          <option value="daily">Every day</option>
          <option value="weekly">Every week</option>
          <option value="monthly">Every month</option>
        </select>
      </label>
      {value.frequency === "weekly" && (
        <fieldset className="filter-box">
          <legend>On</legend>
          <div className="filter-choices">
            {WEEKDAYS.map((d, i) => (
              <label key={d} className="inline small">
                <input
                  type="checkbox"
                  checked={value.weekdays.includes(i)}
                  onChange={(e) => set({ weekdays: e.target.checked ? [...value.weekdays, i].sort() : value.weekdays.filter((x) => x !== i) })}
                />
                {d}
              </label>
            ))}
          </div>
        </fieldset>
      )}
      {value.frequency === "monthly" && (
        <label className="field">
          On day
          <select value={value.day_of_month ?? 1} onChange={(e) => set({ day_of_month: Number(e.target.value) })}>
            {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
            <option value={-1}>Last day of the month</option>
          </select>
        </label>
      )}
      <div className="row-gap">
        <label className="field spacer">
          {value.frequency === "once" ? "On" : "Starting"}
          <input type="date" required value={value.start_date} onChange={(e) => set({ start_date: e.target.value })} />
        </label>
        <label className="field spacer">
          At
          <input type="time" required value={value.time_of_day} onChange={(e) => set({ time_of_day: e.target.value })} />
        </label>
      </div>
    </>
  );
}
