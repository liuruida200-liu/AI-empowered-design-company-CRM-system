"""
seed.py — Populate production_capabilities with realistic data for a
Chinese design-and-print company.

Run once:
    python seed.py
"""

import asyncio
from db import SessionLocal, init_db, ProductionCapability
from sqlalchemy import select

CAPABILITIES = [
    # ── Outdoor Vinyl Banners ──────────────────────────────────────────────
    {
        "name": "Outdoor Vinyl Banner (Standard)",
        "description": (
            "Heavy-duty 440gsm PVC vinyl, UV-resistant inks, suitable for outdoor use "
            "up to 3 years. Ideal for storefronts, events, and construction site hoardings. "
            "Standard finish is matte; gloss available on request. Grommets every 50cm included."
        ),
        "material_type": "vinyl",
        "max_width_cm": 300.0,
        "max_height_cm": 600.0,
        "price_per_sqm": 85.0,
        "lead_time_days": 2,
        "notes": (
            "Minimum order 1 sqm. Hemmed edges standard. Wind slits available for large banners. "
            "Extra ¥8/sqm for gloss finish. Extra ¥15/sqm for blockout (double-sided)."
        ),
    },
    {
        "name": "Outdoor Vinyl Banner (Premium Cold Laminate)",
        "description": (
            "500gsm premium vinyl with additional 80-micron cold lamination for extra scratch "
            "and UV resistance. Rated 5-year outdoor lifespan. Best for long-term signage, "
            "vehicle wraps, and high-traffic areas. Vivid colour reproduction with CMYK + white ink."
        ),
        "material_type": "vinyl",
        "max_width_cm": 320.0,
        "max_height_cm": 500.0,
        "price_per_sqm": 135.0,
        "lead_time_days": 3,
        "notes": (
            "White ink base layer available for transparent or mesh substrates at extra ¥12/sqm. "
            "Suitable for curved surfaces. Anti-graffiti coating available ¥20/sqm extra."
        ),
    },

    # ── Indoor Fabric & Textile ────────────────────────────────────────────
    {
        "name": "Dye-Sublimation Fabric Banner",
        "description": (
            "High-definition sublimation printing on 110gsm polyester fabric. "
            "Produces vibrant, photo-quality images with soft texture. Machine washable. "
            "Ideal for trade show displays, retail backdrops, and pop-up stands. "
            "Lightweight and wrinkle-resistant — rolls up for easy transport."
        ),
        "material_type": "fabric",
        "max_width_cm": 320.0,
        "max_height_cm": 1000.0,
        "price_per_sqm": 110.0,
        "lead_time_days": 3,
        "notes": (
            "Tension frame systems available separately. Seamless joins for widths over 160cm. "
            "Double-sided printing available at 1.8× single-side price. "
            "Custom shapes (circles, cutouts) ¥30 setup fee."
        ),
    },
    {
        "name": "Backlit Fabric (SEG Lightbox)",
        "description": (
            "200gsm backlit polyester fabric designed for LED lightbox frames (SEG system). "
            "Translucent material allows even light diffusion with no hotspots. "
            "Colour-accurate printing optimised for backlit viewing. "
            "Popular for retail feature walls, airport displays, and exhibition stands."
        ),
        "material_type": "fabric",
        "max_width_cm": 300.0,
        "max_height_cm": 800.0,
        "price_per_sqm": 150.0,
        "lead_time_days": 4,
        "notes": (
            "SEG silicone edge included. Frame not included (available separately). "
            "Requires colour profile calibration for accurate backlit colours — "
            "please provide files in RGB not CMYK. Minimum width 50cm."
        ),
    },

    # ── Acrylic Signs ─────────────────────────────────────────────────────
    {
        "name": "Acrylic Sign — UV Flatbed Print (5mm)",
        "description": (
            "5mm clear or white acrylic sheet with direct UV flatbed printing. "
            "Scratch-resistant, high-gloss finish. Suitable for office signage, "
            "reception logos, menu boards, and decorative panels. "
            "Can be cut to custom shapes with CNC router."
        ),
        "material_type": "acrylic",
        "max_width_cm": 240.0,
        "max_height_cm": 120.0,
        "price_per_sqm": 320.0,
        "lead_time_days": 4,
        "notes": (
            "Drilling and countersunk holes ¥5 each. "
            "Standoff mounts (silver/black/gold) ¥18 each. "
            "Frosted acrylic +¥40/sqm. Mirror acrylic +¥60/sqm. "
            "CNC custom shape cutting ¥80 setup + ¥2/cm perimeter."
        ),
    },
    {
        "name": "Acrylic Sign — UV Flatbed Print (10mm)",
        "description": (
            "10mm thick acrylic for premium, high-impact signage. "
            "Excellent depth and clarity. Used for lobby signs, wayfinding, "
            "architectural features, and luxury retail displays. "
            "Polished edges standard."
        ),
        "material_type": "acrylic",
        "max_width_cm": 200.0,
        "max_height_cm": 100.0,
        "price_per_sqm": 480.0,
        "lead_time_days": 5,
        "notes": (
            "Edge polishing included. Back-painting available (solid colour) ¥30/sqm. "
            "3D layered acrylic assembly (stacked layers) priced on request. "
            "Maximum single piece 200×100cm due to sheet size limits."
        ),
    },

    # ── Foam Board & Correx ───────────────────────────────────────────────
    {
        "name": "Foam Board Print (5mm KT Board)",
        "description": (
            "Lightweight 5mm KT foam board with full-colour digital print on self-adhesive vinyl. "
            "Ideal for point-of-sale displays, presentations, exhibitions, and temporary signage. "
            "Easy to cut and mount. Not suitable for outdoor use."
        ),
        "material_type": "foam board",
        "max_width_cm": 120.0,
        "max_height_cm": 240.0,
        "price_per_sqm": 65.0,
        "lead_time_days": 1,
        "notes": (
            "Same-day production available for orders before 12:00pm. "
            "Easel backs ¥8 each. Rounded corners ¥5 per board. "
            "10mm foam board available at ¥95/sqm. Not waterproof."
        ),
    },
    {
        "name": "Corrugated Plastic (Correx) Board",
        "description": (
            "4mm twin-wall corrugated polypropylene (Correx) with UV-printed graphics. "
            "Lightweight, waterproof, and impact-resistant. "
            "Used for real estate signs, election boards, outdoor temporary displays, "
            "and construction hoardings. Recyclable material."
        ),
        "material_type": "correx",
        "max_width_cm": 120.0,
        "max_height_cm": 240.0,
        "price_per_sqm": 75.0,
        "lead_time_days": 2,
        "notes": (
            "H-stakes for ground mounting ¥12 each. "
            "Cable tie holes ¥2 each. "
            "Available in white, yellow, and blue base colours. "
            "Outdoor rated 6–12 months depending on UV exposure."
        ),
    },

    # ── Canvas & Fine Art Prints ──────────────────────────────────────────
    {
        "name": "Canvas Print (Stretched)",
        "description": (
            "400gsm artist-grade cotton canvas with pigment inkjet printing. "
            "Stretched over 38mm pine frame, ready to hang. "
            "Archival quality — 75+ year fade resistance under normal conditions. "
            "Popular for photo reproductions, corporate art, restaurants, and hotels."
        ),
        "material_type": "canvas",
        "max_width_cm": 200.0,
        "max_height_cm": 300.0,
        "price_per_sqm": 280.0,
        "lead_time_days": 3,
        "notes": (
            "Gallery wrap (image continues around edges) standard. "
            "Mirror wrap or solid colour border available. "
            "Hanging hardware included. "
            "Varnish coating (gloss/matte/satin) ¥25/sqm extra. "
            "Panoramic prints up to 200×100cm in single piece."
        ),
    },

    # ── UV Flatbed (Rigid Substrates) ─────────────────────────────────────
    {
        "name": "UV Flatbed Print — Aluminium Composite Panel (ACP)",
        "description": (
            "3mm aluminium composite panel (ACM/Dibond equivalent) with direct UV printing. "
            "Rigid, flat, weatherproof, and lightweight. "
            "Used for outdoor fascia signs, building directories, estate agent boards, "
            "and premium exhibition panels. Silver brushed or white face available."
        ),
        "material_type": "aluminium composite",
        "max_width_cm": 250.0,
        "max_height_cm": 125.0,
        "price_per_sqm": 260.0,
        "lead_time_days": 3,
        "notes": (
            "Routing/cutting to shape ¥80 setup + ¥2/cm. "
            "Folding/fabrication priced on request. "
            "Drill holes ¥5 each. "
            "5mm ACP available at ¥340/sqm. "
            "Anti-UV clear lacquer coat included for outdoor use."
        ),
    },
    {
        "name": "UV Flatbed Print — PVC Foam Board (Forex/Sintra)",
        "description": (
            "5mm expanded PVC (Forex) with direct UV flatbed print. "
            "Smooth surface, slightly flexible, easy to cut and mount. "
            "Suitable for indoor and short-term outdoor use. "
            "Lighter than aluminium composite. Used for displays, props, and retail."
        ),
        "material_type": "pvc foam",
        "max_width_cm": 200.0,
        "max_height_cm": 300.0,
        "price_per_sqm": 175.0,
        "lead_time_days": 2,
        "notes": (
            "10mm PVC foam at ¥240/sqm. "
            "White face standard; black core (Forex Black) +¥30/sqm. "
            "CNC cut shapes ¥60 setup + ¥1.5/cm perimeter. "
            "Not suitable for prolonged direct sunlight (warping risk)."
        ),
    },

    # ── Roll-Up & Display Systems ─────────────────────────────────────────
    {
        "name": "Roll-Up Banner Stand (Print Only)",
        "description": (
            "High-resolution print on 175-micron polyester film for roll-up/pull-up banner stands. "
            "Anti-curl base, scratch-resistant surface. "
            "Standard sizes: 85×200cm, 100×200cm, 120×200cm. "
            "Print only — stand sold separately or as package."
        ),
        "material_type": "polyester film",
        "max_width_cm": 150.0,
        "max_height_cm": 220.0,
        "price_per_sqm": 95.0,
        "lead_time_days": 1,
        "notes": (
            "85×200cm complete package (print + stand) ¥280. "
            "100×200cm complete package ¥320. "
            "Premium retractable stand upgrade ¥180 extra. "
            "Double-sided roll-up available — requires double-sided stand."
        ),
    },

    # ── Stickers & Labels ─────────────────────────────────────────────────
    {
        "name": "Vinyl Sticker / Cut Vinyl Lettering",
        "description": (
            "Cast vinyl (Oracal 651 equivalent) cut to shape by plotter cutter. "
            "Available in 60+ standard colours plus custom printed vinyl. "
            "Used for vehicle graphics, window lettering, wall decals, "
            "product labels, and promotional stickers. "
            "Outdoor rated 5–7 years."
        ),
        "material_type": "cut vinyl",
        "max_width_cm": 150.0,
        "max_height_cm": None,
        "price_per_sqm": 120.0,
        "lead_time_days": 1,
        "notes": (
            "Minimum charge ¥50. Application tape included. "
            "Printed vinyl (CMYK) at ¥180/sqm. "
            "Reflective vinyl at ¥220/sqm. "
            "Frosted window vinyl at ¥160/sqm. "
            "Installation service available — quote on request by location."
        ),
    },
    {
        "name": "Digital Printed Sticker (Sheet / Roll)",
        "description": (
            "Full-colour CMYK + white inkjet print on gloss or matte self-adhesive vinyl. "
            "Die-cut to any shape. "
            "Used for product packaging, promotional handouts, laptop stickers, "
            "and branded merchandise. Waterproof and fade-resistant."
        ),
        "material_type": "printed sticker",
        "max_width_cm": 60.0,
        "max_height_cm": None,
        "price_per_sqm": 200.0,
        "lead_time_days": 2,
        "notes": (
            "Minimum order ¥80. Kiss-cut or die-cut ¥30 setup fee. "
            "Clear vinyl substrate +¥20/sqm. "
            "Holographic vinyl +¥60/sqm. "
            "Bulk roll labels (1000+ units) — request special pricing."
        ),
    },

    # ── Window Graphics ───────────────────────────────────────────────────
    {
        "name": "Window Frosted / Etched Vinyl",
        "description": (
            "Translucent frosted vinyl film simulating sandblasted glass effect. "
            "Can be cut to custom patterns, logos, and text. "
            "Provides privacy while allowing light transmission. "
            "Used for office partitions, shopfront windows, and shower screens."
        ),
        "material_type": "window vinyl",
        "max_width_cm": 120.0,
        "max_height_cm": None,
        "price_per_sqm": 160.0,
        "lead_time_days": 2,
        "notes": (
            "One-way vision (perforated) vinyl for window graphics: ¥140/sqm. "
            "Full-colour printed frosted: ¥210/sqm. "
            "Anti-UV window film (heat reduction, no print): ¥90/sqm. "
            "Installation included within 30km of city centre."
        ),
    },

    # ── Large Format Wallpaper & Murals ───────────────────────────────────
    {
        "name": "Wall Mural — Self-Adhesive Wallpaper",
        "description": (
            "Photo-quality print on premium 180gsm self-adhesive non-woven wallpaper. "
            "Repositionable for up to 72 hours after application — ideal for rental spaces. "
            "Bubble-free application with air-release channels. "
            "Suitable for smooth painted walls, tiles, and glass surfaces."
        ),
        "material_type": "wallpaper",
        "max_width_cm": 320.0,
        "max_height_cm": 500.0,
        "price_per_sqm": 145.0,
        "lead_time_days": 3,
        "notes": (
            "Panels supplied in vertical strips for seamless installation. "
            "Installation service: ¥35/sqm within city. "
            "Textured wallpaper substrate (linen/canvas effect) +¥30/sqm. "
            "Paste-the-wall option for commercial installs ¥160/sqm."
        ),
    },

    # ── Vehicle Wraps ─────────────────────────────────────────────────────
    {
        "name": "Vehicle Wrap — Cast Vinyl (Full/Partial)",
        "description": (
            "3M or Avery Dennison cast vinyl wrap film with laminate. "
            "Conforms to complex curves without lifting or distortion. "
            "Vivid CMYK print with 7-year outdoor rating. "
            "Removable without paint damage (when installed correctly). "
            "Used for car branding, fleet graphics, and colour change wraps."
        ),
        "material_type": "vehicle wrap vinyl",
        "max_width_cm": 152.0,
        "max_height_cm": None,
        "price_per_sqm": 420.0,
        "lead_time_days": 5,
        "notes": (
            "Price includes print + laminate + installation labour for standard sedan. "
            "SUV/van surcharge ¥800–¥1,500 depending on size. "
            "Colour-change wrap (solid, no print): ¥280/sqm. "
            "Matte laminate, chrome, brushed metal finishes available. "
            "Vehicle must be clean and paint in good condition before wrapping."
        ),
    },

    # ── Event & Exhibition ────────────────────────────────────────────────
    {
        "name": "Step-and-Repeat Backdrop (Fabric)",
        "description": (
            "Seamless dye-sublimation fabric backdrop on telescopic aluminium frame. "
            "Standard sizes: 2×2m, 2×3m, 3×3m, 4×2m. "
            "Wrinkle-free fabric for photo and video backdrops. "
            "Quick 5-minute tool-free assembly. "
            "Used at press conferences, red carpet events, product launches."
        ),
        "material_type": "fabric",
        "max_width_cm": 500.0,
        "max_height_cm": 300.0,
        "price_per_sqm": 130.0,
        "lead_time_days": 3,
        "notes": (
            "Package includes print + frame + carry bag. "
            "2×2m complete: ¥680. 3×2m complete: ¥980. 4×2m complete: ¥1,280. "
            "Replacement print only (no frame): standard sqm rate. "
            "Curved (concave) backdrop frame surcharge ¥350."
        ),
    },
    {
        "name": "Exhibition Display Board (System Panel)",
        "description": (
            "Modular 1m×2.4m display panels for exhibition booth construction. "
            "Octanorm-compatible aluminium extrusion system. "
            "Panels printed on 5mm foam board or fabric face. "
            "Interlocking design allows custom booth configurations."
        ),
        "material_type": "exhibition panel",
        "max_width_cm": 100.0,
        "max_height_cm": 240.0,
        "price_per_sqm": 190.0,
        "lead_time_days": 4,
        "notes": (
            "Panel only (no frame): ¥190/sqm. "
            "Panel + frame system: ¥380/sqm. "
            "Lighting, shelving, and lockable counters available on request. "
            "Booth assembly service ¥800/day per technician."
        ),
    },

    # ── Specialty Printing ────────────────────────────────────────────────
    {
        "name": "Floor Graphics — Anti-Slip Laminate",
        "description": (
            "Full-colour print on 180-micron vinyl with R10-rated anti-slip over-laminate. "
            "Designed for temporary floor advertising in retail, exhibitions, and wayfinding. "
            "Certified safe for pedestrian traffic. "
            "Strong adhesive works on smooth concrete, tile, and vinyl floors."
        ),
        "material_type": "floor vinyl",
        "max_width_cm": 130.0,
        "max_height_cm": None,
        "price_per_sqm": 185.0,
        "lead_time_days": 2,
        "notes": (
            "Anti-slip certification (R10) included. "
            "Outdoor anti-slip (R11) +¥25/sqm. "
            "Removal without adhesive residue on most smooth surfaces. "
            "Not suitable for carpet or heavily textured surfaces. "
            "Max recommended single piece: 1.3×3m."
        ),
    },
    {
        "name": "Laser Engraving — Acrylic / Wood",
        "description": (
            "CO2 laser engraving and cutting on acrylic (clear, coloured, mirrored) "
            "or wood (MDF, plywood, bamboo). "
            "Precision up to 0.1mm. "
            "Used for awards, trophies, nameplates, decorative panels, "
            "architectural models, and branded gifts."
        ),
        "material_type": "laser engraving",
        "max_width_cm": 90.0,
        "max_height_cm": 60.0,
        "price_per_sqm": 380.0,
        "lead_time_days": 3,
        "notes": (
            "Minimum charge ¥120. Engraving depth: 0.5mm–3mm depending on material. "
            "Full cut-through available for acrylic up to 15mm thick. "
            "Engraved and filled with colour paint: +¥0.50/cm². "
            "3D relief engraving (layered depth): priced on request. "
            "File must be vector (AI, CDR, DXF) for cutting jobs."
        ),
    },
]


async def seed():
    await init_db()
    async with SessionLocal() as session:
        # Check if already seeded
        res = await session.execute(select(ProductionCapability))
        existing = res.scalars().all()
        if existing:
            print(f"Already seeded — {len(existing)} capabilities found. Skipping.")
            print("To re-seed, delete all rows from production_capabilities first:")
            print("  DELETE FROM production_capabilities;")
            return

        for cap_data in CAPABILITIES:
            session.add(ProductionCapability(**cap_data))

        await session.commit()
        print(f"✓ Seeded {len(CAPABILITIES)} production capabilities.")
        for c in CAPABILITIES:
            print(f"  - {c['name']} | {c['material_type']} | ¥{c['price_per_sqm']}/sqm | {c['lead_time_days']}d lead time")


if __name__ == "__main__":
    asyncio.run(seed())