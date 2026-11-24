"""Apply additive ordering migrations and idempotently seed the DOCX-derived menu."""

import json

from src.ordering.menu import seed_menu
from src.warehouse.loader import connection


def main():
    with connection() as conn:
        result = seed_menu(conn)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
