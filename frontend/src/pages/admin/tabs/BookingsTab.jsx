import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function BookingsTab() {
  const [bookings, setBookings] = useState([]);

  useEffect(() => {
    api.get("/admin/bookings").then(({ data }) => setBookings(data)).catch(() => {});
  }, []);

  return (
    <div className="mt-6 space-y-2">
      {bookings.map((b) => (
        <div key={b.id} className="te-card p-4 grid grid-cols-6 gap-3 text-sm items-center">
          <div className="font-mono font-bold">{b.reference}</div>
          <div>{b.contact_email}</div>
          <div className="font-mono">{b.departure_date} {b.departure_time}</div>
          <div>Seats: {b.seats.join(",")}</div>
          <div className="font-mono">{b.pricing.currency.toUpperCase()} {b.pricing.total.toFixed(2)}</div>
          <div className="text-[10px] uppercase font-mono font-bold">{b.status}</div>
        </div>
      ))}
    </div>
  );
}
