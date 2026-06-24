from __future__ import annotations

import os

from openpyxl import Workbook

SHEET_COLUMNS: dict[str, list[str]] = {
    "INP_STORE": ["store_id", "store_name", "format", "region", "selling_area_sqft", "weekly_footfall", "currency"],
    "INP_PRODUCT": [
        "product_id", "product_name", "department", "subcategory", "is_perishable",
        "shelf_life_days", "temp_zone", "unit_cost", "list_price", "unit_of_measure", "default_vendor_id",
    ],
    "INP_VENDOR": [
        "vendor_id", "vendor_name", "vendor_type", "lead_time_days",
        "delivery_cadence_days", "min_order_value", "reliability_score", "cutoff_time",
    ],
    "INP_VENDOR_PRODUCT": ["vendor_id", "product_id", "case_pack_units", "case_cost", "min_order_cases", "lead_time_days"],
    "INP_INVENTORY": [
        "product_id", "store_id", "on_hand_units", "on_order_units",
        "received_date", "age_days", "days_of_supply", "as_of_date",
    ],
    "INP_PRICE": ["product_id", "store_id", "current_price", "floor_price", "ceiling_price", "currency"],
    "INP_SALES_HISTORY": ["product_id", "store_id", "sale_date", "units_sold", "revenue", "avg_price", "on_markdown"],
    "INP_SHRINK_HISTORY": ["shrink_id", "product_id", "store_id", "shrink_date", "units_lost", "cost_lost", "reason"],
    "INP_POLICY": ["policy_key", "policy_value", "scope", "department", "description"],
    "INP_DEMAND_FORECAST": ["product_id", "store_id", "forecast_date", "expected_units", "units_p10", "units_p90", "confidence"],
    "INP_ELASTICITY": ["product_id", "elasticity", "quality", "confidence"],
}

SHEET_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "INP_STORE": {
        "store_id": "Stable store slug; join key everywhere.",
        "store_name": "Display name.",
        "format": "flagship | neighborhood | express.",
        "region": "Geographic/admin region.",
        "selling_area_sqft": "Drives revenue-per-sqft KPI.",
        "weekly_footfall": "Customers per week.",
        "currency": "Default USD.",
    },
    "INP_PRODUCT": {
        "product_id": "Stable SKU slug.",
        "product_name": "Display name.",
        "department": "produce/meat/seafood/dairy/deli/bakery/floral/frozen/grocery/beverage.",
        "subcategory": "e.g. stone_fruit.",
        "is_perishable": "Turns on shelf-life / shrink logic.",
        "shelf_life_days": "Sellable days from receipt.",
        "temp_zone": "ambient | refrigerated | frozen.",
        "unit_cost": "Landed cost per unit.",
        "list_price": "Regular shelf price.",
        "unit_of_measure": "ea | lb | case.",
        "default_vendor_id": "Fallback supplier if no vendor_product row.",
    },
    "INP_VENDOR": {
        "vendor_id": "Stable vendor slug.",
        "vendor_name": "Display name.",
        "vendor_type": "produce_distributor/direct_farm/broadline/dsd/local.",
        "lead_time_days": "Order to receipt.",
        "delivery_cadence_days": "Days between routine deliveries.",
        "min_order_value": "Minimum $ to activate an order.",
        "reliability_score": "0-100 OTIF.",
        "cutoff_time": "Daily order cutoff, HH:MM.",
    },
    "INP_VENDOR_PRODUCT": {
        "vendor_id": "FK to INP_VENDOR.",
        "product_id": "FK to INP_PRODUCT.",
        "case_pack_units": "Order increment.",
        "case_cost": "Cost per case from this vendor.",
        "min_order_cases": "Per-line MOQ.",
        "lead_time_days": "Item-specific override.",
    },
    "INP_INVENTORY": {
        "product_id": "FK to INP_PRODUCT.",
        "store_id": "FK to INP_STORE.",
        "on_hand_units": "Physical on-shelf.",
        "on_order_units": "Inbound/in-transit.",
        "received_date": "When the oldest on-hand lot arrived.",
        "age_days": "Days the oldest units have aged.",
        "days_of_supply": "Est. days current on-hand lasts.",
        "as_of_date": "Snapshot anchor.",
    },
    "INP_PRICE": {
        "product_id": "FK to INP_PRODUCT.",
        "store_id": "FK to INP_STORE, or blank for chain-wide default.",
        "current_price": "Live shelf price.",
        "floor_price": "Lowest allowed (margin floor).",
        "ceiling_price": "Highest allowed.",
        "currency": "Default USD.",
    },
    "INP_SALES_HISTORY": {
        "product_id": "FK to INP_PRODUCT.",
        "store_id": "FK to INP_STORE.",
        "sale_date": "ISO YYYY-MM-DD.",
        "units_sold": "Quantity transacted.",
        "revenue": "Total sale value.",
        "avg_price": "Realized price (revenue / units).",
        "on_markdown": "Was the day at markdown?",
    },
    "INP_SHRINK_HISTORY": {
        "shrink_id": "Unique event id.",
        "product_id": "FK to INP_PRODUCT.",
        "store_id": "FK to INP_STORE.",
        "shrink_date": "ISO YYYY-MM-DD.",
        "units_lost": "Written-off quantity.",
        "cost_lost": "Cost basis of loss.",
        "reason": "spoilage/damage/expired/theft/overstock/donation.",
    },
    "INP_POLICY": {
        "policy_key": "See known policy keys in the spec.",
        "policy_value": "Value as text.",
        "scope": "global | department | store.",
        "department": "Only when scope=department.",
        "description": "Human rationale.",
    },
    "INP_DEMAND_FORECAST": {
        "product_id": "FK to INP_PRODUCT.",
        "store_id": "FK to INP_STORE, or blank for chain-level.",
        "forecast_date": "Horizon date ISO YYYY-MM-DD.",
        "expected_units": "Mean point forecast.",
        "units_p10": "Downside estimate.",
        "units_p90": "Upside estimate.",
        "confidence": "0-1.",
    },
    "INP_ELASTICITY": {
        "product_id": "FK to INP_PRODUCT.",
        "elasticity": "d(ln q)/d(ln p); typically negative.",
        "quality": "measured | modeled | prior.",
        "confidence": "0-1.",
    },
}

SHEET_ORDER = list(SHEET_COLUMNS)


def _cell_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def build_workbook(rows_by_sheet: dict[str, list[dict]], output_path: str | os.PathLike) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    for sheet_name in SHEET_ORDER:
        columns = SHEET_COLUMNS[sheet_name]
        ws = wb.create_sheet(sheet_name)
        ws.append(columns)
        descriptions = SHEET_DESCRIPTIONS.get(sheet_name, {})
        ws.append([descriptions.get(col, "") for col in columns])

        for row in rows_by_sheet.get(sheet_name, []):
            ws.append([_cell_value(row.get(col)) for col in columns])

    wb.save(output_path)
