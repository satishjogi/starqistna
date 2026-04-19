import React from "react";

export default function Terms() {
  return (
    <div className="px-4 md:px-6 lg:px-10 py-12 max-w-4xl" data-testid="terms-page">
      <div className="te-overline mb-2">Legal</div>
      <h1 className="text-4xl md:text-5xl font-black tracking-tight">Terms of Service</h1>
      <div className="te-overline mt-2">Last updated · April 2026</div>

      <div className="prose prose-zinc max-w-none mt-8 space-y-6 text-sm leading-relaxed">
        <section>
          <h2 className="text-xl font-black mt-6">1. Acceptance</h2>
          <p>By booking a ticket through Star Qistna ("we", "us", "the Company") at starqistna.com, you ("the Passenger") agree to be bound by these Terms. If you do not agree, do not proceed with the booking.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">2. The Service</h2>
          <p>Star Qistna provides inter-city coach booking services in Malaysia and Singapore. We act as an operator of coach transportation. Schedules, routes, and fares are subject to availability and may be amended without prior notice.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">3. Bookings & Payments</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>All fares are quoted inclusive of applicable taxes in the currency of the boarding terminal (MYR for Malaysian boardings, SGD for Singapore boardings).</li>
            <li>Tickets are confirmed only after successful payment. Seat locks during checkout expire after 10 minutes if payment is not completed.</li>
            <li>Child fare applies to passengers aged 3–11 years. Infants under 3 travel free on an adult's lap (no separate seat).</li>
            <li>Your booking reference and QR code constitute your ticket and must be presented at the boarding gate.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">4. Boarding</h2>
          <ul className="list-disc pl-6 space-y-1">
            <li>Passengers must arrive at the boarding terminal at least 30 minutes before departure.</li>
            <li>The coach will not wait for late passengers. No refund will be issued for missed departures.</li>
            <li>A valid photo ID matching the passenger name may be checked at boarding.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">5. Baggage</h2>
          <p>Each adult passenger is allowed one hand carry (max 7 kg) and one check-in luggage (max 20 kg). Additional baggage is subject to availability and may incur fees. Dangerous, illegal, or perishable goods are prohibited.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">6. Delays & Cancellations by Us</h2>
          <p>If we cancel or substantially delay a trip (more than 60 minutes), we will notify you via email/SMS and you are entitled to a full refund or rebooking. We are not liable for delays caused by traffic, weather, border controls, or events outside our reasonable control.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">7. Passenger Conduct</h2>
          <p>We reserve the right to refuse boarding or remove any passenger whose behaviour endangers, distresses, or inconveniences other passengers or crew. No refund in such cases.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">8. Limitation of Liability</h2>
          <p>To the maximum extent permitted by law, our liability in relation to any booking is limited to the total fare paid. We are not liable for indirect or consequential losses.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">9. Governing Law</h2>
          <p>These Terms are governed by the laws of Malaysia. Any disputes shall be submitted to the exclusive jurisdiction of the courts of Kuala Lumpur.</p>
        </section>

        <section>
          <h2 className="text-xl font-black mt-6">10. Contact</h2>
          <p>For any questions about these Terms, write to us at <a className="underline font-bold" href="mailto:support@starqistna.com">support@starqistna.com</a>.</p>
        </section>
      </div>
    </div>
  );
}
