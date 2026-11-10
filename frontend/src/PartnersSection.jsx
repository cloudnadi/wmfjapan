import { useEffect, useState } from "react";
import { getPublicPartners } from "./api";

/**
 * Public "Our Partners" strip. Fetches the active partners from the public
 * /partners endpoint and renders them as a row of logo/name cards. Renders
 * nothing while loading or when there are no partners, so it can be dropped
 * onto any page without leaving an empty heading behind.
 */
export default function PartnersSection({ title = "Our Partners" }) {
  const [partners, setPartners] = useState([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let active = true;
    getPublicPartners()
      .then((list) => {
        if (active) setPartners(Array.isArray(list) ? list : []);
      })
      .finally(() => {
        if (active) setLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  if (!loaded || partners.length === 0) {
    return null;
  }

  return (
    <section className="partners-section" aria-label={title}>
      <h2 className="partners-section-title">{title}</h2>
      <div className="partners-strip">
        {partners.map((partner) => {
          const card = (
            <>
              {partner.logoUrl ? (
                <div className="partners-strip-logo">
                  <img src={partner.logoUrl} alt={`${partner.name} logo`} loading="lazy" />
                </div>
              ) : null}
              <span className="partners-strip-name">{partner.name}</span>
              {partner.description ? (
                <span className="partners-strip-desc">{partner.description}</span>
              ) : null}
            </>
          );

          return partner.websiteUrl ? (
            <a
              key={partner.partnerId}
              className="partners-strip-item"
              href={partner.websiteUrl}
              target="_blank"
              rel="noreferrer"
            >
              {card}
            </a>
          ) : (
            <div key={partner.partnerId} className="partners-strip-item">
              {card}
            </div>
          );
        })}
      </div>
    </section>
  );
}
