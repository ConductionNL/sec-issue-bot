import sys
from typing import Optional

from incident_agent.extract import IncidentExtractor
from incident_agent.schema import DUTCH_FIELD_LABELS


def main() -> int:
    """
    Demo CLI to run the incident extractor on a short description and print results.

    @return int: Process exit code (0 on success, 2 on usage error).
    """
    if len(sys.argv) < 2:
        print("Usage: python demo_extract.py \"<short incident description>\"")
        return 2

    description: str = sys.argv[1]
    extractor = IncidentExtractor()
    result = extractor.extract(description)

    print("\nProposed fields (only filled):")
    for key, value in result.data.model_dump().items():
        if value:
            label = DUTCH_FIELD_LABELS.get(key, key)
            print(f"- {label}: {value}")

    if result.questions:
        print("\nFollow-up questions:")
        for q in result.questions:
            print(f"- [{q.field_key}] {q.question_text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())