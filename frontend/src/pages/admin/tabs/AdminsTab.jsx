import React, { useEffect, useState } from "react";
import api from "../../../lib/api";

export default function AdminsTab({ user }) {
  const [admins, setAdmins] = useState({ admins: [], pending_invites: [] });
  const [form, setForm] = useState({ email: "", full_name: "", role: "admin" });
  const [msg, setMsg] = useState("");
  const isSuperAdmin = user?.role === "super_admin";

  const load = () => {
    api.get("/admin/admins").then(({ data }) => setAdmins(data)).catch(() => {});
  };

  useEffect(() => { load(); }, []);

  const sendInvite = async (e) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/admin/admins/invite", form);
      setMsg(`Invite sent to ${form.email}. They have 7 days to accept.`);
      setForm({ email: "", full_name: "", role: "admin" });
      load();
    } catch (err) {
      setMsg(`Error: ${err?.response?.data?.detail || "could not send invite"}`);
    }
  };

  const cancelInvite = async (inv) => {
    if (!window.confirm(`Cancel invite for ${inv.email}?`)) return;
    try {
      await api.delete(`/admin/admins/invites/${inv.id}`);
      load();
    } catch (err) {
      alert(err?.response?.data?.detail || "Could not cancel");
    }
  };

  const toggleRole = async (a) => {
    const isSuper = a.role === "super_admin";
    const newRole = isSuper ? "admin" : "super_admin";
    if (!window.confirm(`Change ${a.email} to ${newRole}?`)) return;
    try {
      await api.patch(`/admin/admins/${a.id}/role`, { role: newRole });
      load();
    } catch (err) { alert(err?.response?.data?.detail || "failed"); }
  };

  const toggleActive = async (a) => {
    const next = a.is_active === false;
    if (!window.confirm(`${next ? "Reactivate" : "Deactivate"} ${a.email}?`)) return;
    try {
      await api.patch(`/admin/admins/${a.id}/active`, { is_active: next });
      load();
    } catch (err) { alert(err?.response?.data?.detail || "failed"); }
  };

  const revoke = async (a) => {
    if (!window.confirm(`Revoke admin access for ${a.email}? They'll still be able to log in as a normal user.`)) return;
    try {
      await api.delete(`/admin/admins/${a.id}`);
      load();
    } catch (err) { alert(err?.response?.data?.detail || "failed"); }
  };

  return (
    <div className="mt-6 space-y-6" data-testid="admins-section">
      {isSuperAdmin ? (
        <form onSubmit={sendInvite} className="te-card p-6 space-y-4" data-testid="admin-invite-form">
          <div className="te-overline">Invite a new admin</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="te-label">Full name</label>
              <input
                required
                className="te-input"
                value={form.full_name}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
                data-testid="admin-invite-name"
              />
            </div>
            <div>
              <label className="te-label">Email</label>
              <input
                type="email"
                required
                className="te-input"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                data-testid="admin-invite-email"
              />
            </div>
            <div>
              <label className="te-label">Role</label>
              <select
                className="te-input"
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value })}
                data-testid="admin-invite-role"
              >
                <option value="admin">Admin</option>
                <option value="super_admin">Super-admin</option>
              </select>
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div className="text-[10px] font-mono text-zinc-500 leading-relaxed">
              INVITE SENT VIA EMAIL · EXPIRES IN 7 DAYS · RECIPIENT SETS OWN PASSWORD
            </div>
            <button className="te-btn-primary" data-testid="admin-invite-submit">
              Send invite
            </button>
          </div>
          {msg && <div className="text-xs font-bold" data-testid="admin-invite-msg">{msg}</div>}
        </form>
      ) : (
        <div className="te-card p-4 text-xs text-zinc-600" data-testid="admin-non-super-notice">
          Only super-admins can invite or manage other admins. You can still view the list below.
        </div>
      )}

      {admins.pending_invites?.length > 0 && (
        <div className="te-card p-0 overflow-hidden">
          <div className="px-4 py-3 bg-amber-50 border-b border-amber-200 text-[10px] font-mono uppercase tracking-wider text-amber-700">
            Pending invites ({admins.pending_invites.length})
          </div>
          {admins.pending_invites.map((inv) => (
            <div key={inv.id} className="flex items-center justify-between px-4 py-3 border-t border-black/5 text-xs">
              <div>
                <div className="font-bold">{inv.full_name} <span className="text-zinc-500">· {inv.email}</span></div>
                <div className="font-mono text-[10px] text-zinc-500">
                  Role: {inv.role} · Invited by {inv.invited_by_email} · Expires {new Date(inv.expires_at).toLocaleDateString()}
                </div>
              </div>
              {isSuperAdmin && (
                <button
                  type="button"
                  onClick={() => cancelInvite(inv)}
                  className="text-[10px] font-mono font-bold text-red-600 hover:text-red-800 uppercase tracking-wider"
                  data-testid={`admin-cancel-invite-${inv.id}`}
                >
                  Cancel
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="te-card p-0 overflow-hidden">
        <div className="grid grid-cols-12 gap-2 px-4 py-3 bg-black text-white text-[10px] font-mono uppercase tracking-wider">
          <div className="col-span-3">Name</div>
          <div className="col-span-3">Email</div>
          <div className="col-span-2">Role</div>
          <div className="col-span-2">Last login</div>
          <div className="col-span-2 text-right">Actions</div>
        </div>
        {admins.admins?.length === 0 && (
          <div className="p-6 text-sm text-zinc-500" data-testid="admin-empty">
            No admins yet.
          </div>
        )}
        {admins.admins?.map((a) => {
          const isSelf = a.id === user?.id;
          const isSuper = a.role === "super_admin";
          return (
            <div
              key={a.id}
              className={`grid grid-cols-12 gap-2 px-4 py-3 border-t border-black/5 text-xs items-center ${a.is_active === false ? "opacity-50" : ""}`}
              data-testid={`admin-row-${a.id}`}
            >
              <div className="col-span-3 font-bold truncate">
                {a.full_name || "—"}
                {isSelf && <span className="ml-2 text-[9px] font-mono text-zinc-400">YOU</span>}
              </div>
              <div className="col-span-3 font-mono text-[11px] truncate">{a.email}</div>
              <div className="col-span-2">
                <span className={`inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${isSuper ? "bg-[#002FA7] text-white" : "bg-zinc-200 text-zinc-800"}`}>
                  {isSuper ? "Super" : "Admin"}
                </span>
                {a.is_active === false && (
                  <span className="ml-1 inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-zinc-900 text-white">
                    Off
                  </span>
                )}
              </div>
              <div className="col-span-2 font-mono text-[10px] text-zinc-500">
                {a.last_login_at ? new Date(a.last_login_at).toLocaleString() : "Never"}
              </div>
              <div className="col-span-2 text-right flex gap-2 justify-end">
                {isSuperAdmin && !isSelf && (
                  <>
                    <button
                      type="button"
                      onClick={() => toggleRole(a)}
                      className="text-[10px] font-mono font-bold uppercase tracking-wider text-[#002FA7] hover:underline"
                      data-testid={`admin-toggle-role-${a.id}`}
                    >
                      {isSuper ? "Demote" : "Promote"}
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleActive(a)}
                      className="text-[10px] font-mono font-bold uppercase tracking-wider text-zinc-700 hover:underline"
                      data-testid={`admin-toggle-active-${a.id}`}
                    >
                      {a.is_active === false ? "Reactivate" : "Deactivate"}
                    </button>
                    <button
                      type="button"
                      onClick={() => revoke(a)}
                      className="text-[10px] font-mono font-bold uppercase tracking-wider text-red-600 hover:underline"
                      data-testid={`admin-revoke-${a.id}`}
                    >
                      Revoke
                    </button>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
