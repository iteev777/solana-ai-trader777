from discovery.gecko_scanner import scan_new_pools
from discovery.pumpfun_scanner import scan_pumpfun
from discovery.birdeye_scanner import scan_birdeye


seen_tokens = set()


def collect_tokens():

    all_tokens = []

    scanners = [
        scan_new_pools,
        scan_pumpfun,
        scan_birdeye
    ]

    for scanner in scanners:

        try:
            tokens = scanner()

            for token in tokens:

                address = token.get("tokenAddress")

                if not address:
                    continue

                if address in seen_tokens:
                    continue

                seen_tokens.add(address)

                all_tokens.append(token)

        except Exception as error:
            print(
                "TOKEN AGGREGATOR ERROR:",
                repr(error)
            )

    print("AGGREGATED NEW TOKENS:", len(all_tokens))

    return all_tokens