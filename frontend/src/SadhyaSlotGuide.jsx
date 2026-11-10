import { useState, useEffect } from "react";
import Navbar from "./Navbar";

const HOME_PATH = "/";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const SEAT_SELECTION_GUIDE_PATH = "/seat-selection-guide";
const ZONE_UPGRADE_GUIDE_PATH = "/zone-upgrade-guide";

// Sadhya slot selection closes end of day 11 September 2026 in Tokyo. Pinned to
// +09:00 so the countdown reads the same for members watching from abroad.
const SLOT_DEADLINE_ISO = "2026-09-11T23:59:59+09:00";
const SLOT_DEADLINE_LABEL = "Friday, 11 September 2026";

const STEPS = [
  {
    title: "Login",
    body: (
      <>
        Login to your registration using your <strong>registered email address</strong>.
      </>
    ),
  },
  {
    title: "Choose Slot",
    body: (
      <>
        Click <strong>Change Entry Slot</strong>, select your preferred slot for every
        registered member and save.
      </>
    ),
  },
  {
    title: "Get Confirmation",
    body: <>You will receive a confirmation email containing your QR code.</>,
  },
];

const REMINDERS = [
  <>
    Slot selection must be completed <strong>before {SLOT_DEADLINE_LABEL}.</strong>
  </>,
  <>
    A slot must be selected for <strong>every registered member</strong>, including children
    below 6 years.
  </>,
  <>After selecting the slot, a confirmation email containing your QR code will be sent to you.</>,
  <>
    <strong>On-site slot allocation will not be available.</strong>
  </>,
  <>Please show the QR code at the Registration Desk (4F) to receive your Sadhya coupon.</>,
  <>
    After collecting the coupon, please wait at the 4th Floor Hall. Our volunteers will guide you to the Sadhya Hall according to your assigned slot.
  </>,
  <>
    Please arrive at least 15 minutes before your assigned Sadhya slot and kindly follow your
    allotted timing.
  </>,
  <>
    Please arrive on time for your allotted Sadhya slot. If you miss your slot, please check with the crowd management team. However, we cannot guarantee accommodation in the next slot, as it may already be fully booked.
  </>
];

const FAQS = [
  {
    q: "Do children below 6 need a slot?",
    a: "Yes. A Sadhya slot must be selected for every registered member, including children below 6 years of age.",
  },
  {
    q: "Can I select my slot at the venue?",
    a: "No. On-site slot allocation will not be possible. Please select your slot in advance.",
  },
  {
    q: "What do I need to show at the venue?",
    a: "Show the QR code from your Sadhya confirmation email at the registration desk.",
  },
  {
    q: "Can I come at a different time?",
    a: "Members are requested to strictly follow their assigned Sadhya slot to help manage the crowd smoothly.",
  },
];

/** Whole days/hours/minutes/seconds left until the deadline, or null once past. */
function remainingUntil(deadlineMs) {
  const difference = deadlineMs - Date.now();
  if (difference <= 0) return null;
  return {
    days: Math.floor(difference / (1000 * 60 * 60 * 24)),
    hours: Math.floor((difference / (1000 * 60 * 60)) % 24),
    minutes: Math.floor((difference / (1000 * 60)) % 60),
    seconds: Math.floor((difference / 1000) % 60),
  };
}

function SlotCountdown() {
  const deadlineMs = new Date(SLOT_DEADLINE_ISO).getTime();
  const [remaining, setRemaining] = useState(() => remainingUntil(deadlineMs));

  useEffect(() => {
    const timer = setInterval(() => setRemaining(remainingUntil(deadlineMs)), 1000);
    return () => clearInterval(timer);
  }, [deadlineMs]);

  const boxes = [
    { label: "Days", value: remaining?.days },
    { label: "Hours", value: remaining?.hours },
    { label: "Minutes", value: remaining?.minutes },
    { label: "Seconds", value: remaining?.seconds },
  ];

  return (
    <div className="slot-guide-countdown">
      <p className="slot-guide-eyebrow">Slot Selection Deadline</p>
      <p className="slot-guide-countdown-date">{SLOT_DEADLINE_LABEL}</p>
      <div className="slot-guide-timer" role="timer" aria-live="off">
        {boxes.map((box) => (
          <div key={box.label} className="slot-guide-time-box">
            <strong>{remaining ? box.value : 0}</strong>
            <span>{box.label}</span>
          </div>
        ))}
      </div>
      {remaining ? null : (
        <p className="slot-guide-countdown-closed">
          The slot selection deadline has passed. Please contact the coordinators for help.
        </p>
      )}
    </div>
  );
}

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
 * Public Sadhya slot guide for Aavani-26: explains that slot selection is
 * mandatory for every registered member, counts down to the selection
 * deadline, and sends members to their registrations to pick a slot.
 */
export default function SadhyaSlotGuide({ navigateTo }) {
  const [agreed, setAgreed] = useState(false);

  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />

      <main className="slot-guide">
        <header className="slot-guide-header">
          <h1>Sadhya Slot Guide</h1>
          <p>
            Select your Sadhya slot in advance and enjoy a smooth Onam experience.
          </p>
        </header>

        <SlotCountdown />

        <div className="slot-guide-alert">
          <strong>Important:</strong> This year, the Kasai Hall authorities are very strict about
          following the premises rules and regulations. We sincerely request everyone to follow
          the Sadhya slot instructions and cooperate with the coordinators. Your support will help
          us manage the crowd effectively and ensure a smooth, enjoyable experience for everyone.
        </div>

        <div className="slot-guide-alert">
          <strong>Important:</strong> Sadhya slot selection is mandatory for{" "}
          <strong>all registered members, including children below 6 years of age.</strong>{" "}
          On-site slot allocation will not be possible.
        </div>

        <section className="slot-guide-section" aria-labelledby="slot-guide-steps-heading">
          <h2 id="slot-guide-steps-heading" className="slot-guide-section-title">
            How to Select Your Slot
          </h2>
          <div className="slot-guide-steps">
            {STEPS.map((step, index) => (
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
          aria-labelledby="slot-guide-remember-heading"
        >
          <h2 id="slot-guide-remember-heading" className="slot-guide-section-title">
            Please Remember
          </h2>
          <ul className="slot-guide-checklist">
            {REMINDERS.map((reminder, index) => (
              <li key={index}>{reminder}</li>
            ))}
          </ul>
        </section>

        <section
          className="slot-guide-section slot-guide-card"
          aria-labelledby="slot-guide-qr-heading"
        >
          <h2 id="slot-guide-qr-heading" className="slot-guide-section-title">
            Your QR Code is Your Pass
          </h2>
          <p className="slot-guide-centered">
            Once your Sadhya slot is successfully selected, you will receive a confirmation email
            with your <strong>QR code.</strong>
          </p>
          <p className="slot-guide-centered">
            Keep the QR code ready on your phone and show it at the registration desk when you
            arrive.
          </p>
        </section>

        <section
          className="slot-guide-section slot-guide-cta"
          aria-labelledby="slot-guide-cta-heading"
        >
          <h2 id="slot-guide-cta-heading" className="slot-guide-section-title">
            Select Your Sadhya Slot
          </h2>
          <div className="slot-guide-agree-row">
            <input
              type="checkbox"
              id="slot-guide-agree"
              checked={agreed}
              onChange={(event) => setAgreed(event.target.checked)}
            />
            <label htmlFor="slot-guide-agree">
              Yes, I read above reminders and guidelines, together we can make Onam a wonderful success!
            </label>
          </div>
          <button
            type="button"
            className="landing-hero-cta"
            disabled={!agreed}
            onClick={() => navigateTo(MY_REGISTRATIONS_PATH)}
          >
            Book My Sadhya Slot
          </button>
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
          <span>Aavani-26 • Sadhya Slot Management</span>
          <button type="button" onClick={() => navigateTo(HOME_PATH)}>
            ← Back to home
          </button>
        </footer>
      </main>
    </>
  );
}
