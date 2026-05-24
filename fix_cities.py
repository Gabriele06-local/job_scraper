import os
import certifi
from pymongo import MongoClient
from dotenv import load_dotenv

# Load env from parent directory (backend) or current
load_dotenv("../backend/.env")

uri = os.getenv("MONGODB_URI")
client = MongoClient(uri, tls=True, tlsCAFile=certifi.where())
db = client.get_database()
jobs_col = db.jobs

print(f"Connected to database: {db.name}")

# Find all jobs where city is an array
count = 0
for job in jobs_col.find({"city": {"$type": "array"}}):
    city_array = job["city"]
    if not city_array:
        new_city = None
    else:
        # Take the first city or join them
        new_city = str(city_array[0]) if len(city_array) > 0 else None

    jobs_col.update_one({"_id": job["_id"]}, {"$set": {"city": new_city}})
    count += 1
    print(f"Fixed job {job['_id']}: {city_array} -> {new_city}")

# Also check for any other weird types for city
for job in jobs_col.find({"city": {"$not": {"$type": ["string", "null"]}}}):
    if job.get("city") and not isinstance(job["city"], str):
        old_city = job["city"]
        new_city = str(old_city)
        jobs_col.update_one({"_id": job["_id"]}, {"$set": {"city": new_city}})
        count += 1
        print(f"Fixed weird type for job {job['_id']}: {old_city} ({type(old_city)}) -> {new_city}")

print(f"Finished. Fixed {count} jobs.")
client.close()
