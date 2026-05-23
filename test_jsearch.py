"""Quick JSearch smoke test — prints first 10 normalized results.

Run manually after setting APIFY_API_TOKEN in .env:
    python test_jsearch.py

Not a pytest test — sits next to verify_connection.py / fix_cities.py.
"""

import json
import sys
from itertools import islice

from connectors.jsearch import JSearchConnector


def main() -> int:
    conn = JSearchConnector()
    if not conn._scraper._api_key:
        print("ERROR: RAPIDAPI_KEY not set in .env", file=sys.stderr)
        return 1

    print(f"Fetching first 10 jobs from {conn.source_name}…")
    count = 0
    for i, job in enumerate(islice(conn.fetch(), 10), 1):
        print(f"\n--- Job {i} ---")
        print(json.dumps(job, indent=2, default=str, ensure_ascii=False))
        count += 1

    print(f"\nDone. {count} job(s) fetched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
