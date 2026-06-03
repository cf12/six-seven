import logging
import os

from flask import Flask, render_template, request

from main import find_price_rows, lock_price, normalize_bearer_token


app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

GRADES = ["Regular", "Midgrade", "Premium", "Diesel", "None"]
DEFAULTS = {
    "zip": "",
    "grade": "Regular",
    "radius_miles": "50",
    "search_limit": "100",
    "result_limit": "10",
    "bearer_token": "",
}


def form_values() -> dict[str, str]:
    values = {**DEFAULTS, "bearer_token": os.getenv("BEARER_TOKEN", "")}
    values.update({name: request.form.get(name, values[name]).strip() for name in values})
    values["bearer_token"] = normalize_bearer_token(values["bearer_token"])
    return values


def row_address(row: dict) -> str:
    return ", ".join(part for part in [row.get("address"), row.get("city"), row.get("state")] if part)


def page(status: int = 200, **context):
    context.setdefault("grades", GRADES)
    context.setdefault("values", {**DEFAULTS, "bearer_token": os.getenv("BEARER_TOKEN", "")})
    context.setdefault("rows", None)
    context.setdefault("message", "")
    context.setdefault("error", "")
    context.setdefault("lock_response", None)
    context.setdefault("row_address", row_address)
    return render_template("index.html", **context), status


@app.get("/")
def index():
    return page()


@app.post("/search")
def search():
    values = form_values()
    try:
        rows = find_price_rows(
            zip_code=values["zip"],
            grade=values["grade"],
            radius_miles=float(values["radius_miles"]),
            search_limit=int(values["search_limit"]),
            bearer_token=values["bearer_token"],
        )[: int(values["result_limit"])]
        return page(values=values, rows=rows)
    except Exception as exc:
        logging.exception("Search failed")
        return page(values=values, error=str(exc), status=500)


@app.post("/lock")
def lock():
    values = form_values()
    try:
        lock_response = lock_price(
            store_id=request.form["store_id"],
            lat=float(request.form["lat"]),
            lon=float(request.form["lon"]),
            bearer_token=values["bearer_token"],
        )
        return page(
            values=values,
            message="Price lock request sent.",
            lock_response={
                "status": f"{lock_response.status_code} {lock_response.reason}",
                "body": lock_response.text,
            },
        )
    except Exception as exc:
        logging.exception("Lock failed")
        return page(values=values, error=str(exc), status=500)
