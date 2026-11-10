import { Check, DateTimeInput, Input } from "./adminFields";
import { t } from "./i18n";

// Admin tab: switch the optional add-ons on per event and configure them.
// Everything is edited in the event draft and saved with the normal Save button; the server validates it, assigns
// ids to new entries and refuses to enable an add-on that has nothing to offer.

const newId = () => (globalThis.crypto?.randomUUID?.() || Math.random().toString(16).slice(2)).replace(/-/g, "").slice(0, 8);
const seatCount = (section) => (Number(section.rows) || 0) * (Number(section.seatsPerRow) || 0);

function Row({ children, onRemove, label, readOnly }) {
  return (
    <div className="ep-card ep-addon-row" role="group" aria-label={label}>
      <div className="field-row">{children}</div>
      {!readOnly && <button type="button" className="button secondary" onClick={onRemove}>{t("events.addons.remove")}</button>}
    </div>
  );
}

export default function EventAddons({ draft, set, readOnly, errors }) {
  const enabled = (key) => !!draft.capabilities?.[key]?.enabled;
  const toggle = (key) => (value) => set("capabilities", { ...(draft.capabilities || {}), [key]: { enabled: value } });
  const slots = draft.slots || [];
  const upgrades = draft.upgrades || [];
  const sections = draft.seatMap?.sections || [];
  const edit = (list, i, patch) => list.map((item, n) => (n === i ? { ...item, ...patch } : item));
  const err = (key) => errors[key];

  return (
    <fieldset disabled={readOnly} className="ep-fieldset ep-addons">
      <legend>{t("events.tab.addons")}</legend>
      <p className="ep-note">{t("events.addons.intro")}</p>

      <section aria-labelledby="ad-slots-h" className="ep-addon">
        <h3 id="ad-slots-h">{t("events.addons.slots.title")}</h3>
        <p className="ep-note">{t("events.addons.slots.help")}</p>
        <Check id="ad-slots-on" label={t("events.addons.enable")} checked={enabled("entrySlots")} onChange={toggle("entrySlots")} />
        {err("capabilities.entrySlots") && <span className="field-error" role="alert">{err("capabilities.entrySlots")}</span>}
        {err("slots") && <span className="field-error" role="alert">{err("slots")}</span>}
        {slots.map((slot, i) => (
          <Row key={slot.slotId || `new-${i}`} label={slot.name || t("events.addons.slots.new")} readOnly={readOnly}
            onRemove={() => set("slots", slots.filter((_, n) => n !== i))}>
            <Input id={`slot-${i}-name`} label={t("events.addons.name")} value={slot.name} onChange={(v) => set("slots", edit(slots, i, { name: v }))} />
            <DateTimeInput id={`slot-${i}-start`} label={t("events.addons.starts")} value={slot.startsAt} onChange={(v) => set("slots", edit(slots, i, { startsAt: v }))} />
            <DateTimeInput id={`slot-${i}-end`} label={t("events.addons.ends")} value={slot.endsAt} onChange={(v) => set("slots", edit(slots, i, { endsAt: v }))} />
            <Input id={`slot-${i}-cap`} label={t("events.addons.capacity")} type="number" min="1" step="1" value={slot.capacity}
              onChange={(v) => set("slots", edit(slots, i, { capacity: v === "" ? "" : Number(v) }))} error={err(`slots[${i}]`)} />
          </Row>
        ))}
        {!readOnly && slots.length < 50 && (
          <button type="button" className="button secondary" onClick={() => set("slots", [...slots, { slotId: newId(), name: "", capacity: 20 }])}>
            {t("events.addons.slots.add")}
          </button>
        )}
      </section>

      <section aria-labelledby="ad-up-h" className="ep-addon">
        <h3 id="ad-up-h">{t("events.addons.upgrades.title")}</h3>
        <p className="ep-note">{t("events.addons.upgrades.help")}</p>
        <Check id="ad-up-on" label={t("events.addons.enable")} checked={enabled("zoneUpgrade")} onChange={toggle("zoneUpgrade")} />
        {err("capabilities.zoneUpgrade") && <span className="field-error" role="alert">{err("capabilities.zoneUpgrade")}</span>}
        {err("upgrades") && <span className="field-error" role="alert">{err("upgrades")}</span>}
        {upgrades.map((up, i) => (
          <Row key={up.upgradeId || `new-${i}`} label={up.name || t("events.addons.upgrades.new")} readOnly={readOnly}
            onRemove={() => set("upgrades", upgrades.filter((_, n) => n !== i))}>
            <Input id={`up-${i}-name`} label={t("events.addons.name")} value={up.name} onChange={(v) => set("upgrades", edit(upgrades, i, { name: v }))} />
            <Input id={`up-${i}-desc`} label={t("events.addons.description")} value={up.description || ""} onChange={(v) => set("upgrades", edit(upgrades, i, { description: v }))} />
            <Input id={`up-${i}-price`} label={t("events.addons.price")} type="number" min="0" step="1" value={up.priceJpy}
              onChange={(v) => set("upgrades", edit(upgrades, i, { priceJpy: v === "" ? "" : Number(v) }))} error={err(`upgrades[${i}]`)} />
            <Input id={`up-${i}-limit`} label={t("events.addons.limit")} type="number" min="1" step="1" value={up.limit ?? ""} help={t("events.addons.limitHelp")}
              onChange={(v) => set("upgrades", edit(upgrades, i, { limit: v === "" ? null : Number(v) }))} />
          </Row>
        ))}
        {!readOnly && upgrades.length < 20 && (
          <button type="button" className="button secondary" onClick={() => set("upgrades", [...upgrades, { upgradeId: newId(), name: "", priceJpy: 0 }])}>
            {t("events.addons.upgrades.add")}
          </button>
        )}
      </section>

      <section aria-labelledby="ad-seat-h" className="ep-addon">
        <h3 id="ad-seat-h">{t("events.addons.seats.title")}</h3>
        <p className="ep-note">{t("events.addons.seats.help")}</p>
        <Check id="ad-seat-on" label={t("events.addons.enable")} checked={enabled("seatReservation")} onChange={toggle("seatReservation")} />
        {err("capabilities.seatReservation") && <span className="field-error" role="alert">{err("capabilities.seatReservation")}</span>}
        {err("seatMap") && <span className="field-error" role="alert">{err("seatMap")}</span>}
        {sections.map((sec, i) => {
          const patch = (p) => set("seatMap", { sections: edit(sections, i, p) });
          return (
            <Row key={sec.sectionId || `new-${i}`} label={sec.name || t("events.addons.seats.new")} readOnly={readOnly}
              onRemove={() => set("seatMap", { sections: sections.filter((_, n) => n !== i) })}>
              <Input id={`sec-${i}-name`} label={t("events.addons.name")} value={sec.name} onChange={(v) => patch({ name: v })} />
              <Input id={`sec-${i}-rows`} label={t("events.addons.rows")} type="number" min="1" max="26" step="1" value={sec.rows} onChange={(v) => patch({ rows: v === "" ? "" : Number(v) })} />
              <Input id={`sec-${i}-per`} label={t("events.addons.seatsPerRow")} type="number" min="1" max="60" step="1" value={sec.seatsPerRow} onChange={(v) => patch({ seatsPerRow: v === "" ? "" : Number(v) })} />
              <Input id={`sec-${i}-extra`} label={t("events.addons.extraPrice")} type="number" min="0" step="1" value={sec.extraPriceJpy ?? 0}
                onChange={(v) => patch({ extraPriceJpy: v === "" ? "" : Number(v) })} error={err(`seatMap.sections[${i}]`)} help={t("events.addons.seatCount", { n: seatCount(sec) })} />
            </Row>
          );
        })}
        {!readOnly && sections.length < 20 && (
          <button type="button" className="button secondary"
            onClick={() => set("seatMap", { sections: [...sections, { sectionId: newId().slice(0, 6), name: "", rows: 5, seatsPerRow: 10, extraPriceJpy: 0 }] })}>
            {t("events.addons.seats.add")}
          </button>
        )}
      </section>
    </fieldset>
  );
}
