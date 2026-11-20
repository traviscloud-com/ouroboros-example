import type { DateRange } from "react-day-picker";
import { Calendar } from "@/components/ui/calendar";

/** Lazily loaded by the Filterbar so react-day-picker + date-fns stay out of the initial chunk. */
export default function RangeCalendar({
  selected,
  onSelect,
}: {
  selected: DateRange;
  onSelect: (r: DateRange | undefined) => void;
}) {
  return (
    <Calendar
      mode="range"
      numberOfMonths={2}
      defaultMonth={selected.from}
      selected={selected}
      onSelect={onSelect}
      disabled={{ after: new Date() }}
    />
  );
}
