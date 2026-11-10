import { useEffect, useRef, useState } from "react";
import { useAuthSession } from "./auth/useAuth";
import { signOut } from "./auth/session";
import { t } from "./i18n";
import "./site.css";
import { siteConfig } from "./siteConfig";

const HOME_PATH = "/";
const EVENTS_PATH = "/events";
const MY_EVENTS_PATH = "/my/events";
const SIGN_IN_PATH = "/signin";
const ADMIN_PATH = "/admin";

// Account dropdown for a signed-in attendee. The name falls back to the email address.
function UserMenu({ navigateTo }) {
  const { session } = useAuthSession();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const triggerRef = useRef(null);

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

  if (!session) return null;

  const go = (path) => {
    setOpen(false);
    navigateTo(path);
  };
  const displayName = session.name || session.email.split("@")[0] || t("site.nav.account");
  const initials =
    displayName
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0].toUpperCase())
      .join("") || "A";
  const items = [
    { label: t("site.nav.myEvents"), onClick: () => go(MY_EVENTS_PATH) },
    {
      label: t("site.nav.signOut"),
      onClick: async () => {
        setOpen(false);
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
            <div className="wmf-user-hello">{t("auth.signedInAs", { email: session.email })}</div>
            <div className="wmf-user-avatar" aria-hidden="true">{initials}</div>
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
 * Fixed top navigation: logo, links, the organisation's own site and the account menu.
 * Collapses to a hamburger on small screens. The admin screens (showUserMenu={false}) pass their own links.
 */
export default function Navbar({ navigateTo, brand = siteConfig.name, links, primaryAction, showUserMenu = true }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const { session } = useAuthSession();
  const authed = !!session;

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
