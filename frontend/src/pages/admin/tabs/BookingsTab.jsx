import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../../../lib/api";

export default function BookingsTab() {
  const [bookings, setBookings] = useState([]);
  const navigate = useNavigate();

  useEffect(() => {
    api.get("/admin/bookings").then(({ data }) => setBookings(data)).catch(() => {});
  }, []);

  const handleBookingClick = (bookingId) => {
    navigate(`/bookings/${bookingId}`);
  };

  return (
    <div className="mt-6 space-y-2">
      {bookings.map((b) => (
        <div key={b.id} className="te-card p-4 grid grid-cols-6 gap-3 text-sm items-center">
          <button
            onClick={() => handleBookingClick(b.id)}
            className="font-mono font-bold text-blue-600 hover:text-blue-800 hover:underline cursor-pointer text-left"
          >
            {b.reference}
          </button>
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
