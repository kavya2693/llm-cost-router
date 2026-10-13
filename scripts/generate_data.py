"""Write the evaluation suite and the daily traffic log: `make data`."""

from llm_cost_router.config import SUITE_PATH, TRAFFIC_PATH
from llm_cost_router.data import generate_suite, generate_traffic, write_suite, write_traffic


def main() -> None:
    cases = generate_suite()
    write_suite(SUITE_PATH, cases)
    traffic = generate_traffic()
    write_traffic(TRAFFIC_PATH, traffic)
    print(f"wrote {len(cases)} cases to {SUITE_PATH.name} and {len(traffic)} days to {TRAFFIC_PATH.name}")


if __name__ == "__main__":
    main()
