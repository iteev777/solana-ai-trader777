import requests


API_URL = "https://api.rugcheck.xyz/v1/tokens/"


def get_rugcheck_report(token_address):

    if not token_address:
        print("RUGCHECK ERROR: Missing token address")
        return None

    url = API_URL + token_address + "/report"

    try:
        response = requests.get(
            url,
            headers={"Accept": "application/json"},
            timeout=20
        )

        if response.status_code == 429:
            print("RUGCHECK: Rate limit reached")
            return None

        response.raise_for_status()

        data = response.json()

        if not isinstance(data, dict):
            print("RUGCHECK ERROR: Invalid response")
            return None

        print("================================")
        print("RUGCHECK REPORT")
        print("TOKEN:", token_address)
        print("SCORE:", data.get("score"))
        print("NORMALIZED SCORE:", data.get("score_normalised"))

        risks = data.get("risks", [])

        print("RISK FLAGS:", len(risks))

        for risk in risks:

            print(
                "RISK:",
                risk.get("name", "Unknown"),
                "| LEVEL:",
                risk.get("level", "Unknown"),
                "| DESCRIPTION:",
                risk.get("description", "No description")
            )

        print("================================")

        return data

    except requests.RequestException as error:

        print("RUGCHECK CONNECTION ERROR:", error)
        return None

    except ValueError as error:

        print("RUGCHECK DATA ERROR:", error)
        return None


if __name__ == "__main__":

    print("RUGCHECK MODULE READY")
