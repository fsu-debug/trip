"""TRIP MCP Server — manage trips, places, and itineraries via AI tools."""
import asyncio
import logging

from fastmcp import FastMCP

from auth import api_delete, api_get, api_post, api_put
from log_config import TripToolLoggingMiddleware, log_startup_config, setup_logging

setup_logging()
logger = logging.getLogger("trip.mcp")

mcp = FastMCP("TRIP")
mcp.add_middleware(TripToolLoggingMiddleware())


# ── Response helpers ──


def _slim_place(place: dict) -> dict:
    category = place.get("category") or {}
    return {
        "id": place.get("id"),
        "name": place.get("name"),
        "lat": place.get("lat"),
        "lng": place.get("lng"),
        "category_id": category.get("id"),
        "category": category.get("name"),
        "description": (place.get("description") or "")[:200] or None,
        "price": place.get("price"),
        "duration": place.get("duration"),
        "visited": place.get("visited"),
    }


def _slim_provider_result(result: dict) -> dict:
    return {
        "name": result.get("name"),
        "place": result.get("place"),
        "category": result.get("category"),
        "lat": result.get("lat"),
        "lng": result.get("lng"),
        "description": (result.get("description") or "")[:200] or None,
        "price": result.get("price"),
    }


VALID_ITEM_STATUSES = {"pending", "booked", "constraint", "optional"}
STATUS_ALIASES = {
    "confirmed": "booked",
    "booked": "booked",
    "pending": "pending",
    "constraint": "constraint",
    "optional": "optional",
}


def _item_comment(item: dict) -> str | None:
    for key in ("comment", "notes", "description", "remarks"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _normalize_status(status: str) -> str | None:
    if not status:
        return None
    normalized = STATUS_ALIASES.get(status.lower().strip())
    if normalized in VALID_ITEM_STATUSES:
        return normalized
    return None


def _normalize_time(time: str) -> str | None:
    if not time:
        return None
    time = time.strip()
    if len(time) == 1:
        return f"0{time}:00"
    if len(time) == 2 and time.isdigit():
        return f"{time}:00"
    if ":" not in time and time.isdigit():
        return f"{time.zfill(2)}:00"
    return time


async def _find_item(trip_id: int, day_id: int, item_id: int) -> dict | None:
    trip = await _load_trip(trip_id)
    for day in trip.get("days", []):
        if day["id"] == day_id:
            for item in day.get("items", []):
                if item["id"] == item_id:
                    return item
    return None


async def _find_item_by_text(trip_id: int, day_id: int, text: str, time: str = "") -> dict | None:
    normalized_text = text.strip().casefold()
    normalized_time = _normalize_time(time) if time else None
    trip = await _load_trip(trip_id)
    for day in trip.get("days", []):
        if day["id"] != day_id:
            continue
        for item in day.get("items", []):
            if item.get("text", "").strip().casefold() != normalized_text:
                continue
            if normalized_time and item.get("time") != normalized_time:
                continue
            return item
    return None


def _slim_item(item: dict) -> dict:
    place = item.get("place") or {}
    slim = {
        "id": item.get("id"),
        "time": item.get("time"),
        "text": item.get("text"),
        "comment": item.get("comment"),
        "price": item.get("price"),
        "status": item.get("status"),
        "day_id": item.get("day_id"),
    }
    if place:
        slim["place_id"] = place.get("id")
        slim["place_name"] = place.get("name")
        slim["place_lat"] = place.get("lat")
        slim["place_lng"] = place.get("lng")
    slim["lat"] = item.get("lat")
    slim["lng"] = item.get("lng")
    return slim


def _slim_booking(booking: dict) -> dict:
    return {
        "id": booking.get("id"),
        "type": booking.get("type"),
        "label": booking.get("label"),
        "reference": booking.get("reference"),
        "notes": booking.get("notes"),
    }


def _slim_day(day: dict, *, with_items: bool) -> dict:
    slim = {
        "id": day["id"],
        "label": day.get("label"),
        "dt": day.get("dt"),
        "notes": day.get("notes"),
        "bookings": [_slim_booking(b) for b in day.get("bookings", [])],
    }
    items = day.get("items", [])
    if with_items:
        slim["items"] = [_slim_item(item) for item in items]
    else:
        slim["item_count"] = len(items)
    return slim


async def _load_trip(trip_id: int) -> dict:
    return await api_get(f"/api/trips/{trip_id}")


async def _ensure_place_linked_to_trip(trip_id: int, place_id: int) -> None:
    """TRIP requires places to be linked to a trip before they can be set on items."""
    if place_id <= 0:
        return
    trip = await _load_trip(trip_id)
    linked_ids = {place["id"] for place in trip.get("places", [])}
    if place_id in linked_ids:
        return
    await api_put(f"/api/trips/{trip_id}", {"place_ids": [*linked_ids, place_id]})
    logger.info("linked place %s to trip %s", place_id, trip_id)


def _comment_for_place_change(
    existing_comment: str | None,
    old_place_description: str | None,
    new_description: str | None,
) -> tuple[bool, str | None]:
    """Return whether to update comment and the new value when changing an item's place."""
    current = (existing_comment or "").strip()
    old = (old_place_description or "").strip()

    if not current:
        return True, new_description

    if current == old:
        return True, new_description

    if old:
        for separator in ("\n\n", "\r\n", "\r\n\r\n", "\n"):
            prefix = old + separator
            if current.startswith(prefix):
                suffix = current[len(prefix) :].strip()
                if not new_description:
                    return True, suffix or None
                new_text = new_description.strip()
                if suffix:
                    return True, f"{new_text}\n\n{suffix}"
                return True, new_description

    return False, None


async def _enrich_item_with_place(
    data: dict,
    trip_id: int,
    place_id: int,
    *,
    text: str = "",
    existing_comment: str | None = None,
    old_place_id: int | None = None,
    old_place_description: str | None = None,
    fill_text: bool = True,
    fill_price: bool = True,
    fill_comment: bool = True,
) -> None:
    """Mirror the frontend place picker: copy coords and optional fields from the place."""
    await _ensure_place_linked_to_trip(trip_id, place_id)
    place = await api_get(f"/api/places/{place_id}")
    if not place:
        raise RuntimeError(f"Place {place_id} not found")

    data["place"] = place_id
    data["lat"] = place["lat"]
    data["lng"] = place["lng"]

    if fill_price:
        data["price"] = place.get("price") or 0

    if fill_text and not text and not data.get("text") and place.get("name"):
        data["text"] = place["name"]

    if fill_comment and "comment" not in data:
        new_description = place.get("description")

        if old_place_id is not None and old_place_id != place_id:
            should_update, new_comment = _comment_for_place_change(
                existing_comment,
                old_place_description,
                new_description,
            )
            if should_update:
                data["comment"] = new_comment
        elif not (existing_comment or "").strip() and new_description:
            data["comment"] = new_description

    logger.info(
        "applied place %s to item payload (lat=%s, lng=%s)",
        place_id,
        data["lat"],
        data["lng"],
    )


async def _resolve_category_id(category_name: str | None) -> int:
    categories = await api_get("/api/categories")
    if category_name:
        for cat in categories:
            if cat.get("name", "").lower() == category_name.lower():
                return cat["id"]
    return categories[0]["id"] if categories else 1


async def _create_place_from_result(result: dict, category_name: str = "") -> dict:
    category_id = await _resolve_category_id(category_name or result.get("category"))
    data = {
        "name": result.get("name") or result.get("place") or "Unknown",
        "lat": result["lat"],
        "lng": result["lng"],
        "place": result.get("place") or result.get("name"),
        "description": result.get("description") or "",
        "price": result.get("price") or 0,
        "duration": 60,
        "category_id": category_id,
    }
    if result.get("image"):
        data["image"] = result["image"]
    place = await api_post("/api/places", data)
    return _slim_place(place)


# ── Trips ──


@mcp.tool()
async def create_trip(name: str, currency: str = "EUR") -> dict:
    """Create a new trip."""
    return await api_post("/api/trips", {"name": name, "currency": currency})


@mcp.tool()
async def list_trips() -> list:
    """List all trips."""
    return await api_get("/api/trips")


@mcp.tool()
async def get_trip(trip_id: int) -> dict:
    """Get full trip with days, items, and places. Avoid for large trips — use get_trip_overview and get_day instead."""
    return await api_get(f"/api/trips/{trip_id}")


@mcp.tool()
async def get_trip_overview(trip_id: int) -> dict:
    """Trip summary with compact day list (no items or place details)."""
    trip = await _load_trip(trip_id)
    return {
        "id": trip["id"],
        "name": trip.get("name"),
        "currency": trip.get("currency"),
        "notes": trip.get("notes"),
        "archived": trip.get("archived"),
        "days": [_slim_day(day, with_items=False) for day in trip.get("days", [])],
        "place_count": len(trip.get("places", [])),
    }


@mcp.tool()
async def list_trip_days(trip_id: int) -> list:
    """List days of a trip (id, label, date, item count, bookings). Use get_day for item details."""
    trip = await _load_trip(trip_id)
    return [_slim_day(day, with_items=False) for day in trip.get("days", [])]


@mcp.tool()
async def get_day(trip_id: int, day_id: int) -> dict:
    """Get a single day with compact items and bookings. Prefer over get_trip for large trips."""
    trip = await _load_trip(trip_id)
    for day in trip.get("days", []):
        if day["id"] == day_id:
            return _slim_day(day, with_items=True)
    return {}


@mcp.tool()
async def get_trip_balance(trip_id: int) -> dict:
    """Cost balance per trip member (who paid how much vs. fair share). Requires 2+ members."""
    return await api_get(f"/api/trips/{trip_id}/balance")


@mcp.tool()
async def list_trip_places(trip_id: int) -> list:
    """List places linked to a trip (compact)."""
    trip = await _load_trip(trip_id)
    return [_slim_place(place) for place in trip.get("places", [])]


@mcp.tool()
async def add_place_to_trip(trip_id: int, place_id: int) -> dict:
    """Link a place to a trip without removing existing links."""
    await _ensure_place_linked_to_trip(trip_id, place_id)
    trip = await _load_trip(trip_id)
    return {"trip_id": trip_id, "place_ids": [place["id"] for place in trip.get("places", [])]}


@mcp.tool()
async def list_trip_members(trip_id: int) -> list:
    """List collaborators on a trip."""
    return await api_get(f"/api/trips/{trip_id}/members")


@mcp.tool()
async def update_trip(trip_id: int, name: str = "", currency: str = "", notes: str = "") -> dict:
    """Update trip name, currency, or notes."""
    data = {k: v for k, v in {"name": name, "currency": currency, "notes": notes}.items() if v}
    return await api_put(f"/api/trips/{trip_id}", data)


@mcp.tool()
async def delete_trip(trip_id: int) -> dict:
    """Delete a trip."""
    return await api_delete(f"/api/trips/{trip_id}")


@mcp.tool()
async def link_places(trip_id: int, place_ids: list[int]) -> dict:
    """Replace the full set of places linked to a trip. Prefer add_place_to_trip to append a single place."""
    return await api_put(f"/api/trips/{trip_id}", {"place_ids": place_ids})


# ── Days ──


@mcp.tool()
async def add_day(trip_id: int, label: str, date: str = "") -> dict:
    """Add a day. Date: YYYY-MM-DD."""
    data = {"label": label}
    if date:
        data["dt"] = date
    return await api_post(f"/api/trips/{trip_id}/days", data)


@mcp.tool()
async def update_day(trip_id: int, day_id: int, label: str, date: str = "", notes: str = "") -> dict:
    """Update a day. label is required (use get_day to retrieve current values)."""
    data: dict = {"label": label}
    if date:
        data["dt"] = date
    if notes:
        data["notes"] = notes
    return await api_put(f"/api/trips/{trip_id}/days/{day_id}", data)


@mcp.tool()
async def delete_day(trip_id: int, day_id: int) -> dict:
    """Delete a day."""
    return await api_delete(f"/api/trips/{trip_id}/days/{day_id}")


@mcp.tool()
async def duplicate_day(trip_id: int, source_day_id: int, label: str, date: str = "") -> dict:
    """Copy a day and all its items to a new day."""
    trip = await _load_trip(trip_id)
    source = next((day for day in trip.get("days", []) if day["id"] == source_day_id), None)
    if not source:
        return {}

    day_data = {"label": label}
    if date:
        day_data["dt"] = date
    new_day = await api_post(f"/api/trips/{trip_id}/days", day_data)
    new_day_id = new_day["id"]

    created = []
    for item in source.get("items", []):
        item_data = {
            "text": item.get("text", ""),
            "time": item.get("time", "09:00"),
            "price": item.get("price") or 0,
        }
        place = item.get("place")
        if place:
            item_data["place"] = place["id"]
        normalized_status = _normalize_status(item.get("status", ""))
        if normalized_status:
            item_data["status"] = normalized_status
        comment = _item_comment(item)
        if comment:
            item_data["comment"] = comment
        created.append(
            await api_post(f"/api/trips/{trip_id}/days/{new_day_id}/items", item_data)
        )

    return {"day": new_day, "items": [_slim_item(i) for i in created]}


# ── Items ──


@mcp.tool()
async def add_item(
    trip_id: int,
    day_id: int,
    text: str,
    time: str = "09:00",
    price: float = 0,
    place_id: int = 0,
    comment: str = "",
    notes: str = "",
) -> dict:
    """Create a NEW plan item on a day. Do NOT use to edit existing items — call get_day first, then update_item(item_id=...) or set_item_comment(item_id=...)."""
    existing = await _find_item_by_text(trip_id, day_id, text, time)
    if existing:
        raise RuntimeError(
            f"Item already exists on day {day_id} (id={existing['id']}, text={existing.get('text')!r}). "
            f"Use update_item(trip_id={trip_id}, day_id={day_id}, item_id={existing['id']}, ...) "
            f"or set_item_comment(item_id={existing['id']}, comment=...) instead of add_item."
        )

    data = {"text": text, "time": _normalize_time(time) or time, "price": price}
    effective_comment = comment or notes
    if effective_comment:
        data["comment"] = effective_comment
    if place_id and place_id > 0:
        await _enrich_item_with_place(
            data,
            trip_id,
            place_id,
            text=text,
            existing_comment=effective_comment or None,
            fill_price=price == 0,
            fill_comment=not effective_comment,
        )
    logger.info("add_item: trip=%s day=%s payload=%s", trip_id, day_id, data)
    item = await api_post(f"/api/trips/{trip_id}/days/{day_id}/items", data)
    return _slim_item(item)


@mcp.tool()
async def bulk_add_items(trip_id: int, day_id: int, items: list[dict]) -> list:
    """Add multiple items to a day. Each item: {text, time?, price?, place_id?, status?, comment?}."""
    created = []
    for item in items:
        data = {
            "text": item["text"],
            "time": item.get("time", "09:00"),
            "price": item.get("price", 0),
        }
        comment = _item_comment(item)
        if comment:
            data["comment"] = comment
        if item.get("place_id"):
            await _enrich_item_with_place(
                data,
                trip_id,
                item["place_id"],
                text=item.get("text", ""),
                existing_comment=comment,
                fill_price=item.get("price") in (None, 0),
                fill_comment=not comment,
            )
        normalized_status = _normalize_status(item.get("status", ""))
        if normalized_status:
            data["status"] = normalized_status
        created.append(await api_post(f"/api/trips/{trip_id}/days/{day_id}/items", data))
    return [_slim_item(i) for i in created]


@mcp.tool()
async def update_item(
    trip_id: int,
    day_id: int,
    item_id: int,
    text: str = "",
    time: str = "",
    price: float | None = None,
    status: str = "",
    comment: str | None = None,
    notes: str | None = None,
    place_id: int | None = None,
    remove_place: bool = False,
) -> dict:
    """Update an EXISTING item (requires item_id from get_day). Use for text, time, comment, status, or place changes. Changing place_id replaces the old place description in comment (including when extra lines were added below it); other custom edits are kept. Status: pending, booked, constraint, optional (booked=confirmed)."""
    current = await _find_item(trip_id, day_id, item_id)
    if not current:
        raise RuntimeError(f"Item {item_id} not found on day {day_id} of trip {trip_id}")

    data: dict = {}
    if text:
        data["text"] = text
    normalized_time = _normalize_time(time)
    if normalized_time:
        data["time"] = normalized_time
    if price is not None:
        data["price"] = price
    normalized_status = _normalize_status(status)
    if normalized_status:
        data["status"] = normalized_status
    effective_comment = comment if comment is not None else notes
    if effective_comment is not None:
        data["comment"] = str(effective_comment)

    if remove_place:
        data["place"] = None
        data["lat"] = None
        data["lng"] = None
    elif place_id is not None and place_id > 0:
        existing_text = current.get("text", "")
        old_place = current.get("place") or {}
        await _enrich_item_with_place(
            data,
            trip_id,
            place_id,
            text=text or existing_text,
            existing_comment=current.get("comment"),
            old_place_id=old_place.get("id"),
            old_place_description=old_place.get("description"),
            fill_text=not text,
            fill_price=price is None,
            fill_comment=effective_comment is None,
        )

    if not data:
        logger.warning(
            "update_item: no fields to update for trip=%s day=%s item=%s (text=%r time=%r comment=%r notes=%r status=%r)",
            trip_id,
            day_id,
            item_id,
            text,
            time,
            comment,
            notes,
            status,
        )
        raise RuntimeError("No fields to update")

    logger.info(
        "update_item: trip=%s day=%s item=%s payload=%s",
        trip_id,
        day_id,
        item_id,
        data,
    )
    item = await api_put(f"/api/trips/{trip_id}/days/{day_id}/items/{item_id}", data)
    return _slim_item(item)


@mcp.tool()
async def set_item_comment(
    trip_id: int,
    day_id: int,
    item_id: int,
    comment: str,
    notes: str = "",
) -> dict:
    """Set the comment on an existing plan item. Call get_day first to obtain item_id."""
    effective_comment = comment or notes
    if not effective_comment:
        raise RuntimeError("comment is required")
    return await update_item(
        trip_id=trip_id,
        day_id=day_id,
        item_id=item_id,
        comment=effective_comment,
    )


@mcp.tool()
async def delete_item(trip_id: int, day_id: int, item_id: int) -> dict:
    """Delete an item."""
    return await api_delete(f"/api/trips/{trip_id}/days/{day_id}/items/{item_id}")


# ── Bookings ──


@mcp.tool()
async def add_booking(
    trip_id: int,
    day_id: int,
    label: str,
    booking_type: str = "generic",
    reference: str = "",
    notes: str = "",
) -> dict:
    """Add a booking to a day. Types: flight, car, hotel, activity, generic."""
    data = {"label": label, "type": booking_type}
    if reference:
        data["reference"] = reference
    if notes:
        data["notes"] = notes
    return await api_post(f"/api/trips/{trip_id}/days/{day_id}/bookings", data)


@mcp.tool()
async def update_booking(
    booking_id: int,
    label: str = "",
    booking_type: str = "",
    reference: str = "",
    notes: str = "",
    trip_id: int = 0,
    day_id: int = 0,
) -> dict:
    """Update a booking. Pass trip_id+day_id to merge with existing values for partial updates."""
    current = {"label": "Booking", "type": "generic", "reference": None, "notes": None}
    if trip_id and day_id:
        trip = await _load_trip(trip_id)
        for day in trip.get("days", []):
            if day["id"] == day_id:
                for booking in day.get("bookings", []):
                    if booking["id"] == booking_id:
                        current = booking
                        break
    data = {
        "label": label or current.get("label"),
        "type": booking_type or current.get("type", "generic"),
        "reference": reference or current.get("reference"),
        "notes": notes or current.get("notes"),
    }
    return await api_put(f"/api/bookings/{booking_id}", data)


@mcp.tool()
async def delete_booking(booking_id: int) -> dict:
    """Delete a booking."""
    return await api_delete(f"/api/bookings/{booking_id}")


# ── Places ──


@mcp.tool()
async def search_places(query: str) -> list:
    """Search for places via map provider (text query). Returns compact results, not yet saved."""
    results = await api_get("/api/completions/search", params={"q": query})
    return [_slim_provider_result(r) for r in results]


@mcp.tool()
async def import_place_from_google(query_or_url: str, category: str = "") -> dict:
    """Resolve a Google Maps URL or search query and create a place."""
    results = await api_post("/api/completions/bulk", [query_or_url])
    if not results:
        return {}
    return await _create_place_from_result(results[0], category)


@mcp.tool()
async def geocode(query: str) -> dict:
    """Geocode an address or place name to map boundaries."""
    return await api_get("/api/completions/geocode", params={"q": query})


@mcp.tool()
async def search_nearby(latitude: float, longitude: float) -> list:
    """Search for nearby places at coordinates."""
    results = await api_post("/api/completions/nearby", {"latitude": latitude, "longitude": longitude})
    return [_slim_provider_result(r) for r in results]


@mcp.tool()
async def get_route(
    from_lat: float,
    from_lng: float,
    to_lat: float,
    to_lng: float,
    profile: str = "car",
) -> dict:
    """Get route between two points. Profiles: car, foot, bike (transit if Google API key set)."""
    data = {
        "coordinates": [{"lat": from_lat, "lng": from_lng}, {"lat": to_lat, "lng": to_lng}],
        "profile": profile,
    }
    return await api_post("/api/completions/route", data)


@mcp.tool()
async def create_place(
    name: str,
    lat: float,
    lng: float,
    category_id: int = 1,
    description: str = "",
    price: float = 0,
    duration: int = 60,
    image_url: str = "",
) -> dict:
    """Create a place. Pass image_url for a photo (server downloads automatically)."""
    data = {
        "name": name,
        "lat": lat,
        "lng": lng,
        "place": name,
        "description": description,
        "price": price,
        "duration": duration,
        "category_id": category_id,
    }
    if image_url:
        data["image"] = image_url
    place = await api_post("/api/places", data)
    return _slim_place(place)


@mcp.tool()
async def list_places() -> list:
    """List all places (compact). Use get_place for full details."""
    places = await api_get("/api/places")
    return [_slim_place(place) for place in places]


@mcp.tool()
async def get_place(place_id: int) -> dict:
    """Get a single place with details (no GPX data)."""
    place = await api_get(f"/api/places/{place_id}")
    slim = _slim_place(place)
    if place.get("description") and len(place["description"]) > 200:
        slim["description"] = place["description"]
    slim["links"] = place.get("links")
    slim["allowdog"] = place.get("allowdog")
    slim["restroom"] = place.get("restroom")
    return slim


@mcp.tool()
async def update_place(
    place_id: int,
    name: str = "",
    description: str = "",
    category_id: int | None = None,
    category: str = "",
    lat: float | None = None,
    lng: float | None = None,
    price: float | None = None,
    duration: int | None = None,
) -> dict:
    """Update a place. Use category_id or category (name) to change its category."""
    data: dict = {}
    if name:
        data["name"] = name
    if description:
        data["description"] = description
    if category_id is not None and category_id > 0:
        data["category_id"] = category_id
    elif category:
        data["category_id"] = await _resolve_category_id(category)
    if lat is not None:
        data["lat"] = lat
    if lng is not None:
        data["lng"] = lng
    if price is not None:
        data["price"] = price
    if duration is not None:
        data["duration"] = duration
    if not data:
        raise RuntimeError("No fields to update")
    logger.info("update_place: id=%s payload=%s", place_id, data)
    place = await api_put(f"/api/places/{place_id}", data)
    return _slim_place(place)


@mcp.tool()
async def delete_place(place_id: int) -> dict:
    """Delete a place."""
    return await api_delete(f"/api/places/{place_id}")


# ── Categories ──


@mcp.tool()
async def list_categories() -> list:
    """List place categories."""
    return await api_get("/api/categories")


@mcp.tool()
async def create_category(name: str, color: str = "#3B82F6") -> dict:
    """Create a category."""
    return await api_post("/api/categories", {"name": name, "color": color})


# ── Packing & Checklist ──


@mcp.tool()
async def list_packing(trip_id: int) -> list:
    """List packing items for a trip."""
    return await api_get(f"/api/trips/{trip_id}/packing")


@mcp.tool()
async def add_packing_item(trip_id: int, text: str, category: str = "other", quantity: int = 1) -> dict:
    """Add packing item. Categories: clothes, toiletries, tech, documents, other."""
    return await api_post(
        f"/api/trips/{trip_id}/packing", {"text": text, "category": category, "qt": quantity}
    )


@mcp.tool()
async def update_packing_item(
    trip_id: int,
    item_id: int,
    text: str = "",
    category: str = "",
    quantity: int | None = None,
    packed: bool | None = None,
) -> dict:
    """Update a packing list item."""
    data = {}
    if text:
        data["text"] = text
    if category:
        data["category"] = category
    if quantity is not None:
        data["qt"] = quantity
    if packed is not None:
        data["packed"] = packed
    return await api_put(f"/api/trips/{trip_id}/packing/{item_id}", data)


@mcp.tool()
async def delete_packing_item(trip_id: int, item_id: int) -> dict:
    """Delete a packing list item."""
    return await api_delete(f"/api/trips/{trip_id}/packing/{item_id}")


@mcp.tool()
async def list_checklist(trip_id: int) -> list:
    """List pre-trip checklist items."""
    return await api_get(f"/api/trips/{trip_id}/checklist")


@mcp.tool()
async def add_checklist_item(trip_id: int, text: str) -> dict:
    """Add pre-trip checklist item."""
    return await api_post(f"/api/trips/{trip_id}/checklist", {"text": text})


@mcp.tool()
async def update_checklist_item(
    trip_id: int, item_id: int, text: str = "", checked: bool | None = None
) -> dict:
    """Update a checklist item (text or checked status)."""
    data = {}
    if text:
        data["text"] = text
    if checked is not None:
        data["checked"] = checked
    return await api_put(f"/api/trips/{trip_id}/checklist/{item_id}", data)


@mcp.tool()
async def delete_checklist_item(trip_id: int, item_id: int) -> dict:
    """Delete a checklist item."""
    return await api_delete(f"/api/trips/{trip_id}/checklist/{item_id}")


# ── Sharing ──


@mcp.tool()
async def share_trip(trip_id: int, full_access: bool = False) -> dict:
    """Create a share link. full_access=True allows editing."""
    return await api_post(f"/api/trips/{trip_id}/share", {"is_full_access": full_access})


@mcp.tool()
async def invite_member(trip_id: int, username: str) -> dict:
    """Invite a user to collaborate."""
    return await api_post(f"/api/trips/{trip_id}/members", {"user": username})


if __name__ == "__main__":
    log_startup_config()
    tool_count = len(asyncio.run(mcp.list_tools()))
    logger.info("registered %d MCP tools", tool_count)
    logger.info("listening on http://0.0.0.0:3001/mcp")
    mcp.run(transport="http", host="0.0.0.0", port=3001)