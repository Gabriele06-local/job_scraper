import os
import certifi
from pymongo import MongoClient
from datetime import datetime
from dotenv import load_dotenv

# Load env from parent directory (backend) or current
load_dotenv("../backend/.env")

uri = os.getenv("MONGODB_URI")
client = MongoClient(uri, tls=True, tlsCAFile=certifi.where())
db = client.get_database()
jobs_col = db.jobs

print(f"Connected to database: {db.name}")

# Find all jobs where published_at is a string
count = 0
for job in jobs_col.find({"published_at": {"$type": "string"}}):
    pub_at_str = job["published_at"]
    try:
        # Try common formats
        dt = None
        fmts = [
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%d",
            "%d %b %Y",
        ]
        for fmt in fmts:
            try:
                dt = datetime.strptime(pub_at_str, fmt)
                break
            except ValueError:
                continue

        if dt:
            jobs_col.update_one({"_id": job["_id"]}, {"$set": {"published_at": dt}})
            count += 1
            print(f"Fixed job {job['_id']}: {pub_at_str} -> {dt}")
        else:
            print(f"Could not parse date for job {job['_id']}: {pub_at_str}")

    except Exception as e:
        print(f"Error fixing job {job['_id']}: {e}")

print(f"Finished. Fixed {count} jobs.")
client.close()
