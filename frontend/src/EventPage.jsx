import { useEffect, useState } from "react";
import EventShell, { ErrorBox, Loading } from "./EventShell";
import { getPublicEvent } from "./eventsApi";
import { ADDON_KEYS, getEventState, normalizeEvent } from "./eventModel";
import { formatJpy, formatJst, t } from "./i18n";
import { siteConfig } from "./siteConfig";

const SECTION_KEYS = ["agenda", "speakers", "sponsors", "faq", "contact"];

const ICONS = {
  calendar: "M7 2v2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2V2h-2v2H9V2H7zm12 8v9H5v-9h14z",
  pin: "M12 2a7 7 0 0 0-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 0 0-7-7zm0 9.5A2.5 2.5 0 1 1 12 6a2.5 2.5 0 0 1 0 5.5z",
  video: "M15 8v8H5V8h10zm2 3l4-3v8l-4-3v-2z",
};

function Icon({ name }) {
  return <svg className="pe-icon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false"><path d={ICONS[name]} fill="currentColor" /></svg>;
}

function guestBadge(ev) {
  if (ev.access?.membersOnly) return <span className="pe-pill pe-pill-accent">{t("pub.membersOnly")}</span>;
  if (!ev.capabilities.guestCheckout.enabled) return <span className="pe-pill pe-pill-accent">{t("pub.signInRequiredBadge")}</span>;
  return <span className="pe-pill">{t("pub.guestsWelcome")}</span>;
}

/** Add-ons this event offers, from its capabilities. Disabled ones are not rendered at all. */
function AddonList({ ev, navigateTo }) {
  const enabled = ADDON_KEYS.filter((key) => ev.capabilities[key].enabled);
  if (enabled.length === 0) return null;
  return (
    <section aria-labelledby="pe-addons-h" className="pe-section pe-addons">
      <h2 id="pe-addons-h">{t("pub.addons.title")}</h2>
      <ul>
        {enabled.map((key) => <li key={key} data-addon={key}>{t(`pub.addons.${key}`)}</li>)}
      </ul>
      <button type="button" className="button secondary" onClick={() => navigateTo("/my/registrations")}>{t("pub.addons.manage")}</button>
    </section>
  );
}

export function RegistrationBox({ ev, navigateTo }) {
  const r = ev.registration;
  const state = getEventState(ev);
  const go = () => navigateTo(`/events/${ev.slug}/register`);
  let body;
  if (state === "cancelled") {
    body = <p role="status">{t("pub.state.cancelled")}</p>;
  } else if (state === "unavailable") {
    body = <p role="status">{t("pub.state.registrationUnavailable")}</p>;
  } else if (state === "not_open") {
    body = <p>{t("pub.reg.opensAt", { when: ev.registrationOpensAt ? formatJst(ev.registrationOpensAt) : "" })}</p>;
  } else if (state === "closed") {
    body = <p role="status">{t("pub.registrationClosed")}</p>;
  } else if (state === "members_only" || state === "renewal_required") {
    body = (
      <>
        <p>{t(state === "members_only" ? "pub.reg.membersOnlyBlocked" : "pub.reg.renewalBlocked")}</p>
        <button type="button" className="button primary" onClick={() => navigateTo("/member/membership")}>{t("pub.reg.renewNow")}</button>
      </>
    );
  } else if (state === "sold_out") {
    body = <p role="status">{t("pub.state.soldOut")}</p>;
  } else if (state === "sign_in_required") {
    body = (
      <>
        <p>{t(ev.access.membersOnly ? "pub.reg.membersOnlySignIn" : "pub.reg.signInRequired")}</p>
        <button type="button" className="button primary" onClick={go}>{t("pub.reg.signIn")}</button>
        {ev.access.membersOnly && (
          <button type="button" className="button secondary" onClick={() => navigateTo("/member/membership")}>{t("pub.reg.joinMembership")}</button>
        )}
      </>
    );
  } else {
    body = (
      <>
        {r.waitlist && <p>{t("pub.reg.full")}</p>}
        <button type="button" className="button primary" onClick={go}>{t(r.waitlist ? "pub.reg.joinWaitlist" : "pub.reg.register")}</button>
        {!ev.viewer.loggedIn && ev.tickets.some((x) => x.memberPriceJpy < x.nonMemberPriceJpy) && (
          <p className="pe-note">{t("pub.reg.memberDiscountHint")}</p>
        )}
      </>
    );
  }
  const prices = ev.tickets.filter((x) => x.remaining !== 0).map((x) => x.yourPriceJpy);
  const paid = prices.filter((x) => x > 0);
  return (
    <section className="pe-box pe-regbox" aria-labelledby="pe-reg-h" data-state={state}>
      <h2 id="pe-reg-h">{t("pub.reg.title")}</h2>
      {prices.length > 0 && <p className="pe-from">{paid.length ? t("pub.fromPrice", { price: formatJpy(Math.min(...paid)) }) : t("pub.free")}</p>}
      <div className="pe-regbody">{body}</div>
    </section>
  );
}

export default function EventPage({ navigateTo, slug }) {
  const [ev, setEv] = useState(null);
  const [error, setError] = useState("");

  const load = () => {
    let cancelled = false;
    setError("");
    setEv(null);
    getPublicEvent(slug)
      .then((d) => {
        if (cancelled) return;
        const event = normalizeEvent({ ...d.event, legacy: !!d.legacy });
        setEv(event);
        document.title = `${event.title} | ${siteConfig.name}`;
      })
      .catch((e) => !cancelled && setError(e.status === 404 ? t("pub.notFound") : e.message || t("pub.error.generic")));
    return () => {
      cancelled = true;
    };
  };

  useEffect(load, [slug]);

  if (error) {
    return (
      <EventShell navigateTo={navigateTo}>
        <ErrorBox message={error} />
        <div className="btn-row">
          <button type="button" className="button secondary" onClick={load}>{t("pub.retry")}</button>
          <button type="button" className="button secondary" onClick={() => navigateTo("/events")}>{t("pub.nav.allEvents")}</button>
        </div>
      </EventShell>
    );
  }
  if (!ev) return <EventShell navigateTo={navigateTo}><Loading /></EventShell>;

  const d = ev.design || {};
  const layout = d.layout || "classic";
  const place = [ev.location?.venueName, ev.location?.address].filter(Boolean).join(", ");
  const sections = (d.sections || []).filter((s) => s.enabled && s.content && SECTION_KEYS.includes(s.key));
  return (
    <EventShell navigateTo={navigateTo} accent={d.accentColor} className={`pe-layout-${layout}`}>
      <article className="pe-article">
        <header className="pe-header">
          {layout !== "minimal" && d.coverImage && (
            <div className="pe-cover"><img src={d.coverImage} alt="" /></div>
          )}
          <div className="pe-titleblock">
            {ev.category && <p className="pe-eyebrow">{ev.category}</p>}
            <h1>{ev.title}</h1>
            <ul className="pe-chips">
              <li><Icon name="calendar" /><span>{ev.startsAt ? formatJst(ev.startsAt) : t("pub.dateTbd")}{ev.endsAt ? ` – ${formatJst(ev.endsAt)}` : ""}</span></li>
              {place && (
                <li><Icon name="pin" /><span>{place}{ev.location?.mapUrl && <> · <a href={ev.location.mapUrl} target="_blank" rel="noopener noreferrer">{t("pub.map")}</a></>}</span></li>
              )}
              {ev.hasOnlineLink && <li><Icon name="video" /><span>{t("pub.onlineAfterRegistering")}</span></li>}
            </ul>
            <p className="pe-badges">
              {guestBadge(ev)}
              {ev.tags?.map((tag) => <span key={tag} className="pe-pill">{tag}</span>)}
            </p>
          </div>
        </header>

        <div className="pe-columns">
          <div className="pe-main">
            {ev.shortDescription && <p className="pe-lead">{ev.shortDescription}</p>}
            {ev.longDescription && <div className="pe-rich" dangerouslySetInnerHTML={{ __html: ev.longDescription }} />}

            {ev.tickets.length > 0 && (
              <section aria-labelledby="pe-tix-h" className="pe-section">
                <h2 id="pe-tix-h">{t("pub.tickets")}</h2>
                <ul className="pe-tickets">
                  {ev.tickets.map((x) => (
                    <li key={x.ticketTypeId} className={x.remaining === 0 ? "soldout" : ""}>
                      <div className="pe-ticket-info">
                        <strong>{x.name}</strong>
                        {x.description && <p className="pe-meta">{x.description}</p>}
                        {x.earlyBirdOpen && <p className="pe-note">{t("pub.earlyBird", { price: formatJpy(x.earlyBirdMemberPriceJpy), until: formatJst(x.earlyBirdCutoff) })}</p>}
                        {x.perPersonCap && <p className="pe-meta">{t("pub.maxPer", { n: x.perPersonCap })}</p>}
                      </div>
                      <div className="pe-prices">
                        <span className="pe-price">{formatJpy(x.yourPriceJpy)}</span>
                        {x.memberPriceJpy !== x.nonMemberPriceJpy && (
                          <small>{t("pub.memberVsGuest", { member: formatJpy(x.memberPriceJpy), guest: formatJpy(x.nonMemberPriceJpy) })}</small>
                        )}
                        {x.remaining === 0 ? <small className="pe-warn">{t("pub.soldOut")}</small>
                          : x.remaining !== null && x.remaining <= 10 && <small className="pe-warn">{t("pub.fewLeft", { n: x.remaining })}</small>}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {sections.map((s) => (
          <section key={s.key} aria-labelledby={`pe-sec-${s.key}`} className="pe-section">
            <h2 id={`pe-sec-${s.key}`}>{t(`pub.sections.${s.key}`)}</h2>
            <div className="pe-rich" dangerouslySetInnerHTML={{ __html: s.content }} />
          </section>
        ))}

            {d.gallery?.length > 0 && (
          <section aria-labelledby="pe-gal-h" className="pe-section">
            <h2 id="pe-gal-h">{t("pub.gallery")}</h2>
            <div className="pe-gallery">
              {d.gallery.map((url, i) => <img key={url} src={url} alt={t("pub.galleryAlt", { n: i + 1 })} loading="lazy" />)}
            </div>
          </section>
        )}
          </div>
          <aside className="pe-aside"><RegistrationBox ev={ev} navigateTo={navigateTo} /><AddonList ev={ev} navigateTo={navigateTo} /></aside>
        </div>
      </article>
    </EventShell>
  );
}
