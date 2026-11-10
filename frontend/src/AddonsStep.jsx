import { formatJpy, formatJst, t } from "./i18n";

// Checkout step for the add-ons an event offers (entry slot, zone upgrades, seats). It renders only what the event
// has switched on. The server prices and reserves everything again at registration: nothing here is trusted.

const ROW_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
export const EMPTY_ADDONS = { slotId: "", upgrades: {}, seats: [] };

export const hasAddons = (ev) => ["entrySlots", "zoneUpgrade", "seatReservation"].some((k) => ev.capabilities?.[k]?.enabled && ev.addons?.[k]);

/** The request body's `addons` for the selection (only enabled add-ons are ever included). */
export function addonsPayload(ev, value) {
  const out = {};
  if (ev.capabilities?.entrySlots?.enabled && ev.addons?.entrySlots) out.entrySlots = { slotId: value.slotId };
  if (ev.capabilities?.zoneUpgrade?.enabled && ev.addons?.zoneUpgrade) {
    const picked = Object.fromEntries(Object.entries(value.upgrades).filter(([, n]) => n > 0));
    if (Object.keys(picked).length) out.zoneUpgrade = picked;
  }
  if (ev.capabilities?.seatReservation?.enabled && ev.addons?.seatReservation) out.seatReservation = { seats: value.seats };
  return out;
}

/** Whole-yen cost of the selection, for display only. */
export function addonsTotal(ev, value) {
  let total = 0;
  (ev.addons?.zoneUpgrade?.upgrades || []).forEach((u) => { total += u.priceJpy * (value.upgrades[u.upgradeId] || 0); });
  const sections = ev.addons?.seatReservation?.sections || [];
  value.seats.forEach((seat) => {
    const section = sections.find((s) => seat.startsWith(`${s.sectionId}-`));
    total += section?.extraPriceJpy || 0;
  });
  return total;
}

/** Problems with the selection, keyed like the server's field errors. */
export function addonErrors(ev, value, seats) {
  const errors = {};
  if (ev.capabilities?.entrySlots?.enabled && ev.addons?.entrySlots && !value.slotId) errors["addons.entrySlots"] = t("pub.addons.chooseSlot");
  if (ev.capabilities?.seatReservation?.enabled && ev.addons?.seatReservation && value.seats.length !== seats) {
    errors["addons.seatReservation"] = t("pub.addons.chooseSeats", { n: seats });
  }
  return errors;
}

function SeatSection({ section, taken, selected, onToggle, limitReached }) {
  return (
    <div className="pe-seatsection">
      <h4>{section.name}{section.extraPriceJpy ? <small> +{formatJpy(section.extraPriceJpy)} {t("pub.addons.perSeat")}</small> : null}</h4>
      <div className="pe-seatgrid" role="group" aria-label={section.name}>
        {Array.from({ length: section.rows }, (_, r) => (
          <div className="pe-seatrow" key={r}>
            <span className="pe-rowlabel" aria-hidden="true">{ROW_LETTERS[r]}</span>
            {Array.from({ length: section.seatsPerRow }, (_, n) => {
              const id = `${section.sectionId}-${ROW_LETTERS[r]}${n + 1}`;
              const isTaken = taken.has(id);
              const isSelected = selected.includes(id);
              return (
                <button key={id} type="button" className={`pe-seat${isSelected ? " selected" : ""}${isTaken ? " taken" : ""}`}
                  aria-pressed={isSelected} disabled={isTaken || (!isSelected && limitReached)}
                  aria-label={`${section.name} ${ROW_LETTERS[r]}${n + 1}${isTaken ? ` (${t("pub.addons.taken")})` : ""}`}
                  onClick={() => onToggle(id)}>{n + 1}</button>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AddonsStep({ ev, seats, value, onChange, errors, headingRef }) {
  const caps = ev.capabilities;
  const info = ev.addons || {};
  const taken = new Set(info.seatReservation?.taken || []);
  const set = (patch) => onChange({ ...value, ...patch });
  const toggleSeat = (id) => set({ seats: value.seats.includes(id) ? value.seats.filter((s) => s !== id) : [...value.seats, id] });
  const upgradeTotal = Object.values(value.upgrades).reduce((a, b) => a + b, 0);

  return (
    <section className="pe-box">
      <h2 tabIndex={-1} ref={headingRef}>{t("pub.step.addons")}</h2>

      {caps.entrySlots.enabled && info.entrySlots && (
        <fieldset className="pe-addon-group">
          <legend>{t("pub.addons.entrySlots")}</legend>
          <p className="pe-meta">{t("pub.addons.slotHelp", { n: seats })}</p>
          {info.entrySlots.slots.map((slot) => {
            const full = slot.remaining < seats;
            return (
              <label key={slot.slotId} className={`ep-choice${full ? " soldout" : ""}`}>
                <input type="radio" name="slot" value={slot.slotId} checked={value.slotId === slot.slotId} disabled={full}
                  onChange={() => set({ slotId: slot.slotId })} />{" "}
                <strong>{slot.name}</strong>
                {slot.startsAt && <small> — {formatJst(slot.startsAt)}{slot.endsAt ? ` – ${formatJst(slot.endsAt)}` : ""}</small>}
                <small> · {slot.remaining === 0 ? t("pub.soldOut") : t("pub.addons.placesLeft", { n: slot.remaining })}</small>
              </label>
            );
          })}
          {errors["addons.entrySlots"] && <span className="field-error" role="alert">{errors["addons.entrySlots"]}</span>}
        </fieldset>
      )}

      {caps.zoneUpgrade.enabled && info.zoneUpgrade && (
        <fieldset className="pe-addon-group">
          <legend>{t("pub.addons.zoneUpgrade")}</legend>
          <p className="pe-meta">{t("pub.addons.upgradeHelp", { n: seats })}</p>
          <ul className="pe-tickets">
            {info.zoneUpgrade.upgrades.map((u) => {
              const qty = value.upgrades[u.upgradeId] || 0;
              const max = Math.min(seats - (upgradeTotal - qty), u.remaining ?? seats);
              return (
                <li key={u.upgradeId}>
                  <div>
                    <label htmlFor={`up-${u.upgradeId}`}><strong>{u.name}</strong></label>
                    {u.description && <p className="pe-meta">{u.description}</p>}
                    <p className="pe-price">+{formatJpy(u.priceJpy)} <small>{t("pub.addons.each")}</small></p>
                    {u.remaining === 0 && <p className="pe-warn">{t("pub.soldOut")}</p>}
                  </div>
                  <div className="pe-qty">
                    <button type="button" className="button secondary" aria-label={t("pub.tickets.less", { name: u.name })} disabled={qty === 0}
                      onClick={() => set({ upgrades: { ...value.upgrades, [u.upgradeId]: qty - 1 } })}>−</button>
                    <input id={`up-${u.upgradeId}`} type="number" min="0" max={Math.max(0, max)} value={qty} inputMode="numeric"
                      onChange={(e) => set({ upgrades: { ...value.upgrades, [u.upgradeId]: Math.max(0, Math.min(Math.max(0, max), Number(e.target.value) || 0)) } })} />
                    <button type="button" className="button secondary" aria-label={t("pub.tickets.more", { name: u.name })} disabled={qty >= max}
                      onClick={() => set({ upgrades: { ...value.upgrades, [u.upgradeId]: qty + 1 } })}>+</button>
                  </div>
                </li>
              );
            })}
          </ul>
          {errors["addons.zoneUpgrade"] && <span className="field-error" role="alert">{errors["addons.zoneUpgrade"]}</span>}
        </fieldset>
      )}

      {caps.seatReservation.enabled && info.seatReservation && (
        <fieldset className="pe-addon-group">
          <legend>{t("pub.addons.seatReservation")}</legend>
          <p className="pe-meta" aria-live="polite">{t("pub.addons.seatHelp", { chosen: value.seats.length, n: seats })}</p>
          {info.seatReservation.sections.map((section) => (
            <SeatSection key={section.sectionId} section={section} taken={taken} selected={value.seats} onToggle={toggleSeat}
              limitReached={value.seats.length >= seats} />
          ))}
          {errors["addons.seatReservation"] && <span className="field-error" role="alert">{errors["addons.seatReservation"]}</span>}
        </fieldset>
      )}
    </section>
  );
}

/** The add-ons a stored registration holds (slot, upgrades, seats), as list items. Renders nothing when there are none. */
export function RegistrationAddons({ reg }) {
  const rec = reg.addons || {};
  const items = [];
  if (rec.entrySlot) items.push(<li key="slot">{t("pub.summary.slot")}: {rec.entrySlot.name}{rec.entrySlot.startsAt ? ` (${formatJst(rec.entrySlot.startsAt)})` : ""}</li>);
  (rec.upgrades || []).forEach((u) => items.push(<li key={u.upgradeId}>{u.name} × {u.qty} — {formatJpy(u.subtotalJpy)}</li>));
  if ((rec.seats || []).length) items.push(<li key="seats">{t("pub.summary.seats")}: {rec.seats.map((s) => s.seatId).join(", ")}</li>);
  return items.length ? <ul>{items}</ul> : null;
}
