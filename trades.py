"""
trades.py — v5.89.341

The itemized "Confirmed repairs" section groups findings by TRADE (the contractor a
buyer would actually call), not by the eight coarse risk categories. Under the
coarse buckets, caulking and rodent openings rendered as "Roof Exterior", attic
insulation and a fireplace switch as "Other Items", and a sprinkler drip line as
"Plumbing" priced like a supply-line repipe. A buyer reading that card either
argues with the label or with the number, and either way stops trusting it.

Every finding carries a `trade`. Claude assigns it from this list; the keyword
fallback derives it from the sentence. Each trade maps to ONE risk category (for
scoring, which still runs on the coarse buckets) and carries its own per-finding
national baseline cost by severity, so a loose escutcheon is priced as an
exterior-trim item, not as plumbing.

Baseline costs: 2026 national, per finding, (low, high) by severity, each cell held to
a ~1.7x spread around the midpoint (house rule: a buyer needs a believable number). Sources are
the same ones repair_cost_estimator cites (RSMeans residential repair, HomeAdvisor
/ Angi True Cost, HomeGuide). Metro multiplier is applied by the caller.
"""

# trade -> (label, risk category value, {severity: (low, high)})
TRADES = {
    'roof': ('Roof', 'roof_exterior',
             {'minor': (420, 720), 'moderate': (2120, 3580), 'major': (6920, 11700), 'critical': (14230, 24050)}),
    'exterior_walls_trim': ('Exterior Walls & Trim', 'roof_exterior',
             {'minor': (250, 420), 'moderate': (1000, 1690), 'major': (3650, 6180), 'critical': (10000, 16900)}),
    'gutters_drainage': ('Gutters, Grading & Drainage', 'roof_exterior',
             {'minor': (250, 420), 'moderate': (1000, 1690), 'major': (3650, 6180), 'critical': (8460, 14300)}),
    'windows_doors': ('Windows & Doors', 'general',
             {'minor': (210, 360), 'moderate': (770, 1300), 'major': (3080, 5200), 'critical': (8080, 13650)}),
    'attic_insulation': ('Attic & Insulation', 'general',
             {'minor': (420, 720), 'moderate': (1620, 2730), 'major': (3650, 6180), 'critical': (7120, 12020)}),
    'foundation_structure': ('Foundation & Structure', 'foundation_structure',
             {'minor': (880, 1500), 'moderate': (4420, 7480), 'major': (11920, 20150), 'critical': (28080, 47450)}),
    'plumbing': ('Plumbing', 'plumbing',
             {'minor': (270, 460), 'moderate': (1270, 2140), 'major': (4420, 7480), 'critical': (11540, 19500)}),
    'water_heater': ('Water Heater', 'plumbing',
             {'minor': (210, 360), 'moderate': (650, 1100), 'major': (1730, 2920), 'critical': (3080, 5200)}),
    'sewer_septic': ('Sewer & Septic', 'plumbing',
             {'minor': (420, 720), 'moderate': (2120, 3580), 'major': (6540, 11050), 'critical': (16150, 27300)}),
    'electrical': ('Electrical', 'electrical',
             {'minor': (210, 360), 'moderate': (920, 1560), 'major': (3270, 5520), 'critical': (8850, 14950)}),
    'hvac': ('Heating & Cooling', 'hvac_systems',
             {'minor': (270, 460), 'moderate': (1270, 2140), 'major': (4040, 6820), 'critical': (8460, 14300)}),
    'ventilation_fans': ('Ventilation & Exhaust Fans', 'hvac_systems',
             {'minor': (130, 230), 'moderate': (330, 550), 'major': (810, 1360), 'critical': (1730, 2920)}),
    'fireplace_chimney': ('Fireplace & Chimney', 'general',
             {'minor': (210, 360), 'moderate': (620, 1040), 'major': (2120, 3580), 'critical': (5380, 9100)}),
    'appliances': ('Appliances', 'general',
             {'minor': (150, 260), 'moderate': (460, 780), 'major': (1310, 2210), 'critical': (3270, 5520)}),
    'interior_finishes': ('Interior Walls, Ceilings & Floors', 'general',
             {'minor': (190, 320), 'moderate': (730, 1240), 'major': (2500, 4220), 'critical': (6540, 11050)}),
    'garage': ('Garage & Garage Door', 'general',
             {'minor': (150, 260), 'moderate': (460, 780), 'major': (1310, 2210), 'critical': (3270, 5520)}),
    'irrigation_grounds': ('Irrigation & Grounds', 'general',
             {'minor': (150, 260), 'moderate': (460, 780), 'major': (1310, 2210), 'critical': (3270, 5520)}),
    'pool_spa': ('Pool & Spa', 'general',
             {'minor': (310, 520), 'moderate': (1270, 2140), 'major': (4040, 6820), 'critical': (10770, 18200)}),
    'pest_wdi': ('Pests & Wood-Destroying Insects', 'environmental',
             {'minor': (270, 460), 'moderate': (920, 1560), 'major': (3270, 5520), 'critical': (8460, 14300)}),
    'environmental': ('Environmental (mold, asbestos, radon, lead)', 'environmental',
             {'minor': (420, 720), 'moderate': (2120, 3580), 'major': (6150, 10400), 'critical': (12310, 20800)}),
    'safety_devices': ('Smoke & CO Alarms, Safety Devices', 'electrical',
             {'minor': (100, 160), 'moderate': (310, 520), 'major': (810, 1360), 'critical': (2120, 3580)}),
    'permits_legal': ('Permits & Legal', 'legal_title',
             {'minor': (770, 1300), 'moderate': (2120, 3580), 'major': (5380, 9100), 'critical': (13460, 22750)}),
    'other': ('Other Items', 'general',
             {'minor': (190, 320), 'moderate': (770, 1300), 'major': (2500, 4220), 'critical': (6540, 11050)}),
}

TRADE_KEYS = list(TRADES.keys())

# Keyword fallback used by the rules parser and for AI output that omits `trade`.
# Order matters: first match wins, so the more specific trades come first.
_TRADE_KEYWORDS = [
    ('exterior_walls_trim', ['escutcheon', 'caulk', 'siding', 'fascia', 'soffit', 'eave', 'exterior wall']),
    ('water_heater',        ['water heater', 't&p valve', 't & p valve', 'tankless']),
    ('sewer_septic',        ['sewer', 'septic', 'main drain line', 'waste line', 'cleanout']),
    ('irrigation_grounds',  ['sprinkler', 'irrigation', 'drip tubing', 'drip line', 'landscape', 'fence', 'tree ', 'shrub']),
    ('ventilation_fans',    ['exhaust fan', 'bath fan', 'bathroom fan', 'vent fan', 'ventilation fan', 'dryer vent', 'dryer exhaust']),
    ('fireplace_chimney',   ['fireplace', 'chimney', 'firebox', 'damper', 'flue', 'gas log']),
    ('attic_insulation',    ['insulation', 'attic']),
    ('gutters_drainage',    ['gutter', 'downspout', 'grading', 'grade slopes', 'drainage', 'splash block']),
    ('roof',                ['roof', 'shingle', 'flashing', 'ridge', 'valley', 'skylight', 'roof covering', 'decking']),
    ('exterior_walls_trim', ['siding', 'trim', 'caulk', 'fascia', 'soffit', 'eave', 'brick', 'stucco', 'exterior wall',
                             'escutcheon', 'weep hole', 'lintel', 'exterior']),
    ('windows_doors',       ['window', 'door', 'thermal seal', 'weatherstrip', 'threshold', 'screen']),
    ('foundation_structure',['foundation', 'slab', 'pier', 'beam', 'joist', 'truss', 'rafter', 'structural', 'settlement',
                             'post-tension', 'post tension', 'crawlspace', 'crawl space']),
    ('safety_devices',      ['smoke alarm', 'smoke detector', 'carbon monoxide', 'co alarm', 'co detector', 'gfci', 'afci']),
    ('electrical',          ['electrical', 'wiring', 'panel', 'breaker', 'outlet', 'receptacle', 'circuit', 'grounding',
                             'bonding', 'light fixture', 'switch', 'ceiling fan']),
    ('hvac',                ['hvac', 'furnace', 'condenser', 'air condition', 'a/c', 'heat pump', 'thermostat', 'duct',
                             'refrigerant', 'evaporator', 'heating', 'cooling', 'temperature differential']),
    ('plumbing',            ['plumbing', 'pipe', 'faucet', 'toilet', 'sink', 'shower', 'tub', 'drain', 'supply line',
                             'valve', 'water pressure', 'hose bib', 'leak']),
    ('appliances',          ['dishwasher', 'disposal', 'disposer', 'range', 'oven', 'cooktop', 'microwave', 'range hood',
                             'appliance', 'refrigerator', 'washer', 'dryer']),
    ('garage',              ['garage door', 'garage opener', 'door operator', 'garage']),
    ('pool_spa',            ['pool', 'spa', 'hot tub', 'whirlpool', 'hydro-massage', 'hydromassage']),
    ('pest_wdi',            ['termite', 'wood destroying', 'wdi', 'rodent', 'pest', 'infestation', 'carpenter ant']),
    ('environmental',       ['mold', 'mildew', 'asbestos', 'radon', 'lead paint', 'lead-based']),
    ('interior_finishes',   ['drywall', 'ceiling', 'floor', 'flooring', 'tile', 'grout', 'carpet', 'paint', 'cabinet',
                             'countertop', 'stair', 'handrail', 'guardrail', 'interior wall']),
    ('permits_legal',       ['permit', 'unpermitted', 'code violation', 'easement']),
]


CATEGORY_TO_TRADE = {
    'foundation': 'foundation_structure', 'foundation_structure': 'foundation_structure',
    'roof': 'roof', 'roof_exterior': 'roof', 'exterior': 'exterior_walls_trim',
    'plumbing': 'plumbing', 'electrical': 'electrical', 'hvac': 'hvac', 'hvac_systems': 'hvac',
    'environmental': 'environmental', 'water_damage': 'interior_finishes', 'pest': 'pest_wdi',
    'permits': 'permits_legal', 'legal_title': 'permits_legal', 'safety': 'safety_devices',
    'insurance_hoa': 'permits_legal', 'general': 'other',
}


def trade_for_category(category: str) -> str:
    """Coarse category -> the trade that best represents it (used when a finding has
    no trade and its text gives no clue)."""
    return CATEGORY_TO_TRADE.get(str(category or '').strip().lower(), 'other')


def trade_for_text(text: str) -> str:
    """Best-effort trade from a finding sentence. Returns 'other' when nothing matches."""
    t = (text or '').lower()
    for trade, words in _TRADE_KEYWORDS:
        if any(w in t for w in words):
            return trade
    return 'other'


def normalize_trade(value) -> str:
    """Accept anything the model or a legacy row might send; return a key from TRADES."""
    v = str(value or '').strip().lower().replace(' ', '_').replace('&', 'and').replace('/', '_')
    if v in TRADES:
        return v
    aliases = {
        'exterior': 'exterior_walls_trim', 'siding': 'exterior_walls_trim', 'walls': 'exterior_walls_trim',
        'insulation': 'attic_insulation', 'attic': 'attic_insulation',
        'gutters': 'gutters_drainage', 'drainage': 'gutters_drainage', 'grading': 'gutters_drainage',
        'fans': 'ventilation_fans', 'ventilation': 'ventilation_fans', 'exhaust_fans': 'ventilation_fans',
        'fireplace': 'fireplace_chimney', 'chimney': 'fireplace_chimney',
        'irrigation': 'irrigation_grounds', 'sprinklers': 'irrigation_grounds', 'sprinkler': 'irrigation_grounds',
        'grounds': 'irrigation_grounds', 'landscaping': 'irrigation_grounds',
        'heating_and_cooling': 'hvac', 'hvac_systems': 'hvac', 'ac': 'hvac',
        'foundation': 'foundation_structure', 'structure': 'foundation_structure', 'structural': 'foundation_structure',
        'sewer': 'sewer_septic', 'septic': 'sewer_septic',
        'pest': 'pest_wdi', 'termites': 'pest_wdi', 'wdi': 'pest_wdi',
        'safety': 'safety_devices', 'alarms': 'safety_devices',
        'permits': 'permits_legal', 'legal': 'permits_legal', 'legal_title': 'permits_legal',
        'interior': 'interior_finishes', 'finishes': 'interior_finishes',
        'pool': 'pool_spa', 'spa': 'pool_spa',
        'general': 'other', 'misc': 'other', 'miscellaneous': 'other',
    }
    return aliases.get(v, '')


def trade_label(trade: str) -> str:
    return TRADES.get(trade, TRADES['other'])[0]


def trade_category(trade: str) -> str:
    """Risk-category value (IssueCategory.value) that this trade rolls up into."""
    return TRADES.get(trade, TRADES['other'])[1]


def trade_cost(trade: str, severity: str):
    """(low, high) national per-finding baseline for a trade at a severity."""
    table = TRADES.get(trade, TRADES['other'])[2]
    sev = (severity or 'moderate').lower()
    if sev == 'informational':
        return 0.0, 0.0
    low, high = table.get(sev, table['moderate'])
    return float(low), float(high)
