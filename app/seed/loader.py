import json, pathlib
from datetime import date
from ..db import SessionLocal, Country, Pathway, Organization, ResearchSource

SEED = pathlib.Path(__file__).with_name("countries.json")


def upsert_country(s, iso2, data, destination, active):
    c = s.get(Country, iso2) or Country(iso2=iso2)
    c.iso3, c.name = data["iso3"], data["name"]
    c.is_destination = c.is_destination or destination
    c.is_origin = c.is_origin or not destination
    c.config = data.get("config", c.config or {})
    c.active = active if c.active is None else (c.active or active)
    s.merge(c)


def seed():
    d = json.loads(SEED.read_text())
    with SessionLocal() as s:
        if s.query(Country).count():
            return
        for iso2, c in d["destinations"].items():
            upsert_country(s, iso2, c, True, True)
            cfg = c["config"]
            s.add(Organization(name=cfg["authority"], category="government", country=iso2,
                               website=cfg["official_sites"][0], source_urls=cfg["official_sites"],
                               status="UNVERIFIED", notes="Seeded from configuration; verify via official sources."))
            for u in cfg["official_sites"]:
                s.add(ResearchSource(country=iso2, title=f"{cfg['authority']} (official)", url=u, level=1, policy_area="general"))
            for p in c["pathways"]:
                s.merge(Pathway(**{k: v for k, v in p.items() if k != "last_verified"}))
        for iso2, o in d["origins"].items():
            ex = s.get(Country, iso2)
            if not ex:
                s.add(Country(iso2=iso2, iso3=o["iso3"], name=o["name"], is_origin=True, active=o["active"]))
        s.commit()
