import logging
from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient

from config import config

logger = logging.getLogger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_db = None


async def connect():
    global _client, _db
    if not config.MONGODB_URI:
        raise ValueError("MONGODB_URI is not configured in environment")
    _client = AsyncIOMotorClient(config.MONGODB_URI)
    try:
        _db = _client.get_default_database()
    except Exception:
        _db = _client["visa_bot"]
    await _db.applicants.create_index("id", unique=True)
    await _db.run_records.create_index([("applicant_id", 1), ("date", -1)])
    logger.info("MongoDB connected")


async def disconnect():
    global _client
    if _client:
        _client.close()
        logger.info("MongoDB disconnected")


async def load_data() -> dict:
    """Load applicants, schedule, and settings — mirrors the old JSON structure."""
    applicants = await _db.applicants.find({}, {"_id": 0}).to_list(None)

    schedule_doc = await _db.meta.find_one({"_id": "schedule"})
    settings_doc = await _db.meta.find_one({"_id": "settings"})

    schedule = (
        {k: v for k, v in schedule_doc.items() if k != "_id"}
        if schedule_doc
        else {"enabled": True, "days": []}
    )
    settings = (
        {k: v for k, v in settings_doc.items() if k != "_id"}
        if settings_doc
        else {"max_parallel": 10}
    )

    return {"applicants": applicants, "schedule": schedule, "settings": settings}


async def save_data(data: dict):
    """Persist data to MongoDB — mirrors the old JSON write."""
    # Upsert each applicant, delete removed ones
    applicants = data.get("applicants", [])
    existing_ids = []
    for app in applicants:
        await _db.applicants.replace_one({"id": app["id"]}, app, upsert=True)
        existing_ids.append(app["id"])

    if existing_ids:
        await _db.applicants.delete_many({"id": {"$nin": existing_ids}})
    else:
        await _db.applicants.delete_many({})

    if "schedule" in data:
        await _db.meta.replace_one(
            {"_id": "schedule"},
            {"_id": "schedule", **data["schedule"]},
            upsert=True,
        )

    if "settings" in data:
        await _db.meta.replace_one(
            {"_id": "settings"},
            {"_id": "settings", **data["settings"]},
            upsert=True,
        )


async def save_run_record(record: dict):
    """Insert a completed-session record for analytics."""
    await _db.run_records.insert_one({**record})


async def get_applicant_analytics():
    """Aggregate run records grouped by applicant."""
    pipeline = [
        {
            "$group": {
                "_id": "$applicant_id",
                "passport_number": {"$last": "$passport_number"},
                "total_tries": {"$sum": 1},
                "total_polls": {"$sum": "$poll_count"},
                "slots_found": {
                    "$sum": {"$cond": [{"$eq": ["$status", "slot_found"]}, 1, 0]}
                },
                "last_run": {"$max": "$ended_at"},
                "last_status": {"$last": "$status"},
                "center": {"$last": "$center"},
            }
        },
        {"$sort": {"last_run": -1}},
    ]
    results = await _db.run_records.aggregate(pipeline).to_list(None)
    for r in results:
        r["applicant_id"] = r.pop("_id")
    return results


async def get_global_stats():
    """Overall and today's statistics."""
    today = datetime.now().strftime("%Y-%m-%d")
    pipeline = [
        {
            "$facet": {
                "today": [
                    {"$match": {"date": today}},
                    {
                        "$group": {
                            "_id": None,
                            "tries": {"$sum": 1},
                            "polls": {"$sum": "$poll_count"},
                            "slots_found": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "slot_found"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    },
                ],
                "total": [
                    {
                        "$group": {
                            "_id": None,
                            "tries": {"$sum": 1},
                            "polls": {"$sum": "$poll_count"},
                            "slots_found": {
                                "$sum": {
                                    "$cond": [
                                        {"$eq": ["$status", "slot_found"]},
                                        1,
                                        0,
                                    ]
                                }
                            },
                        }
                    }
                ],
            }
        }
    ]
    result = await _db.run_records.aggregate(pipeline).to_list(1)
    empty = {"tries": 0, "polls": 0, "slots_found": 0}
    if result:
        r = result[0]
        today_data = r["today"][0] if r["today"] else empty
        total_data = r["total"][0] if r["total"] else empty
        today_data.pop("_id", None)
        total_data.pop("_id", None)
        return {"today": today_data, "total": total_data}
    return {"today": empty, "total": empty}
