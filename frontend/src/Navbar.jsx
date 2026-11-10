import { useEffect, useRef, useState } from "react";
import { useAuthSession } from "./auth/useAuth";
import { signOut } from "./auth/session";
import { t } from "./i18n";
import { useTheme } from "./theme";
import "./site.css";
import { getMembershipStatus } from "./api";
import { siteConfig } from "./siteConfig";
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
const SIGN_IN_PATH = "/signin";
const ADMIN_PATH = "/admin";

// Account dropdown for a signed-in attendee (Cognito account) and/or a member signed in with the older
// one-time-code login. The name falls back to the email address.
function UserMenu({ navigateTo }) {
  const { session } = useAuthSession();
  const [open, setOpen] = useState(false);
  const [memberName, setMemberName] = useState("");
  const [memberEmail, setMemberEmail] = useState(() => getRegistrantEmail() || "");
  const [memberAuthed, setMemberAuthed] = useState(() => isRegistrantAuthenticated());
  const ref = useRef(null);
  const triggerRef = useRef(null);

  useEffect(
    () =>
      onRegistrantSessionChange(() => {
        setMemberAuthed(isRegistrantAuthenticated());
        setMemberEmail(getRegistrantEmail() || "");
        setMemberName("");
      }),
    []
  );

  useEffect(() => {
    if (!memberAuthed) return undefined;
    let cancelled = false;
    getMembershipStatus()
      .then((status) => {
        if (!cancelled) setMemberName(status?.profile?.fullName || status?.memberName || "");
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [memberAuthed, memberEmail]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => {
      if (ref.current && !ref.current.contains(event.target)) setOpen(false);
    };
    const onKey = (event) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!session && !memberAuthed) return null;

  const go = (path) => {
    setOpen(false);
    navigateTo(path);
  };
  const email = session?.email || memberEmail;
  const displayName = session?.name || memberName || email.split("@")[0] || t("site.nav.account");
  const initials =
    displayName
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join("") || "A";
  const items = [
    { label: t("site.nav.myEvents"), onClick: () => go(MY_EVENTS_PATH) },
    ...(memberAuthed
      ? [
          { label: "Member Profile", onClick: () => go(MEMBER_PROFILE_PATH) },
          { label: "WMF Japan Foundation Membership", onClick: () => go(MEMBER_MEMBERSHIP_PATH) },
          { label: "Registrations", onClick: () => go(MY_REGISTRATIONS_PATH) },
        ]
      : []),
    {
      label: t("site.nav.signOut"),
      onClick: async () => {
        setOpen(false);
        clearRegistrantSession();
        const { hostedLogoutUrl } = await signOut();
        if (hostedLogoutUrl) window.location.assign(hostedLogoutUrl);
        else navigateTo(HOME_PATH);
      },
    },
  ];

  return (
    <div className="wmf-user" ref={ref}>
      <button
        type="button"
        className="wmf-user-trigger"
        ref={triggerRef}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`${t("site.nav.account")}: ${displayName}`}
        onClick={() => setOpen((prev) => !prev)}
      >
        <span aria-hidden="true">👤</span> <span className="wmf-user-name">{displayName}</span>
      </button>
      {open ? (
        <div className="wmf-user-menu" role="menu">
          <div className="wmf-user-head">
            <div className="wmf-user-hello">
              {t("auth.signedInAs", { email })}
            </div>
            <div className="wmf-user-avatar" aria-hidden="true">
              {initials}
            </div>
          </div>
          {items.map((item) => (
            <button key={item.label} type="button" role="menuitem" className="wmf-user-item" onClick={item.onClick}>
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Fixed top navigation: logo, links, the organisation's own site, theme toggle and the account menu.
 * Collapses to a hamburger on small screens. The admin screens (showUserMenu={false}) pass their own links.
 */
export default function Navbar({ navigateTo, brand = siteConfig.name, links, primaryAction, showUserMenu = true }) {
  const { theme, toggleTheme } = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const { session } = useAuthSession();
  const [memberAuthed, setMemberAuthed] = useState(() => isRegistrantAuthenticated());
  useEffect(() => onRegistrantSessionChange(() => setMemberAuthed(isRegistrantAuthenticated())), []);
  const authed = !!session || memberAuthed;

  const goSignIn = () => {
    const here = `${window.location.pathname}${window.location.search}`;
    navigateTo(here.startsWith("/events") || here.startsWith("/my/events") ? `${SIGN_IN_PATH}?returnTo=${encodeURIComponent(here)}` : SIGN_IN_PATH);
  };

  const menuLinks =
    showUserMenu === false
      ? links || []
      : [
          ...(authed ? [] : [{ label: t("site.nav.signIn"), cta: true, onClick: goSignIn }]),
          { label: t("site.nav.events"), onClick: () => navigateTo(EVENTS_PATH) },
          { label: "Sponsors", onClick: () => navigateTo(SPONSORS_PATH) },
          { label: t("site.orgLink", { org: siteConfig.orgName }), href: siteConfig.orgUrl, external: true },
          ...(siteConfig.contactEmail ? [{ label: t("site.footer.contact"), href: `mailto:${siteConfig.contactEmail}` }] : []),
          { label: t("site.footer.adminLogin"), onClick: () => navigateTo(ADMIN_PATH), mobileOnly: true },
        ];

  const handleLinkClick = (onClick) => {
    setMenuOpen(false);
    if (onClick) {
      onClick();
    }
  };

  return (
    <nav className="wmf-nav" aria-label={t("site.nav.primary")}>
      <a className="skip-link" href="#top">{t("site.skipToContent")}</a>
      <div className="wmf-nav-inner">
        <button
          type="button"
          className="wmf-nav-logo"
          aria-label={brand}
          onClick={() => handleLinkClick(() => navigateTo(HOME_PATH))}
        >
          {siteConfig.logo.src ? (
            <img src={siteConfig.logo.src} height="48" alt={siteConfig.logo.alt} />
          ) : (
            <span className="wmf-nav-wordmark">{siteConfig.name}</span>
          )}
        </button>

        <div id="wmf-nav-links" className={`wmf-nav-links ${menuOpen ? "open" : ""}`}>
          {menuLinks.map((link) =>
            link.href ? (
              <a
                key={link.label}
                className={`wmf-nav-link${link.mobileOnly ? " wmf-nav-link--mobile-only" : ""}`}
                href={link.href}
                {...(link.external ? { target: "_blank", rel: "noopener noreferrer", "aria-label": t("site.orgLinkAria", { org: siteConfig.orgName }) } : {})}
              >
                {link.label}
              </a>
            ) : (
              <button
                key={link.label}
                type="button"
                className={link.cta ? "wmf-nav-cta" : `wmf-nav-link${link.mobileOnly ? " wmf-nav-link--mobile-only" : ""}`}
                onClick={() => handleLinkClick(link.onClick)}
              >
                {link.label}
              </button>
            )
          )}
          {primaryAction ? (
            <button
              type="button"
              className="wmf-nav-cta"
              onClick={() => handleLinkClick(primaryAction.onClick)}
            >
              {primaryAction.label}
            </button>
          ) : null}
        </div>

        <div className="wmf-nav-actions">
          {showUserMenu ? <UserMenu navigateTo={navigateTo} /> : null}
          <button
            type="button"
            className="wmf-theme-toggle"
            onClick={toggleTheme}
            aria-label={t("site.nav.themeToggle")}
            aria-pressed={theme === "light"}
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            <span className="wmf-theme-icon" aria-hidden="true">🌙</span>
            <span className={`wmf-theme-track ${theme === "light" ? "light" : ""}`}>
              <span className="wmf-theme-knob" />
            </span>
            <span className="wmf-theme-icon" aria-hidden="true">☀️</span>
          </button>

          <button
            type="button"
            className="wmf-menu-toggle"
            aria-label={menuOpen ? t("site.nav.menuClose") : t("site.nav.menuOpen")}
            aria-expanded={menuOpen}
            aria-controls="wmf-nav-links"
            onClick={() => setMenuOpen((prev) => !prev)}
          >
            {menuOpen ? "✕" : "☰"}
          </button>
        </div>
      </div>
    </nav>
  );
}
