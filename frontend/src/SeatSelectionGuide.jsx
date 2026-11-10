import { useState, useEffect } from "react";
import Navbar from "./Navbar";

const HOME_PATH = "/";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const HALL_SEAT_SELECTION_PATH = "/my/hall-seat-selection";
const SEAT_SELECTION_GUIDE_PATH = "/seat-selection-guide";
const ZONE_UPGRADE_GUIDE_PATH = "/zone-upgrade-guide";

// Event seat selection closes at 5:00 PM on 2 October 2026 in Tokyo. Pinned
// to +09:00 so the countdown reads the same for members watching from abroad.
// Please ensure you select your seat before the deadline.
const SEAT_DEADLINE_ISO = "2026-10-02T17:00:00+09:00";
const SEAT_DEADLINE_LABEL = "Friday, 2 October 2026, 5:00 PM (JST)";

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
    title: "Choose Seat",
    body: (
      <>
        Click <strong>Select Your Seats</strong>, select your preferred seat for every
        registered member excluding children below 6 years and save.
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
    Seat selection must be completed <strong>before {SEAT_DEADLINE_LABEL}.</strong>
  </>,
  <>
    A seat must be selected for <strong>every registered member</strong>, excluding children <strong>below 6 years</strong>.
  </>,
  <>After selecting the slot, a confirmation email containing your QR code will be sent to you.</>,
  <>
    <strong>On-site seat allocation will not be available.</strong>
  </>,
  <>
    After collecting the slip, please move to the event hall and sit on your assigned seat. Our volunteers will guide you to the seating area if there is a need.
  </>,
];

const FAQS = [
  {
    q: "Do children below 6 need a seat?",
    a: "No. A seat is not required for children below 6 years of age.",
  },
  {
    q: "Can I select my seat at the venue?",
    a: "No. On-site seat allocation will not be possible. Please select your seat in advance.",
  },
  {
    q: "What do I need to show at the venue?",
    a: "Show the QR code from your seat selection confirmation email to the volunteers.",
  },
  {
    q: "Can I site on a different seat?",
    a: "Members are requested to strictly sit onto their assigned seats to help manage the crowd smoothly.",
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

function SeatCountdown() {
  const deadlineMs = new Date(SEAT_DEADLINE_ISO).getTime();
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
      <p className="slot-guide-eyebrow">Seat Selection Deadline</p>
      <p className="slot-guide-countdown-date">{SEAT_DEADLINE_LABEL}</p>
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
          The seat selection deadline has passed. Please contact the coordinators for help.
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
 * Public seat selection guide for Aavani-26: explains that seat selection is
 * mandatory for every registered member, counts down to the selection
 * deadline, and sends members to their registrations to pick a seat.
 */
export default function SeatSelectionGuide({ navigateTo }) {
  const [agreed, setAgreed] = useState(false);

  return (
    <>
      <Navbar
        navigateTo={navigateTo}
      />

      <main className="slot-guide">
        <header className="slot-guide-header">
          <h1>Seat Selection Guide</h1>
          <p>
            Select your seat in advance and enjoy a smooth Celebrity Event experience.
          </p>
        </header>

        <SeatCountdown />

        <div className="slot-guide-alert">
          <strong>Important:</strong> This year, the Kasai Hall authorities are very strict about
          following the premises rules and regulations. We sincerely request everyone to follow
          the seat selection instructions and cooperate with the coordinators. Your support will help
          us manage the crowd effectively and ensure a smooth, enjoyable experience for everyone.
        </div>

        <div className="slot-guide-alert">
          <strong>Important:</strong> Seat selection is mandatory for{" "}
          <strong>all registered members, excluding children below 6 years of age.</strong>{" "}
          On-site seat allocation will not be possible.
        </div>

        <section className="slot-guide-section" aria-labelledby="slot-guide-steps-heading">
          <h2 id="slot-guide-steps-heading" className="slot-guide-section-title">
            How to Select Your Seat
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
            Once your seat is successfully selected, you will receive a confirmation email
            with your <strong>QR code.</strong>
          </p>
          <p className="slot-guide-centered">
            Keep the QR code ready on your phone and show it to the volunteers when you
            arrive.
          </p>
        </section>

        <section
          className="slot-guide-section slot-guide-cta"
          aria-labelledby="slot-guide-cta-heading"
        >
          <h2 id="slot-guide-cta-heading" className="slot-guide-section-title">
            Select Your Seat
          </h2>
          <div className="slot-guide-agree-row">
            <input
              type="checkbox"
              id="slot-guide-agree"
              checked={agreed}
              onChange={(event) => setAgreed(event.target.checked)}
            />
            <label htmlFor="slot-guide-agree">
              Yes, together we can make Celebrity Event a wonderful success!
            </label>
          </div>
          <button
            type="button"
            className="landing-hero-cta"
            disabled={!agreed}
            onClick={() => navigateTo(HALL_SEAT_SELECTION_PATH)}
          >
            Book My Seat
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
          <span>Aavani-26 • Celebrity Event Management</span>
          <button type="button" onClick={() => navigateTo(HOME_PATH)}>
            ← Back to home
          </button>
        </footer>
      </main>
    </>
  );
}
