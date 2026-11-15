"""Live FX reference rates for venture simulation.

One fixed, keyless public source: the European Central Bank's daily euro
reference rates (about thirty currencies, published once per working day).
The URL is a constant, not operator input, and the request goes through the
same SSRF-checked fetcher as news feeds. Rates are cached by the caller and
frozen onto each tick, so a venture stays replayable when the source changes
or is unreachable.
"""
from datetime import datetime, timezone
import xml.etree.ElementTree as ElementTree

import venture_feeds

ECB_URL = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml'
SOURCE = 'ecb'
LABEL = 'Live ECB reference rate (manual rate is the fallback)'
MAX_AGE_HOURS = 12
FETCH_LIMIT = 64_000


def parse_ecb(body):
    if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
        raise ValueError('FX source rejected a document with entity declarations.')
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise ValueError('FX source is not valid XML.') from error
    rates, date = {'EUR': 1.0}, ''
    for element in root.iter():
        if element.get('time'):
            date = element.get('time')[:10]
        currency, rate = element.get('currency'), element.get('rate')
        if currency and rate:
            try:
                value = float(rate)
            except ValueError:
                continue
            if len(currency) == 3 and currency.isalpha() and 0 < value < 1_000_000:
                rates[currency.upper()] = value
    if len(rates) < 2 or not date:
        raise ValueError('FX source returned no reference rates.')
    return dict(source=SOURCE, date=date, rates=rates)


def fetch(request_fn=venture_feeds.request):
    response = request_fn(ECB_URL, {'User-Agent': 'Herdr venture simulation', 'Accept': 'application/xml'},
                          limit=FETCH_LIMIT)
    value = parse_ecb(response.body)
    value['fetched_at'] = datetime.now(timezone.utc).isoformat()
    return value


def cross(rates, pair):
    """``LEFT/RIGHT`` rate: one LEFT buys this many RIGHT (EUR-based cross)."""
    left, right = pair.split('/')
    if left == right:
        return 1.0
    missing = [code for code in (left, right) if code not in rates]
    if missing:
        raise ValueError('The ECB reference rates do not include ' + ', '.join(missing) + '.')
    return round(rates[right] / rates[left], 6)
