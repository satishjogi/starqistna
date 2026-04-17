import React from "react";

export default function Footer() {
  return (
    <footer className="border-t border-black/10 mt-24 bg-white">
      <div className="px-6 md:px-12 lg:px-20 py-12 grid grid-cols-1 md:grid-cols-4 gap-8">
        <div className="md:col-span-2">
          <div className="font-black text-2xl tracking-tight">TRANSIT/E1</div>
          <p className="text-sm text-zinc-600 mt-3 max-w-md">
            A transit authority for buses. Book inter-city rides across Malaysia & Singapore with confirmed seats, transparent pricing, and zero drama.
          </p>
        </div>
        <div>
          <div className="te-overline mb-3">Product</div>
          <ul className="space-y-2 text-sm">
            <li>Routes</li>
            <li>Operators</li>
            <li>API Access</li>
          </ul>
        </div>
        <div>
          <div className="te-overline mb-3">Support</div>
          <ul className="space-y-2 text-sm">
            <li>Help Center</li>
            <li>Refunds</li>
            <li>Contact</li>
          </ul>
        </div>
      </div>
      <div className="border-t border-black/10 px-6 md:px-12 lg:px-20 py-5 flex flex-col sm:flex-row justify-between text-xs font-mono text-zinc-500">
        <div>© {new Date().getFullYear()} TRANSIT/E1 — ALL RIGHTS RESERVED</div>
        <div>PAYMENTS BY STRIPE · IPAY88 (COMING SOON)</div>
      </div>
    </footer>
  );
}
