from __future__ import annotations

import logging
import os

import click
import requests
import zipcodes

from dotenv import load_dotenv
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

logger = logging.getLogger(__name__)

s = requests.Session()
s.headers.update(
    {
        "User-Agent": "7-Eleven/707 CFNetwork/3860.500.112 Darwin/25.4.0",
    }
)


def get_zip_coordinates(zip_code: str) -> tuple[float, float]:
    logger.info("Geocoding ZIP %s...", zip_code)

    matches = zipcodes.matching(zip_code)

    if matches and len(matches):
        lat = matches[0].get("lat")
        lon = matches[0].get("long")
        logger.info("Geocoded ZIP %s -> (%s, %s)", zip_code, lat, lon)
        return float(lat), float(lon)

    raise RuntimeError(f"Unable to geocode ZIP {zip_code}")


def update_pricelock(store_id: str, lat: float, lon: float) -> None:
    logger.info("Unlocking current price lock")
    unlock_response = s.put(
        "https://apis.7-eleven.com/v4/fuel/pricelock/multigrade",
        json={"status": "unlock"},
    )
    logger.info(
        "Unlock response status=%s bytes=%s",
        unlock_response.status_code,
        len(unlock_response.text),
    )

    logger.info("Locking price at store=%s (%s, %s)", store_id, lat, lon)
    lock_response = s.post(
        "https://apis.7-eleven.com/v4/fuel/pricelock/multigrade",
        json={"store_id": store_id, "lat": lat, "lon": lon},
    )
    logger.info(
        "Lock response status=%s bytes=%s",
        lock_response.status_code,
        len(lock_response.text),
    )
    if lock_response.status_code != 200:
        logger.error(
            "Error body: %s",
            lock_response.text,
        )


def fetch_stores(
    lat: float,
    lon: float,
    radius_miles: float,
    search_limit: int,
    query_text: str,
) -> list[dict]:
    logger.info("Calling GraphQL stores endpoint")
    payload = {
        "query": query_text,
        "variables": {
            "country": "US",
            "limit": search_limit,
            "filters": ["service:mobile_fuel_pay"],
            "lat": str(lat),
            "lon": str(lon),
            "curr_lat": str(lat),
            "curr_lon": str(lon),
            "radius": radius_miles,
        },
    }
    response = s.post("https://apis.7-eleven.com/v5/stores/graphql", json=payload)
    logger.info(
        "GraphQL response status=%s bytes=%s", response.status_code, len(response.text)
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"GraphQL request failed with {response.status_code}: {response.text}"
        )

    result = response.json()
    if "errors" in result:
        raise RuntimeError(f"GraphQL errors: {result['errors']}")

    return result.get("data", {}).get("stores", [])


def extract_price_rows(stores: list[dict], grade_filter: str | None) -> list[dict]:
    rows = []
    for store in stores:
        fuel_data = store.get("fuel_data")
        if not fuel_data:
            continue

        for grade in fuel_data.get("grades") or []:
            grade_name = str(grade.get("name", "")).strip()

            if grade_filter and grade_name.lower() != grade_filter.lower():
                continue

            raw_price = grade.get("price")
            if raw_price is None:
                continue

            rows.append(
                {
                    "store_id": store.get("id"),
                    "store_name": store.get("name"),
                    "address": store.get("address"),
                    "city": store.get("city"),
                    "state": store.get("state"),
                    "postal_code": store.get("postal_code"),
                    "lat": store.get("lat"),
                    "lon": store.get("lon"),
                    "distance_label": store.get("distance_label"),
                    "grade_name": grade_name,
                    "grade_abbr": grade.get("abbr"),
                    "price_value": float(raw_price) / 1000,
                    "price_label": grade.get("price_label")
                    or f"${float(raw_price) / 1000:.3f}",
                }
            )

    return rows


def select_row_from_table(console: Console, rows: list[dict]) -> dict | None:
    if not rows:
        return None

    selected_index = 0

    def render_table() -> Table:
        table = Table(
            title="Top Prices",
            box=box.ROUNDED,
            header_style="bold cyan",
            show_lines=False,
        )
        table.add_column("#", style="dim", width=3, justify="right")
        table.add_column("Store", overflow="fold")
        table.add_column("Grade", style="bold")
        table.add_column("Price", style="bold green")
        table.add_column("Distance", style="dim")
        table.add_column("City", overflow="fold")
        table.add_column("State", style="dim", width=5)
        for idx, row in enumerate(rows, start=1):
            is_selected = idx - 1 == selected_index
            style = "reverse bold" if is_selected else ""
            table.add_row(
                f"{idx}",
                row.get("store_name") or "",
                row.get("grade_name") or "",
                row.get("price_label") or "",
                row.get("distance_label") or "",
                row.get("city") or "",
                row.get("state") or "",
                style=style,
            )
        return table

    def get_output() -> ANSI:
        with console.capture() as capture:
            console.print(render_table())
            console.print("\nUse ↑/↓ to move, Enter to lock, Esc to skip.", style="dim")
        return ANSI(capture.get())

    kb = KeyBindings()

    @kb.add("up")
    def _up(event) -> None:
        nonlocal selected_index
        selected_index = (selected_index - 1) % len(rows)
        event.app.invalidate()

    @kb.add("down")
    def _down(event) -> None:
        nonlocal selected_index
        selected_index = (selected_index + 1) % len(rows)
        event.app.invalidate()

    @kb.add("enter")
    def _enter(event) -> None:
        event.app.exit(result=rows[selected_index])

    @kb.add("escape")
    def _escape(event) -> None:
        event.app.exit(result=None)

    control = FormattedTextControl(get_output)
    layout = Layout(Window(content=control, always_hide_cursor=True))
    app = Application(layout=layout, key_bindings=kb, full_screen=False)
    return app.run()


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--zip", "zip_code", show_default=True, help="ZIP code", required=True)
@click.option(
    "--grade",
    default="Regular",
    show_default=True,
    help="Fuel grade (use 'None' or empty for all)",
)
@click.option(
    "--radius-miles",
    default=50.0,
    type=float,
    show_default=True,
    help="Search radius in miles",
)
@click.option(
    "--search-limit",
    default=100,
    type=int,
    show_default=True,
    help="Search limit for GraphQL stores",
)
@click.option(
    "--result-limit",
    default=10,
    type=int,
    show_default=True,
    help="Max number of results to display",
)
@click.option(
    "--bearer-token",
    default=lambda: os.getenv("BEARER_TOKEN", "").strip() or None,
    show_default="BEARER_TOKEN",
    help="Bearer token (defaults to BEARER_TOKEN)",
)
def main(
    zip_code: str,
    grade: str,
    radius_miles: float,
    search_limit: int,
    result_limit: int,
    bearer_token: str | None,
) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    console = Console()

    logger.info("Starting 7-Eleven gas price query")
    logger.info(
        "Config zip=%s grade=%s radius=%smi search_limit=%s result_limit=%s",
        zip_code,
        grade or "ALL",
        radius_miles,
        search_limit,
        result_limit,
    )

    try:
        lat, lon = get_zip_coordinates(zip_code)
        s.headers["Authorization"] = f"Bearer {bearer_token}"
        with open("stores.graphql", "r", encoding="utf-8") as query_file:
            query_text = query_file.read()
        stores = fetch_stores(
            lat,
            lon,
            radius_miles=radius_miles,
            search_limit=search_limit,
            query_text=query_text,
        )
        logger.info("Received %s stores", len(stores))

        rows = extract_price_rows(stores, grade_filter=grade)
        logger.info("Extracted %s priced rows after filtering", len(rows))
        if not rows:
            logger.warning(
                "No matching fuel prices were returned for the selected ZIP/radius/grade."
            )
            logger.info("Tip: use --grade None to include all grades.")
            return

        rows.sort(key=lambda row: row["price_value"])
        best = rows[0]

        console.print(
            Panel.fit(
                Text("7-Eleven Gas Price Finder", justify="center", style="bold cyan"),
                subtitle="Query & lock prices",
                border_style="cyan",
            )
        )
        console.print(
            f"[bold]ZIP:[/bold] {zip_code}  [bold]Grade:[/bold] {grade or 'ALL'}  "
            f"[bold]Radius:[/bold] {radius_miles} miles",
            style="dim",
        )

        best_text = Text()
        best_text.append("Lowest price\n", style="bold green")
        best_text.append(
            f"{best['store_name']} | {best['grade_name']} | {best['price_label']}\n",
            style="bold",
        )
        best_text.append(
            f"{best['address']}, {best['city']} {best['state']} {best['postal_code']}",
            style="dim",
        )
        console.print(Panel(best_text, border_style="green", padding=(1, 2)))

        top_rows = rows[:result_limit]

        selected_row = select_row_from_table(console, top_rows)
        if selected_row:
            store_id = str(selected_row.get("store_id"))
            lat_value = selected_row.get("lat")
            lon_value = selected_row.get("lon")
            if not store_id or lat_value is None or lon_value is None:
                raise RuntimeError(
                    "Missing store_id or coordinates for price lock update"
                )

            update_pricelock(
                store_id=store_id, lat=float(lat_value), lon=float(lon_value)
            )

        logger.info("Done")
    except Exception:
        logger.exception("FAILED")
        raise


if __name__ == "__main__":
    main()
