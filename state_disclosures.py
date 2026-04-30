"""
OfferWise State Disclosure Intelligence (v5.62.85)
====================================================
Provides state-specific disclosure requirements, legal context, and form
detection for all 50 US states + DC.

Every state has different disclosure laws. California requires TDS (Transfer
Disclosure Statement). Texas uses TREC Seller's Disclosure. Florida allows
AS-IS contracts with limited disclosure. This module gives OfferWise the
intelligence to provide state-appropriate analysis for any ZIP code in the USA.

Architecture:
    detect_state_from_text(text) -> state_code
    detect_state_from_zip(zip_code) -> state_code
    get_state_context(state_code) -> StateDisclosureContext
    get_state_legal_notes(state_code) -> list[str]
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ── ZIP Code → State Mapping ─────────────────────────────────────────
# First 3 digits of ZIP to state code. Covers all US ZIPs.

ZIP3_TO_STATE = {
    # Alabama
    '350': 'AL', '351': 'AL', '352': 'AL', '354': 'AL', '355': 'AL',
    '356': 'AL', '357': 'AL', '358': 'AL', '359': 'AL', '360': 'AL',
    '361': 'AL', '362': 'AL', '363': 'AL', '364': 'AL', '365': 'AL',
    '366': 'AL', '367': 'AL', '368': 'AL', '369': 'AL',
    # Alaska
    '995': 'AK', '996': 'AK', '997': 'AK', '998': 'AK', '999': 'AK',
    # Arizona
    '850': 'AZ', '851': 'AZ', '852': 'AZ', '853': 'AZ', '855': 'AZ',
    '856': 'AZ', '857': 'AZ', '859': 'AZ', '860': 'AZ', '863': 'AZ',
    '864': 'AZ', '865': 'AZ',
    # Arkansas
    '716': 'AR', '717': 'AR', '718': 'AR', '719': 'AR', '720': 'AR',
    '721': 'AR', '722': 'AR', '723': 'AR', '724': 'AR', '725': 'AR',
    '726': 'AR', '727': 'AR', '728': 'AR', '729': 'AR',
    # California
    '900': 'CA', '901': 'CA', '902': 'CA', '903': 'CA', '904': 'CA',
    '905': 'CA', '906': 'CA', '907': 'CA', '908': 'CA', '910': 'CA',
    '911': 'CA', '912': 'CA', '913': 'CA', '914': 'CA', '915': 'CA',
    '916': 'CA', '917': 'CA', '918': 'CA', '919': 'CA', '920': 'CA',
    '921': 'CA', '922': 'CA', '923': 'CA', '924': 'CA', '925': 'CA',
    '926': 'CA', '927': 'CA', '928': 'CA', '930': 'CA', '931': 'CA',
    '932': 'CA', '933': 'CA', '934': 'CA', '935': 'CA', '936': 'CA',
    '937': 'CA', '938': 'CA', '939': 'CA', '940': 'CA', '941': 'CA',
    '942': 'CA', '943': 'CA', '944': 'CA', '945': 'CA', '946': 'CA',
    '947': 'CA', '948': 'CA', '949': 'CA', '950': 'CA', '951': 'CA',
    '952': 'CA', '953': 'CA', '954': 'CA', '955': 'CA', '956': 'CA',
    '957': 'CA', '958': 'CA', '959': 'CA', '960': 'CA', '961': 'CA',
    # Colorado
    '800': 'CO', '801': 'CO', '802': 'CO', '803': 'CO', '804': 'CO',
    '805': 'CO', '806': 'CO', '807': 'CO', '808': 'CO', '809': 'CO',
    '810': 'CO', '811': 'CO', '812': 'CO', '813': 'CO', '814': 'CO',
    '815': 'CO', '816': 'CO',
    # Connecticut
    '060': 'CT', '061': 'CT', '062': 'CT', '063': 'CT', '064': 'CT',
    '065': 'CT', '066': 'CT', '067': 'CT', '068': 'CT', '069': 'CT',
    # Delaware
    '197': 'DE', '198': 'DE', '199': 'DE',
    # DC
    '200': 'DC', '202': 'DC', '203': 'DC', '204': 'DC', '205': 'DC',
    # Florida
    '320': 'FL', '321': 'FL', '322': 'FL', '323': 'FL', '324': 'FL',
    '325': 'FL', '326': 'FL', '327': 'FL', '328': 'FL', '329': 'FL',
    '330': 'FL', '331': 'FL', '332': 'FL', '333': 'FL', '334': 'FL',
    '335': 'FL', '336': 'FL', '337': 'FL', '338': 'FL', '339': 'FL',
    '340': 'FL', '341': 'FL', '342': 'FL', '344': 'FL', '346': 'FL',
    '347': 'FL', '349': 'FL',
    # Georgia
    '300': 'GA', '301': 'GA', '302': 'GA', '303': 'GA', '304': 'GA',
    '305': 'GA', '306': 'GA', '307': 'GA', '308': 'GA', '309': 'GA',
    '310': 'GA', '311': 'GA', '312': 'GA', '313': 'GA', '314': 'GA',
    '315': 'GA', '316': 'GA', '317': 'GA', '318': 'GA', '319': 'GA',
    '398': 'GA', '399': 'GA',
    # Hawaii
    '967': 'HI', '968': 'HI',
    # Idaho
    '832': 'ID', '833': 'ID', '834': 'ID', '835': 'ID', '836': 'ID', '837': 'ID', '838': 'ID',
    # Illinois
    '600': 'IL', '601': 'IL', '602': 'IL', '603': 'IL', '604': 'IL',
    '605': 'IL', '606': 'IL', '607': 'IL', '608': 'IL', '609': 'IL',
    '610': 'IL', '611': 'IL', '612': 'IL', '613': 'IL', '614': 'IL',
    '615': 'IL', '616': 'IL', '617': 'IL', '618': 'IL', '619': 'IL',
    '620': 'IL', '622': 'IL', '623': 'IL', '624': 'IL', '625': 'IL',
    '626': 'IL', '627': 'IL', '628': 'IL', '629': 'IL',
    # Indiana
    '460': 'IN', '461': 'IN', '462': 'IN', '463': 'IN', '464': 'IN',
    '465': 'IN', '466': 'IN', '467': 'IN', '468': 'IN', '469': 'IN',
    '470': 'IN', '471': 'IN', '472': 'IN', '473': 'IN', '474': 'IN',
    '475': 'IN', '476': 'IN', '477': 'IN', '478': 'IN', '479': 'IN',
    # Iowa
    '500': 'IA', '501': 'IA', '502': 'IA', '503': 'IA', '504': 'IA',
    '505': 'IA', '506': 'IA', '507': 'IA', '508': 'IA', '509': 'IA',
    '510': 'IA', '511': 'IA', '512': 'IA', '513': 'IA', '514': 'IA',
    '515': 'IA', '516': 'IA', '520': 'IA', '521': 'IA', '522': 'IA',
    '523': 'IA', '524': 'IA', '525': 'IA', '526': 'IA', '527': 'IA', '528': 'IA',
    # Kansas
    '660': 'KS', '661': 'KS', '662': 'KS', '664': 'KS', '665': 'KS',
    '666': 'KS', '667': 'KS', '668': 'KS', '669': 'KS', '670': 'KS',
    '671': 'KS', '672': 'KS', '673': 'KS', '674': 'KS', '675': 'KS',
    '676': 'KS', '677': 'KS', '678': 'KS', '679': 'KS',
    # Kentucky
    '400': 'KY', '401': 'KY', '402': 'KY', '403': 'KY', '404': 'KY',
    '405': 'KY', '406': 'KY', '407': 'KY', '408': 'KY', '409': 'KY',
    '410': 'KY', '411': 'KY', '412': 'KY', '413': 'KY', '414': 'KY',
    '415': 'KY', '416': 'KY', '417': 'KY', '418': 'KY',
    # Louisiana
    '700': 'LA', '701': 'LA', '703': 'LA', '704': 'LA', '705': 'LA',
    '706': 'LA', '707': 'LA', '708': 'LA', '710': 'LA', '711': 'LA',
    '712': 'LA', '713': 'LA', '714': 'LA',
    # Maine
    '039': 'ME', '040': 'ME', '041': 'ME', '042': 'ME', '043': 'ME', '044': 'ME', '045': 'ME', '046': 'ME', '047': 'ME', '048': 'ME', '049': 'ME',
    # Maryland
    '206': 'MD', '207': 'MD', '208': 'MD', '209': 'MD', '210': 'MD',
    '211': 'MD', '212': 'MD', '214': 'MD', '215': 'MD', '216': 'MD', '217': 'MD', '218': 'MD', '219': 'MD',
    # Massachusetts
    '010': 'MA', '011': 'MA', '012': 'MA', '013': 'MA', '014': 'MA',
    '015': 'MA', '016': 'MA', '017': 'MA', '018': 'MA', '019': 'MA',
    '020': 'MA', '021': 'MA', '022': 'MA', '023': 'MA', '024': 'MA', '025': 'MA', '026': 'MA', '027': 'MA',
    # Michigan
    '480': 'MI', '481': 'MI', '482': 'MI', '483': 'MI', '484': 'MI',
    '485': 'MI', '486': 'MI', '487': 'MI', '488': 'MI', '489': 'MI',
    '490': 'MI', '491': 'MI', '492': 'MI', '493': 'MI', '494': 'MI',
    '495': 'MI', '496': 'MI', '497': 'MI', '498': 'MI', '499': 'MI',
    # Minnesota
    '550': 'MN', '551': 'MN', '553': 'MN', '554': 'MN', '555': 'MN',
    '556': 'MN', '557': 'MN', '558': 'MN', '559': 'MN', '560': 'MN',
    '561': 'MN', '562': 'MN', '563': 'MN', '564': 'MN', '565': 'MN', '566': 'MN', '567': 'MN',
    # Mississippi
    '386': 'MS', '387': 'MS', '388': 'MS', '389': 'MS', '390': 'MS',
    '391': 'MS', '392': 'MS', '393': 'MS', '394': 'MS', '395': 'MS', '396': 'MS', '397': 'MS',
    # Missouri
    '630': 'MO', '631': 'MO', '633': 'MO', '634': 'MO', '635': 'MO',
    '636': 'MO', '637': 'MO', '638': 'MO', '639': 'MO', '640': 'MO',
    '641': 'MO', '644': 'MO', '645': 'MO', '646': 'MO', '647': 'MO',
    '648': 'MO', '649': 'MO', '650': 'MO', '651': 'MO', '652': 'MO',
    '653': 'MO', '654': 'MO', '655': 'MO', '656': 'MO', '657': 'MO', '658': 'MO',
    # Montana
    '590': 'MT', '591': 'MT', '592': 'MT', '593': 'MT', '594': 'MT', '595': 'MT', '596': 'MT', '597': 'MT', '598': 'MT', '599': 'MT',
    # Nebraska
    '680': 'NE', '681': 'NE', '683': 'NE', '684': 'NE', '685': 'NE',
    '686': 'NE', '687': 'NE', '688': 'NE', '689': 'NE', '690': 'NE', '691': 'NE', '692': 'NE', '693': 'NE',
    # Nevada
    '889': 'NV', '890': 'NV', '891': 'NV', '893': 'NV', '894': 'NV', '895': 'NV', '897': 'NV', '898': 'NV',
    # New Hampshire
    '030': 'NH', '031': 'NH', '032': 'NH', '033': 'NH', '034': 'NH', '036': 'NH', '037': 'NH', '038': 'NH',
    # New Jersey
    '070': 'NJ', '071': 'NJ', '072': 'NJ', '073': 'NJ', '074': 'NJ',
    '075': 'NJ', '076': 'NJ', '077': 'NJ', '078': 'NJ', '079': 'NJ',
    '080': 'NJ', '081': 'NJ', '082': 'NJ', '083': 'NJ', '084': 'NJ',
    '085': 'NJ', '086': 'NJ', '087': 'NJ', '088': 'NJ', '089': 'NJ',
    # New Mexico
    '870': 'NM', '871': 'NM', '872': 'NM', '873': 'NM', '874': 'NM',
    '875': 'NM', '877': 'NM', '878': 'NM', '879': 'NM', '880': 'NM',
    '881': 'NM', '882': 'NM', '883': 'NM', '884': 'NM',
    # New York
    '100': 'NY', '101': 'NY', '102': 'NY', '103': 'NY', '104': 'NY',
    '105': 'NY', '106': 'NY', '107': 'NY', '108': 'NY', '109': 'NY',
    '110': 'NY', '111': 'NY', '112': 'NY', '113': 'NY', '114': 'NY',
    '115': 'NY', '116': 'NY', '117': 'NY', '118': 'NY', '119': 'NY',
    '120': 'NY', '121': 'NY', '122': 'NY', '123': 'NY', '124': 'NY',
    '125': 'NY', '126': 'NY', '127': 'NY', '128': 'NY', '129': 'NY',
    '130': 'NY', '131': 'NY', '132': 'NY', '133': 'NY', '134': 'NY',
    '135': 'NY', '136': 'NY', '137': 'NY', '138': 'NY', '139': 'NY',
    '140': 'NY', '141': 'NY', '142': 'NY', '143': 'NY', '144': 'NY', '145': 'NY', '146': 'NY', '147': 'NY', '148': 'NY', '149': 'NY',
    # North Carolina
    '270': 'NC', '271': 'NC', '272': 'NC', '273': 'NC', '274': 'NC',
    '275': 'NC', '276': 'NC', '277': 'NC', '278': 'NC', '279': 'NC',
    '280': 'NC', '281': 'NC', '282': 'NC', '283': 'NC', '284': 'NC', '285': 'NC', '286': 'NC', '287': 'NC', '288': 'NC', '289': 'NC',
    # North Dakota
    '580': 'ND', '581': 'ND', '582': 'ND', '583': 'ND', '584': 'ND', '585': 'ND', '586': 'ND', '587': 'ND', '588': 'ND',
    # Ohio
    '430': 'OH', '431': 'OH', '432': 'OH', '433': 'OH', '434': 'OH',
    '435': 'OH', '436': 'OH', '437': 'OH', '438': 'OH', '439': 'OH',
    '440': 'OH', '441': 'OH', '442': 'OH', '443': 'OH', '444': 'OH',
    '445': 'OH', '446': 'OH', '447': 'OH', '448': 'OH', '449': 'OH',
    '450': 'OH', '451': 'OH', '452': 'OH', '453': 'OH', '454': 'OH', '455': 'OH', '456': 'OH', '457': 'OH', '458': 'OH',
    # Oklahoma
    '730': 'OK', '731': 'OK', '734': 'OK', '735': 'OK', '736': 'OK',
    '737': 'OK', '738': 'OK', '739': 'OK', '740': 'OK', '741': 'OK',
    '743': 'OK', '744': 'OK', '745': 'OK', '746': 'OK', '747': 'OK', '748': 'OK', '749': 'OK',
    # Oregon
    '970': 'OR', '971': 'OR', '972': 'OR', '973': 'OR', '974': 'OR',
    '975': 'OR', '976': 'OR', '977': 'OR', '978': 'OR', '979': 'OR',
    # Pennsylvania
    '150': 'PA', '151': 'PA', '152': 'PA', '153': 'PA', '154': 'PA',
    '155': 'PA', '156': 'PA', '157': 'PA', '158': 'PA', '159': 'PA',
    '160': 'PA', '161': 'PA', '162': 'PA', '163': 'PA', '164': 'PA',
    '165': 'PA', '166': 'PA', '167': 'PA', '168': 'PA', '169': 'PA',
    '170': 'PA', '171': 'PA', '172': 'PA', '173': 'PA', '174': 'PA',
    '175': 'PA', '176': 'PA', '177': 'PA', '178': 'PA', '179': 'PA',
    '180': 'PA', '181': 'PA', '182': 'PA', '183': 'PA', '184': 'PA',
    '185': 'PA', '186': 'PA', '187': 'PA', '188': 'PA', '189': 'PA',
    '190': 'PA', '191': 'PA', '192': 'PA', '193': 'PA', '194': 'PA', '195': 'PA', '196': 'PA',
    # Rhode Island
    '028': 'RI', '029': 'RI',
    # South Carolina
    '290': 'SC', '291': 'SC', '292': 'SC', '293': 'SC', '294': 'SC', '295': 'SC', '296': 'SC', '297': 'SC', '298': 'SC', '299': 'SC',
    # South Dakota
    '570': 'SD', '571': 'SD', '572': 'SD', '573': 'SD', '574': 'SD', '575': 'SD', '576': 'SD', '577': 'SD',
    # Tennessee
    '370': 'TN', '371': 'TN', '372': 'TN', '373': 'TN', '374': 'TN',
    '375': 'TN', '376': 'TN', '377': 'TN', '378': 'TN', '379': 'TN',
    '380': 'TN', '381': 'TN', '382': 'TN', '383': 'TN', '384': 'TN', '385': 'TN',
    # Texas
    '750': 'TX', '751': 'TX', '752': 'TX', '753': 'TX', '754': 'TX',
    '755': 'TX', '756': 'TX', '757': 'TX', '758': 'TX', '759': 'TX',
    '760': 'TX', '761': 'TX', '762': 'TX', '763': 'TX', '764': 'TX',
    '765': 'TX', '766': 'TX', '767': 'TX', '768': 'TX', '769': 'TX',
    '770': 'TX', '771': 'TX', '772': 'TX', '773': 'TX', '774': 'TX',
    '775': 'TX', '776': 'TX', '777': 'TX', '778': 'TX', '779': 'TX',
    '780': 'TX', '781': 'TX', '782': 'TX', '783': 'TX', '784': 'TX',
    '785': 'TX', '786': 'TX', '787': 'TX', '788': 'TX', '789': 'TX',
    '790': 'TX', '791': 'TX', '792': 'TX', '793': 'TX', '794': 'TX',
    '795': 'TX', '796': 'TX', '797': 'TX', '798': 'TX', '799': 'TX',
    # Utah
    '840': 'UT', '841': 'UT', '842': 'UT', '843': 'UT', '844': 'UT', '845': 'UT', '846': 'UT', '847': 'UT',
    # Vermont
    '050': 'VT', '051': 'VT', '052': 'VT', '053': 'VT', '054': 'VT', '056': 'VT', '057': 'VT', '058': 'VT', '059': 'VT',
    # Virginia
    '220': 'VA', '221': 'VA', '222': 'VA', '223': 'VA', '224': 'VA',
    '225': 'VA', '226': 'VA', '227': 'VA', '228': 'VA', '229': 'VA',
    '230': 'VA', '231': 'VA', '232': 'VA', '233': 'VA', '234': 'VA',
    '235': 'VA', '236': 'VA', '237': 'VA', '238': 'VA', '239': 'VA',
    '240': 'VA', '241': 'VA', '242': 'VA', '243': 'VA', '244': 'VA', '245': 'VA', '246': 'VA',
    # Washington
    '980': 'WA', '981': 'WA', '982': 'WA', '983': 'WA', '984': 'WA',
    '985': 'WA', '986': 'WA', '988': 'WA', '989': 'WA', '990': 'WA',
    '991': 'WA', '992': 'WA', '993': 'WA', '994': 'WA',
    # West Virginia
    '247': 'WV', '248': 'WV', '249': 'WV', '250': 'WV', '251': 'WV',
    '252': 'WV', '253': 'WV', '254': 'WV', '255': 'WV', '256': 'WV',
    '257': 'WV', '258': 'WV', '259': 'WV', '260': 'WV', '261': 'WV',
    '262': 'WV', '263': 'WV', '264': 'WV', '265': 'WV', '266': 'WV', '267': 'WV', '268': 'WV',
    # Wisconsin
    '530': 'WI', '531': 'WI', '532': 'WI', '534': 'WI', '535': 'WI',
    '537': 'WI', '538': 'WI', '539': 'WI', '540': 'WI', '541': 'WI',
    '542': 'WI', '543': 'WI', '544': 'WI', '545': 'WI', '546': 'WI',
    '547': 'WI', '548': 'WI', '549': 'WI',
    # Wyoming
    '820': 'WY', '821': 'WY', '822': 'WY', '823': 'WY', '824': 'WY',
    '825': 'WY', '826': 'WY', '827': 'WY', '828': 'WY', '829': 'WY', '831': 'WY',
}


STATE_NAMES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas',
    'CA': 'California', 'CO': 'Colorado', 'CT': 'Connecticut', 'DE': 'Delaware',
    'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia', 'HI': 'Hawaii',
    'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa',
    'KS': 'Kansas', 'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine',
    'MD': 'Maryland', 'MA': 'Massachusetts', 'MI': 'Michigan', 'MN': 'Minnesota',
    'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana', 'NE': 'Nebraska',
    'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico',
    'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio',
    'OK': 'Oklahoma', 'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island',
    'SC': 'South Carolina', 'SD': 'South Dakota', 'TN': 'Tennessee', 'TX': 'Texas',
    'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming',
}


# ── State Disclosure Context ─────────────────────────────────────────

@dataclass
class StateDisclosureContext:
    """State-specific disclosure intelligence for analysis."""
    state_code: str
    state_name: str
    disclosure_level: str  # 'comprehensive', 'moderate', 'minimal', 'caveat_emptor'
    primary_form: str      # e.g. "Transfer Disclosure Statement (TDS)"
    disclosure_notes: list = field(default_factory=list)
    required_disclosures: list = field(default_factory=list)
    buyer_protections: list = field(default_factory=list)
    common_hazards: list = field(default_factory=list)
    legal_disclaimer: str = ''


# States grouped by disclosure requirements
_COMPREHENSIVE = {
    'CA', 'TX', 'OH', 'PA', 'MI', 'IL', 'IN', 'WI', 'MN', 'IA',
    'OR', 'WA', 'HI', 'ME', 'CT', 'NH', 'RI', 'KY', 'OK', 'NE', 'SD',
}
_MODERATE = {
    'NY', 'FL', 'NJ', 'NC', 'VA', 'MD', 'MA', 'CO', 'AZ', 'NV',
    'TN', 'SC', 'MO', 'LA', 'GA', 'UT', 'NM', 'ID', 'MT', 'DE',
    'WV', 'VT', 'KS', 'AR', 'ND', 'AK', 'DC',
}
_CAVEAT_EMPTOR = {'AL', 'MS', 'WY'}


# State-specific form names and details
_STATE_FORMS = {
    'CA': 'Transfer Disclosure Statement (TDS)',
    'TX': 'TREC Seller\'s Disclosure Notice',
    'FL': 'Seller\'s Property Disclosure (often waived via AS-IS)',
    'NY': 'Property Condition Disclosure Statement (PCDS)',
    'OH': 'Residential Property Disclosure Form',
    'PA': 'Seller\'s Property Disclosure Statement',
    'IL': 'Residential Real Property Disclosure Report',
    'MI': 'Seller\'s Disclosure Statement',
    'NC': 'Residential Property and Owners\' Association Disclosure Statement',
    'VA': 'Residential Property Disclosure Statement',
    'NJ': 'Seller\'s Disclosure Statement',
    'AZ': 'Seller\'s Property Disclosure Statement (SPDS)',
    'CO': 'Seller\'s Property Disclosure',
    'WA': 'Seller Disclosure Statement (Form 17)',
    'OR': 'Seller\'s Property Disclosure Statement',
    'GA': 'Seller\'s Property Disclosure Statement',
    'TN': 'Tennessee Residential Property Condition Disclosure',
    'IN': 'Seller\'s Residential Real Estate Sales Disclosure',
    'MN': 'Seller\'s Property Disclosure Statement',
    'WI': 'Real Estate Condition Report',
}


# State-specific hazard profiles
_STATE_HAZARDS = {
    'CA': ['earthquakes', 'wildfires', 'mudslides', 'drought', 'flood zones'],
    'TX': ['flooding', 'hurricanes', 'foundation settling', 'termites', 'hail'],
    'FL': ['hurricanes', 'flooding', 'sinkholes', 'termites', 'wind damage', 'mold'],
    'NY': ['flooding', 'radon', 'lead paint', 'underground oil tanks', 'snow loads'],
    'OH': ['radon', 'flooding', 'lead paint', 'mine subsidence'],
    'PA': ['radon', 'flooding', 'mine subsidence', 'lead paint'],
    'IL': ['radon', 'flooding', 'lead paint', 'tornadoes'],
    'MI': ['radon', 'flooding', 'lead paint', 'foundation issues from frost'],
    'AZ': ['termites', 'expansive soils', 'flash floods', 'extreme heat damage'],
    'CO': ['radon', 'wildfires', 'hail', 'expansive soils', 'altitude effects'],
    'WA': ['earthquakes', 'volcanic hazards', 'flooding', 'landslides'],
    'OR': ['earthquakes', 'volcanic hazards', 'radon', 'flooding'],
    'NC': ['hurricanes', 'flooding', 'radon', 'termites'],
    'GA': ['termites', 'flooding', 'hurricanes (coastal)', 'radon'],
    'NV': ['earthquakes', 'extreme heat', 'flash floods', 'expansive soils'],
    'NJ': ['radon', 'flooding', 'underground oil tanks', 'lead paint'],
    'VA': ['radon', 'flooding', 'termites', 'hurricanes (coastal)'],
    'TN': ['radon', 'flooding', 'sinkholes', 'tornadoes'],
    'LA': ['hurricanes', 'flooding', 'termites', 'subsidence', 'mold'],
    'SC': ['hurricanes', 'flooding', 'termites', 'mold'],
}

# Default hazards for states without specific profiles
_DEFAULT_HAZARDS = ['flooding', 'radon', 'lead paint (pre-1978)']


def detect_state_from_zip(zip_code: str) -> Optional[str]:
    """Detect state from ZIP code. Returns 2-letter state code or None."""
    z = str(zip_code).strip()[:5]
    if len(z) < 3:
        return None
    prefix = z[:3]
    return ZIP3_TO_STATE.get(prefix)


def detect_state_from_text(text: str) -> Optional[str]:
    """Detect state from document text using form-specific markers."""
    text_upper = text[:5000].upper()  # Check first 5K chars

    form_markers = {
        'CA': ['TRANSFER DISCLOSURE STATEMENT', 'CALIFORNIA CIVIL CODE', 'TDS', 'CAL. CIV. CODE'],
        'TX': ['TREC', 'TEXAS PROPERTY CODE', 'TEXAS REAL ESTATE COMMISSION'],
        'FL': ['FLORIDA STATUTE', 'AS-IS CONTRACT', 'FAR/BAR'],
        'NY': ['PROPERTY CONDITION DISCLOSURE', 'NEW YORK STATE', 'RPL §462'],
        'OH': ['OHIO REVISED CODE', 'RESIDENTIAL PROPERTY DISCLOSURE'],
        'PA': ['PENNSYLVANIA REAL ESTATE', 'SELLER DISCLOSURE ACT'],
        'IL': ['ILLINOIS RESIDENTIAL', 'PROPERTY DISCLOSURE REPORT'],
        'AZ': ['ARIZONA DEPARTMENT OF REAL ESTATE', 'SPDS'],
        'CO': ['COLORADO REAL ESTATE COMMISSION'],
        'WA': ['FORM 17', 'WASHINGTON SELLER DISCLOSURE'],
        'NC': ['NORTH CAROLINA REAL ESTATE COMMISSION'],
        'NJ': ['NEW JERSEY SELLER', 'NJAC'],
        'VA': ['VIRGINIA RESIDENTIAL PROPERTY DISCLOSURE'],
    }

    for state, markers in form_markers.items():
        for marker in markers:
            if marker in text_upper:
                return state

    # Fallback: look for state name in address lines
    for code, name in STATE_NAMES.items():
        pattern = rf'\b{name}\b'
        if re.search(pattern, text[:3000], re.IGNORECASE):
            return code

    return None


def get_state_context(state_code: str) -> StateDisclosureContext:
    """Get full disclosure context for a state."""
    state_code = (state_code or '').upper().strip()
    if state_code not in STATE_NAMES:
        return StateDisclosureContext(
            state_code='XX',
            state_name='Unknown State',
            disclosure_level='unknown',
            primary_form='State-specific disclosure form',
            disclosure_notes=[
                'State could not be determined from the provided documents.',
                'OfferWise analyzes the documents as-is using universal inspection and disclosure patterns.',
                'For state-specific legal requirements, consult a local real estate attorney.',
            ],
            legal_disclaimer='This analysis does not account for state-specific disclosure laws. Consult a local attorney.',
        )

    name = STATE_NAMES[state_code]
    form = _STATE_FORMS.get(state_code, f'{name} seller disclosure form')
    hazards = _STATE_HAZARDS.get(state_code, _DEFAULT_HAZARDS)

    if state_code in _COMPREHENSIVE:
        level = 'comprehensive'
        notes = [
            f'{name} requires comprehensive seller disclosure.',
            f'Sellers must complete and deliver the {form} to buyers.',
            'Sellers must disclose all known material defects.',
        ]
        protections = [
            'Buyer has the right to receive disclosure before closing.',
            'Seller liability for known undisclosed defects.',
            'Inspection contingency is standard.',
        ]
    elif state_code in _CAVEAT_EMPTOR:
        level = 'caveat_emptor'
        notes = [
            f'{name} follows a caveat emptor (buyer beware) approach.',
            'Sellers have minimal or no mandatory disclosure requirements.',
            'A thorough independent inspection is especially critical in this state.',
        ]
        protections = [
            'Limited seller disclosure requirements — buyer must rely on inspection.',
            'Fraud protections still apply (seller cannot actively conceal defects).',
        ]
    else:
        level = 'moderate'
        notes = [
            f'{name} requires seller disclosure with some specific exemptions.',
            f'The standard form is the {form}.',
            'Some property types or transaction types may be exempt.',
        ]
        protections = [
            'Buyer has the right to inspect the property.',
            'Seller must disclose known material defects.',
        ]

    # Special state-specific notes
    if state_code == 'FL':
        notes.append('Florida commonly uses AS-IS contracts which limit seller disclosure obligations. An independent inspection is strongly recommended.')
    elif state_code == 'NY':
        notes.append('New York sellers can opt to pay a $500 credit to buyers instead of providing the Property Condition Disclosure Statement. This is common — it does not mean there are no issues.')
    elif state_code == 'TX':
        notes.append('Texas law requires disclosure of flooding history, previous repairs, and known defects including foundation issues, which are common in many TX areas.')
    elif state_code == 'CA':
        notes.append('California has the most comprehensive disclosure requirements in the nation, including natural hazard zones, environmental hazards, and neighborhood conditions.')

    return StateDisclosureContext(
        state_code=state_code,
        state_name=name,
        disclosure_level=level,
        primary_form=form,
        disclosure_notes=notes,
        required_disclosures=[],  # Populated by AI analysis
        buyer_protections=protections,
        common_hazards=hazards,
        legal_disclaimer=f'This analysis is based on {name} disclosure requirements as understood by OfferWise. It is not legal advice. Consult a {name}-licensed real estate attorney for specific legal questions.',
    )


def get_state_legal_notes(state_code: str) -> list:
    """Get concise legal notes for inclusion in analysis results."""
    ctx = get_state_context(state_code)
    return ctx.disclosure_notes
