import { useEffect, useRef, useState } from "react";
import { useTheme } from "./theme";
import { getMembershipStatus } from "./api";
import {
  clearRegistrantSession,
  getRegistrantEmail,
  isRegistrantAuthenticated,
  onRegistrantSessionChange,
} from "./registrantAuth";

const HOME_PATH = "/";
const MEMBER_PROFILE_PATH = "/member/profile";
const MEMBER_MEMBERSHIP_PATH = "/member/membership";
const MY_REGISTRATIONS_PATH = "/my/registrations";
const SPONSORS_PATH = "/sponsors";
const EVENTS_PATH = "/events";
const MY_EVENTS_PATH = "/my/events";
const ADMIN_PATH = "/admin";

// "Hello, <name>" dropdown for a signed-in member. The name comes from the
// membership profile; until it loads (or if it fails) we fall back to the email.
function UserMenu({ navigateTo }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState(() => getRegistrantEmail() || "");
  const [authed, setAuthed] = useState(() => isRegistrantAuthenticated());
  const ref = useRef(null);

  useEffect(
    () =>
      onRegistrantSessionChange(() => {
        setAuthed(isRegistrantAuthenticated());
        setEmail(getRegistrantEmail() || "");
        setName("");
      }),
    []
  );

  useEffect(() => {
    if (!authed) return undefined;
    let cancelled = false;
    getMembershipStatus()
      .then((status) => {
        if (!cancelled) setName(status?.profile?.fullName || status?.memberName || "");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [authed, email]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (ref.current && !ref.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (!authed) return null;

  const go = (path) => {
    setOpen(false);
    navigateTo(path);
  };
  const displayName = name || email.split("@")[0] || "Member";
  const initials =
    displayName
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join("") || "M";
  const items = [
    { label: "Member Profile", onClick: () => go(MEMBER_PROFILE_PATH) },
    { label: "WMF Japan Foundation Membership", onClick: () => go(MEMBER_MEMBERSHIP_PATH) },
    { label: "Registrations", onClick: () => go(MY_REGISTRATIONS_PATH) },
    { label: "My event registrations", onClick: () => go(MY_EVENTS_PATH) },
    {
      label: "Log Out",
      onClick: () => {
        setOpen(false);
        clearRegistrantSession();
        navigateTo(HOME_PATH);
      },
    },
  ];

  return (
    <div className="nk-user" ref={ref}>
      <button
        type="button"
        className="nk-user-trigger"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span aria-hidden="true">👤</span> <span className="nk-user-name">{displayName}</span>
      </button>
      {open ? (
        <div className="nk-user-menu" role="menu">
          <div className="nk-user-head">
            <div className="nk-user-hello">
              Hello,
              <strong>{displayName}</strong>
            </div>
            <div className="nk-user-avatar" aria-hidden="true">
              {initials}
            </div>
          </div>
          {items.map((item) => (
            <button key={item.label} type="button" role="menuitem" className="nk-user-item" onClick={item.onClick}>
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Fixed top navigation matching beta.wmfjapan.com:
 * logo on the left, inline links, a moon/sun theme toggle switch, and an
 * amber primary action on the right. Collapses to a hamburger on mobile.
 */
export default function Navbar({ navigateTo, brand = "WMF Japan", links, primaryAction, showUserMenu = true }) {
  const { theme, toggleTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const [authed, setAuthed] = useState(() => isRegistrantAuthenticated());
  useEffect(() => onRegistrantSessionChange(() => setAuthed(isRegistrantAuthenticated())), []);

  // One menu for the whole public site: Login / Register first (signed-in
  // members get the account dropdown instead), then Sponsors and Contact.
  // The admin screens (showUserMenu={false}) pass their own.
  const menuLinks =
    showUserMenu === false
      ? links || []
      : [
          ...(authed
            ? []
            : [{ label: "Login / Register", cta: true, onClick: () => navigateTo(MEMBER_PROFILE_PATH) }]),
          { label: "Events", onClick: () => navigateTo(EVENTS_PATH) },
          { label: "Sponsors", onClick: () => navigateTo(SPONSORS_PATH) },
          {
            label: "Contact",
            onClick: () => {
              window.location.href = "mailto:info@wmfjapan.com";
            },
          },
          { label: "Admin Login", onClick: () => navigateTo(ADMIN_PATH), mobileOnly: true },
        ];

  const handleLinkClick = (onClick) => {
    setMenuOpen(false);
    if (onClick) {
      onClick();
    }
  };

  return (
    <nav className="nk-nav">
      <div className="nk-nav-inner">
        <button
          type="button"
          className="nk-nav-logo"
          onClick={() => handleLinkClick(() => navigateTo(HOME_PATH))}
        >
          <img src="/logo-sm.png" width="110" height="60" alt={`${brand} logo`} />
        </button>

        <div className={`nk-nav-links ${menuOpen ? "open" : ""}`}>
          {menuLinks.map((link) => (
            <button
              key={link.label}
              type="button"
              className={link.cta ? "nk-nav-cta" : `nk-nav-link${link.mobileOnly ? " nk-nav-link--mobile-only" : ""}`}
              onClick={() => handleLinkClick(link.onClick)}
            >
              {link.label}
            </button>
          ))}
          {primaryAction ? (
            <button
              type="button"
              className="nk-nav-cta"
              onClick={() => handleLinkClick(primaryAction.onClick)}
            >
              {primaryAction.label}
            </button>
          ) : null}
        </div>

        <div className="nk-nav-actions">
          {showUserMenu ? <UserMenu navigateTo={navigateTo} /> : null}
          <button
            type="button"
            className="nk-theme-toggle"
            onClick={toggleTheme}
            aria-label="Toggle theme"
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            <span className="nk-theme-icon" aria-hidden="true">🌙</span>
            <span className={`nk-theme-track ${theme === "light" ? "light" : ""}`}>
              <span className="nk-theme-knob" />
            </span>
            <span className="nk-theme-icon" aria-hidden="true">☀️</span>
          </button>

          <button
            type="button"
            className="nk-menu-toggle"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((prev) => !prev)}
          >
            {menuOpen ? "✕" : "☰"}
          </button>
        </div>
      </div>
    </nav>
  );
}
