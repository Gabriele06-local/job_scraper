import logging
from typing import Dict
from database.mongo_client import MongoDBClient

logger = logging.getLogger(__name__)


class JobDeduplicator:
    def __init__(self, db_client: MongoDBClient):
        self.db = db_client

    def is_duplicate(self, job: Dict) -> bool:
        """Check if job already exists in DB by link"""
        link = job.get("link")
        if not link:
            return False

        existing = self.db.jobs.find_one({"link": link})
        return existing is not None
