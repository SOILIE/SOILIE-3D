"""Freeze the regional public Lambda tariff; never read account billing data."""
from datetime import datetime, UTC
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from serverless.cloud_benchmark.checkpoint import write_json


REGION = 'ca-central-1'
SOURCE = f'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSLambda/current/{REGION}/index.json'
USAGE = {
    'CAN1-Lambda-GB-Second': 'computeGBSecondUSD',
    'CAN1-Request': 'requestUSD',
    'CAN1-Lambda-Storage-GB-Second': 'ephemeralGBSecondUSD',
}


def extract(raw):
    """Select one x86 on-demand first-tier price for each metered resource."""
    offer = json.loads(raw)
    rates, products = {}, {}
    for sku, product in offer['products'].items():
        field = USAGE.get(product['attributes'].get('usagetype'))
        if field is None:
            continue
        dimensions = [dimension
                      for term in offer['terms']['OnDemand'].get(sku, {}).values()
                      for dimension in term['priceDimensions'].values()
                      if dimension['beginRange'] == '0']
        if field in rates or len(dimensions) != 1:
            raise ValueError('Ambiguous public regional Lambda tariff')
        price = float(dimensions[0]['pricePerUnit']['USD'])
        if not 0 < price < 1:
            raise ValueError('Unexpected public Lambda unit price')
        rates[field] = price
        products[field] = {'sku': sku, 'rateCode': dimensions[0]['rateCode']}
    if rates.keys() != set(USAGE.values()):
        raise ValueError('The public offer omitted a required Lambda resource')
    return {
        'schemaVersion': 1, 'region': REGION, 'architecture': 'x86_64',
        'currency': 'USD', 'retrievedAt': datetime.now(UTC).isoformat(),
        'sourceUrl': SOURCE, 'sourceSha256': hashlib.sha256(raw).hexdigest(),
        'offerPublishedAt': offer['publicationDate'], 'products': products,
        **rates,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('A frozen price snapshot must not be overwritten')
    with urlopen(SOURCE, timeout=60) as response:
        rates = extract(response.read())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, rates)
    print(json.dumps(rates))


if __name__ == '__main__':
    main()
