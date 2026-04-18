import React from "react";

export default function Refund() {
  return (
    <div className="px-6 md:px-12 lg:px-20 py-12 max-w-4xl" data-testid="refund-page">
      <div className="te-overline mb-2">Legal</div>
      <h1 className="text-4xl md:text-5xl font-black tracking-tight">Refund & Cancellation Policy</h1>
      <div className="te-overline mt-2">Last updated · April 2026</div>

      <div className="mt-8 space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-black mt-6">1. Cancellation by the passenger</h2>
          <p>You may cancel your booking at any time before departure via your dashboard or by contacting us. Refunds are issued on the following sliding scale:</p>

          <div className="mt-4 te-card overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-zinc-100">
                <tr>
                  <th className="text-left p-3 font-black uppercase tracking-wider text-xs">Time before departure</th>
                  <th className="text-left p-3 font-black uppercase tracking-wider text-xs">Refund</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-t border-black/10">
                  <td className="p-3">More than 48 hours</td>
                  <td className="p-3 font-mono font-bold text-emerald-700">100% refund</td>
                </tr>
                <tr className="border-t border-black/10">
                  <td className="p-3">24–48 hours</td>
                  <td className="p-3 font-mono font-bold">75% refund</td>
                </tr>
                <tr className="border-t border-black/10">
                  <td className="p-3">4–24 hours</td>
                  <td className="p-3 font-mono font-bold">50% refund</td>
                </tr>
                <tr className="border-t border-black/10">
                  <td className="p-3">Less than 4 hours / no-show</td>
                  <td className="p-3 font-mono font-bold text-red-700">No refund</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">2. Cancellation by Star Qistna</h2>
          <p>If we cancel your trip or delay it by more than 60 minutes, you are entitled to:</p>
          <ul className="list-disc pl-6 space-y-1">
            <li>A <b>full refund</b> (100%) to the original payment method, OR</li>
            <li>A complimentary rebooking to the next available schedule of the same route at no additional cost.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">3. Refund processing</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Refunds are issued to the same payment method used at checkout (Stripe card / iPay88).</li>
            <li>Processing time: <b>5–10 business days</b> for card refunds, depending on your bank.</li>
            <li>Promo discounts applied are non-refundable (refund amount is calculated on the fare paid after discount).</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">4. How to request</h2>
          <p>Log in and open the booking from your dashboard, then click "Cancel booking" — refund is processed automatically. If you booked as a guest, forward your booking confirmation email to <a className="underline font-bold" href="mailto:refunds@starqistna.com">refunds@starqistna.com</a> with a short reason.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">5. Non-refundable circumstances</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Passenger fails to present a valid ID at boarding.</li>
            <li>Passenger is denied boarding due to conduct (see Terms §7).</li>
            <li>No-shows beyond the departure time.</li>
            <li>Delays caused by force majeure (weather, border controls, traffic incidents).</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">6. Disputes</h2>
          <p>If you disagree with a refund decision, reply within 14 days of the decision and we'll escalate to a manager. Final unresolved disputes are governed by the laws of Malaysia.</p>
        </section>
      </div>
    </div>
  );
}
