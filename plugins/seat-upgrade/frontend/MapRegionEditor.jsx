import { useRef, useState } from "react";

// Lets an admin draw one rectangular hotspot for the tier currently being
// edited by dragging on the event's seat map image — stored as fractions
// (0-1) of the image's own box, matching seat_upgrade_logic.py's
// _normalize_region server-side. Every other tier's saved region renders
// dimmed underneath for spatial context, so zones don't end up
// overlapping by accident. SeatUpgrade.jsx renders the same fractions as
// clickable hotspots on the registrant-facing page.
export default function MapRegionEditor({ imageUrl, region, onChange, otherTiers = [], activeLabel }) {
  const containerRef = useRef(null);
  const [dragStart, setDragStart] = useState(null); // {x,y} fraction, from pointerdown

  // Rounded to 4 decimal places (0.01% of the image) — plenty of precision
  // for a hotspot, and avoids storing/transmitting float noise like
  // 0.49999999999999994 from a plain subtraction.
  function round(value) {
    return Math.round(value * 10000) / 10000;
  }

  function pointToFraction(event) {
    const rect = containerRef.current.getBoundingClientRect();
    const x = round(Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1));
    const y = round(Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1));
    return { x, y };
  }

  function handlePointerDown(event) {
    event.preventDefault();
    // Don't clear the existing region here — only once a drag actually
    // moves (below). A plain click with no movement (a misclick, or just
    // releasing outside the box) should leave whatever was already drawn
    // alone rather than silently wiping it.
    setDragStart(pointToFraction(event));
  }

  function handlePointerMove(event) {
    if (!dragStart) return;
    const point = pointToFraction(event);
    const x = Math.min(dragStart.x, point.x);
    const y = Math.min(dragStart.y, point.y);
    const width = round(Math.abs(point.x - dragStart.x));
    const height = round(Math.abs(point.y - dragStart.y));
    if (width < 0.01 || height < 0.01) return; // ignore sub-pixel jitter
    onChange({ x, y, width, height });
  }

  function handlePointerUp() {
    setDragStart(null);
  }

  if (!imageUrl) {
    return (
      <p className="field-note">
        Upload a seat map image on this event's Events form first — the region editor needs it to draw on.
      </p>
    );
  }

  return (
    <div>
      <div
        ref={containerRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerLeave={handlePointerUp}
        style={{
          position: "relative",
          userSelect: "none",
          touchAction: "none",
          cursor: "crosshair",
          border: "1px solid var(--border)",
          borderRadius: "6px",
          overflow: "hidden",
          lineHeight: 0,
        }}
      >
        <img src={imageUrl} alt="Seat map" style={{ display: "block", width: "100%" }} draggable={false} />
        {otherTiers
          .filter((tier) => tier.mapRegion)
          .map((tier) => (
            <div
              key={tier.tierId}
              title={tier.name}
              style={{
                position: "absolute",
                left: `${tier.mapRegion.x * 100}%`,
                top: `${tier.mapRegion.y * 100}%`,
                width: `${tier.mapRegion.width * 100}%`,
                height: `${tier.mapRegion.height * 100}%`,
                border: "1px dashed rgba(93,107,101,0.7)",
                background: "rgba(93,107,101,0.10)",
                pointerEvents: "none",
              }}
            />
          ))}
        {region ? (
          <div
            style={{
              position: "absolute",
              left: `${region.x * 100}%`,
              top: `${region.y * 100}%`,
              width: `${region.width * 100}%`,
              height: `${region.height * 100}%`,
              border: "2px solid #3d7258",
              background: "rgba(61,114,88,0.18)",
              pointerEvents: "none",
            }}
          />
        ) : null}
      </div>
      <div className="btn-row" style={{ marginTop: "8px", alignItems: "center" }}>
        <span className="field-note" style={{ flex: 1, margin: 0 }}>
          {region
            ? `${activeLabel || "This zone"}'s hotspot is set — drag on the image to redraw it.`
            : `Drag on the image to draw ${activeLabel || "this zone"}'s clickable area (optional).`}
        </span>
        {region ? (
          <button type="button" className="button secondary" onClick={() => onChange(null)}>
            Clear Region
          </button>
        ) : null}
      </div>
    </div>
  );
}
