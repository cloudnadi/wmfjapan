import { useState } from "react";
import Navbar from "./Navbar";

const HOME_PATH = "/";
const MY_SEAT_UPGRADE_PATH = "/my/seat-upgrade";
const CELEBRITY_DINNER_SIGNUP_PATH = "/celebrity-dinner";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const SEAT_SELECTION_GUIDE_PATH = "/seat-selection-guide";
const ZONE_UPGRADE_GUIDE_PATH = "/zone-upgrade-guide";

const UPGRADE_STEPS = [
  {
    title: "Login",
    body: (
      <>
        Login to <strong>My Registrations</strong> using your <strong>registered email address</strong>.
      </>
    ),
  },
  {
    title: "Choose a Zone",
    body: (
      <>
        Open <strong>Upgrade My Zone</strong> on a paid registration and pick how many seats you'd
        like in each premium zone.
      </>
    ),
  },
  {
    title: "Pay & Confirm",
    body: (
      <>
        Pay by PayPay, credit card, or bank transfer. You'll get a confirmation email once your
        upgrade is paid.
      </>
    ),
  },
];

const DINNER_STEPS = [
  {
    title: "Login",
    body: (
      <>
        Login using your <strong>registered email address</strong> — either from{" "}
        <strong>My Registrations</strong> or directly at the Dinner with Celebrity sign-up page.
      </>
    ),
  },
  {
    title: "Add Your Guests",
    body: (
      <>
        Enter how many <strong>Veg</strong> and <strong>Non-Veg</strong> guests you're bringing in
        each age category. Kids under 6 are always free — just count them in.
      </>
    ),
  },
  {
    title: "Pay & Confirm",
    body: (
      <>
        Pay by PayPay, credit card, or bank transfer. You'll get a confirmation email with your
        booking details.
      </>
    ),
  },
];

const UPGRADE_REMINDERS = [
  <>Zone Upgrade is available to <strong>paid Celebrity Event / Combo registrants</strong>.</>,
  <>Pricing varies by zone — see the current prices on the Upgrade My Zone page before you pay.</>,
  <>
    A <strong>family discount</strong> may apply once your household's first few seats are
    upgraded at full price — the exact terms are shown at checkout.
  </>,
  <>
    Dinner with Celebrity can be bundled into the same upgrade, <strong>or</strong> booked
    separately — not both for the same registration.
  </>,
  <>Cancellation and refund availability is set per event — check your booking for the current window.</>,
  <>Support WMF Japan by transparently sharing the programme expenses and working towards a sustainable model.</>,
];

const DINNER_REMINDERS = [
  <>Dinner with Celebrity pricing is by age category and never depends on veg vs. non-veg.</>,
  <><strong>Kids under 6 are never charged</strong> — they're counted for catering only.</>,
  <>You can book Dinner with Celebrity on its own, or bundled into a Zone Upgrade — not both.</>,
  <>A confirmation email is sent for every booking, and again once payment is received.</>,
  <>Cancellation and refund availability is set per event — check your booking for the current window.</>,
];

const FAQS = [
  {
    q: "Do I need to upgrade my zone to book the Dinner with Celebrity?",
    a: "No — the two are independent. You can book the Dinner with Celebrity on its own from the Dinner with Celebrity page, or bundle it into a Zone Upgrade at a discount. You can't do both for the same registration.",
  },
  {
    q: "Is Zone Upgrade available to everyone?",
    a: "It's offered to paid Celebrity Event and Combo registrants once the organizers open it for your event.",
  },
  {
    q: "Do kids under 6 need to be counted for the dinner?",
    a: "Yes — they're never charged, but please still add them so the caterer has an accurate headcount.",
  },
  {
    q: "Can I change my guest count or zone after paying?",
    a: "Once a booking is paid, guest counts and zone picks are locked — cancel and rebook instead, subject to the event's cancellation window.",
  },
  {
    q: "How do I cancel or request a refund?",
    a: "Open the booking under My Registrations (or the Dinner with Celebrity page) and use the Cancel button — it's only shown while cancellation is open for that event.",
  },
];

function FaqItem({ question, answer }) {
  const [open, setOpen] = useState(false);

  return (
    <div className={`slot-guide-faq-item${open ? " open" : ""}`}>
      <button
        type="button"
        className="slot-guide-faq-question"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        {question}
        <span className="slot-guide-faq-arrow" aria-hidden="true">
          ▼
        </span>
      </button>
      {open ? <div className="slot-guide-faq-answer">{answer}</div> : null}
    </div>
  );
}

/**
 * Public guide to the two paid add-ons available to registrants: the Zone
 * Upgrade (premium seating) and the Dinner with Celebrity. Mirrors
 * SeatSelectionGuide.jsx/SadhyaSlotGuide.jsx's structure and styling
 * (same .slot-guide-* classes) but covers both features on one page since
 * they're announced and offered together.
 */
export default function ZoneUpgradeGuide({ navigateTo }) {
  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />

      <main className="slot-guide">
        <header className="slot-guide-header">
          <h1>Zone Upgrade &amp; Dinner with Celebrity Guide</h1>
          <p>
            Two optional ways to make the evening more special — upgrade into premium seating,
            and/or join us for the Dinner with Celebrity.
          </p>
        </header>

        <div className="slot-guide-alert">
          <strong>Note:</strong> Both are optional paid add-ons for confirmed Celebrity Event /
          Combo registrants. Availability, pricing, and the registration window are set by the
          organizers per event — what you see on the booking pages is always current.
        </div>

        <section className="slot-guide-section" aria-labelledby="slot-guide-upgrade-heading">
          <h2 id="slot-guide-upgrade-heading" className="slot-guide-section-title">
            Zone Upgrade — How It Works
          </h2>
          <div className="slot-guide-steps">
            {UPGRADE_STEPS.map((step, index) => (
              <div key={step.title} className="slot-guide-step">
                <div className="slot-guide-step-number">{index + 1}</div>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section
          className="slot-guide-section slot-guide-card"
          aria-labelledby="slot-guide-upgrade-remember-heading"
        >
          <h2 id="slot-guide-upgrade-remember-heading" className="slot-guide-section-title">
            Zone Upgrade — Please Remember
          </h2>
          <ul className="slot-guide-checklist">
            {UPGRADE_REMINDERS.map((reminder, index) => (
              <li key={index}>{reminder}</li>
            ))}
          </ul>
        </section>

        <section className="slot-guide-section" aria-labelledby="slot-guide-dinner-heading">
          <h2 id="slot-guide-dinner-heading" className="slot-guide-section-title">
            Dinner with Celebrity — How It Works
          </h2>
          <div className="slot-guide-steps">
            {DINNER_STEPS.map((step, index) => (
              <div key={step.title} className="slot-guide-step">
                <div className="slot-guide-step-number">{index + 1}</div>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </div>
            ))}
          </div>
        </section>

        <section
          className="slot-guide-section slot-guide-card"
          aria-labelledby="slot-guide-dinner-remember-heading"
        >
          <h2 id="slot-guide-dinner-remember-heading" className="slot-guide-section-title">
            Dinner with Celebrity — Please Remember
          </h2>
          <ul className="slot-guide-checklist">
            {DINNER_REMINDERS.map((reminder, index) => (
              <li key={index}>{reminder}</li>
            ))}
          </ul>
        </section>

        <section
          className="slot-guide-section slot-guide-cta"
          aria-labelledby="slot-guide-cta-heading"
        >
          <h2 id="slot-guide-cta-heading" className="slot-guide-section-title">
            Ready to Book?
          </h2>
          <p className="slot-guide-centered">
            Login to My Registrations to upgrade your zone or book the Dinner with Celebrity.
          </p>
          <div className="btn-row" style={{ justifyContent: "center", marginTop: "14px" }}>
            <button
              type="button"
              className="landing-hero-cta"
              onClick={() => navigateTo(MY_SEAT_UPGRADE_PATH)}
            >
              Upgrade My Zone
            </button>
            <button
              type="button"
              className="landing-hero-cta"
              onClick={() => navigateTo(CELEBRITY_DINNER_SIGNUP_PATH)}
            >
              Book Dinner with Celebrity
            </button>
          </div>
        </section>

        <section className="slot-guide-section" aria-labelledby="slot-guide-faq-heading">
          <h2 id="slot-guide-faq-heading" className="slot-guide-section-title">
            Frequently Asked Questions
          </h2>
          {FAQS.map((faq) => (
            <FaqItem key={faq.q} question={faq.q} answer={faq.a} />
          ))}
        </section>

        <footer className="slot-guide-footer">
          <span>WMF Japan • Zone Upgrade &amp; Dinner with Celebrity</span>
          <button type="button" onClick={() => navigateTo(HOME_PATH)}>
            ← Back to home
          </button>
        </footer>
      </main>
    </>
  );
}
