from discovery.token_aggregator import collect_tokens


print("\n========== DISCOVERY TEST ==========\n")

tokens = collect_tokens()

print("\nTOTAL TOKENS FOUND:", len(tokens))

for token in tokens:
    print("--------------------------------")
    print("ADDRESS:", token.get("tokenAddress"))
    print("NAME:", token.get("name"))
    print("SYMBOL:", token.get("symbol"))
    print("SOURCES:", token.get("discovery_sources"))

print("\n========== TEST COMPLETE ==========\n")