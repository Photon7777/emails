"""Convenience wrapper for the separate UMD TA/RA discovery workflow."""

from main import main


if __name__ == "__main__":
    raise SystemExit(
        main(
            [
                "umd-discover",
                "--search-depth",
                "expanded",
                "--target-contacts",
                "75",
                "--max-contacts",
                "100",
                "--min-score",
                "50",
            ]
        )
    )
