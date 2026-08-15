import React from "react";

export default function Footer() {
  return (
    <footer className="border-t border-black/10 mt-24 bg-white">
      <div className="px-4 md:px-6 lg:px-10 py-12 grid grid-cols-1 md:grid-cols-4 gap-8">
        <div className="md:col-span-2">
          <img src="/logo.png" alt="Qistna Express" className="h-14 w-auto" />
          <p className="text-sm text-zinc-600 mt-4 max-w-md">
            First class massage coach service across Malaysia & Singapore. Book inter-city rides with confirmed seats, transparent pricing, and zero drama.
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
            <li><a href="/terms" className="hover:underline">Terms of Service</a></li>
            <li><a href="/privacy" className="hover:underline">Privacy Policy</a></li>
            <li><a href="/refund" className="hover:underline">Refund Policy</a></li>
            <li><a href="mailto:support@starqistna.com" className="hover:underline">Contact</a></li>
          </ul>
        </div>
      </div>
      <div className="border-t border-black/10 px-4 md:px-6 lg:px-10 py-5 flex flex-col sm:flex-row justify-between text-xs font-mono text-zinc-500">
        <div>© {new Date().getFullYear()} QISTNA EXPRESS PVT LTD · ALL RIGHTS RESERVED</div>
        <div>PAYMENTS BY STRIPE · CARD · GRABPAY · FPX</div>
      </div>
    </footer>
  );
}
