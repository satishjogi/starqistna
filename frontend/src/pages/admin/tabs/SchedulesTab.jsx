import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function SchedulesTab() {
  const [schedules, setSchedules] = useState([]);

  useEffect(() => {
    api.get("/admin/schedules").then(({ data }) => setSchedules(data)).catch(() => {});
  }, []);

  return (
    <div className="mt-6 space-y-2 max-h-[600px] overflow-auto">
      {schedules.map((s) => (
        <div key={s.id} className="te-card p-4 grid grid-cols-6 gap-3 text-sm items-center">
          <div className="font-mono">{s.departure_date}</div>
          <div className="font-mono">{s.departure_time} → {s.arrival_time}</div>
          <div>{s.bus_operator}</div>
          <div className="text-xs">{s.bus_type}</div>
          <div className="font-mono">{s.currency.toUpperCase()} {s.adult_fare.toFixed(2)}</div>
          <div className="font-mono">{s.total_seats} seats</div>
        </div>
      ))}
    </div>
  );
}
