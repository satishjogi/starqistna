import React from "react";

export default function Privacy() {
  return (
    <div className="px-4 md:px-6 lg:px-10 py-12 max-w-4xl" data-testid="privacy-page">
      <div className="te-overline mb-2">Legal</div>
      <h1 className="text-4xl md:text-5xl font-black tracking-tight">Privacy Policy</h1>
      <div className="te-overline mt-2">Last updated · April 2026</div>

      <div className="mt-8 space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-black mt-6">1. Who we are</h2>
          <p>Qistna Express ("we", "us") is the data controller for personal information collected via starqistna.com. We comply with the Malaysian Personal Data Protection Act 2010 (PDPA) and Singapore's Personal Data Protection Act 2012 where applicable.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">2. Information we collect</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li><b>Account data</b>: name, email, phone number, password (stored hashed).</li>
            <li><b>Booking data</b>: passenger names, IC/passport number (if provided), contact details, boarding history.</li>
            <li><b>Payment data</b>: handled entirely by our payment processor (Stripe — Card, GrabPay, FPX). We store only the transaction ID and status, never card numbers or wallet credentials.</li>
            <li><b>Technical data</b>: IP address, browser type, device, and cookie identifiers for security and service analytics.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">3. Why we use it</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>To process your booking and issue tickets.</li>
            <li>To contact you about your trip (confirmations, delays, schedule changes).</li>
            <li>To validate boarding via QR code at the gate.</li>
            <li>To comply with legal, tax, and anti-fraud obligations.</li>
            <li>With your consent, to send promotional offers (opt-out anytime).</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">4. Who we share with</h2>
          <p>We only share personal data with:</p>
          <ul className="list-disc pl-6 space-y-1">
            <li>Payment processor (Stripe — Card, GrabPay, FPX) — strictly to settle payments.</li>
            <li>Email delivery providers — to send confirmations and tickets.</li>
            <li>Regulatory authorities — where legally required.</li>
          </ul>
          <p>We do <b>not</b> sell or rent your personal data to third parties.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">5. Retention</h2>
          <p>Account information is retained while your account is active. Booking records are retained for 7 years to meet tax and accounting obligations. Upon deletion, we anonymise rather than hard-delete booking history.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">6. Your rights</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Access — request a copy of the data we hold about you.</li>
            <li>Correction — ask us to fix inaccurate data.</li>
            <li>Deletion — request erasure (subject to legal retention).</li>
            <li>Withdraw consent — opt out of marketing emails at any time.</li>
          </ul>
          <p>Write to <a className="underline font-bold" href="mailto:privacy@starqistna.com">privacy@starqistna.com</a> to exercise these rights.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">7. Security</h2>
          <p>We use HTTPS on every page, hash passwords with industry-standard algorithms, and enforce role-based access control on administrative functions. Admin accounts are protected with two-factor authentication.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">8. Cookies</h2>
          <p>We use a small number of essential cookies to keep you logged in and secure your session. No third-party advertising cookies are used.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">9. Contact</h2>
          <p>Questions? Reach our Data Protection Officer at <a className="underline font-bold" href="mailto:privacy@starqistna.com">privacy@starqistna.com</a>.</p>
        </section>
      </div>
    </div>
  );
}
