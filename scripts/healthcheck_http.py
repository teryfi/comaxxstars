import sys
import urllib.request


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    with urllib.request.urlopen(sys.argv[1], timeout=3) as response:  # noqa: S310
        if response.status != 200:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
