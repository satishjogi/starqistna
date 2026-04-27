import React, { useEffect, useState } from "react";
import api from "../lib/api";
import { useAuth } from "../lib/auth";
import { useNavigate } from "react-router-dom";
import BookingsTab from "./admin/tabs/BookingsTab";
import PaymentsTab from "./admin/tabs/PaymentsTab";
import SchedulesTab from "./admin/tabs/SchedulesTab";
import AddScheduleTab from "./admin/tabs/AddScheduleTab";
import TerminalsTab from "./admin/tabs/TerminalsTab";
import PromoCodesTab from "./admin/tabs/PromoCodesTab";
import FeedbackTab from "./admin/tabs/FeedbackTab";
import AuditLogTab from "./admin/tabs/AuditLogTab";
import AdminsTab from "./admin/tabs/AdminsTab";

const TABS = [
  "bookings", "payments", "schedules", "add-schedule",
  "terminals", "promo-codes", "feedback", "audit-log", "admins",
];

export default function Admin() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState(null);
  const [tab, setTab] = useState("bookings");

  useEffect(() => {
    if (!loading && (!user || !user.is_admin)) navigate("/");
  }, [user, loading, navigate]);

  useEffect(() => {
    if (user?.is_admin) {
      api.get("/admin/stats").then(({ data }) => setStats(data)).catch(() => {});
    }
  }, [user]);

  if (!user?.is_admin) return null;

  return (
    <div className="px-4 md:px-6 lg:px-10 py-10">
      <div className="te-overline mb-2">Control · Admin</div>
      <h1 className="text-4xl font-black tracking-tight">Operations</h1>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-8">
        {stats && [
          ["Users", stats.users],
          ["Bookings", stats.bookings],
          ["Confirmed", stats.confirmed_bookings],
          ["Terminals", stats.terminals],
          ["Schedules", stats.schedules],
        ].map(([k, v]) => (
          <div key={k} className="te-card p-5">
            <div className="te-overline text-[10px]">{k}</div>
            <div className="font-mono text-3xl font-black">{v}</div>
          </div>
        ))}
      </div>

      <div className="mt-10 flex gap-1 border-b border-black/10 flex-wrap">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-xs font-bold uppercase tracking-wider ${tab === t ? "bg-black text-white" : "text-zinc-500"}`}
            data-testid={`admin-tab-${t}`}
          >
            {t.replace(/-/g, " ")}
          </button>
        ))}
      </div>

      {tab === "bookings" && <BookingsTab />}
      {tab === "payments" && <PaymentsTab />}
      {tab === "schedules" && <SchedulesTab />}
      {tab === "add-schedule" && <AddScheduleTab />}
      {tab === "terminals" && <TerminalsTab />}
      {tab === "promo-codes" && <PromoCodesTab />}
      {tab === "feedback" && <FeedbackTab />}
      {tab === "audit-log" && <AuditLogTab />}
      {tab === "admins" && <AdminsTab user={user} />}
    </div>
  );
}
