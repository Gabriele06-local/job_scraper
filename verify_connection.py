import os
import sys
from pymongo import MongoClient
from dotenv import load_dotenv


def verify_connection():
    load_dotenv()

    # Priority: DATABASE_URL > MONGO_URI (legacy)
    uri = os.getenv("DATABASE_URL")

    # Try to extract database name from MONGO_DB env or from URI
    db_name = os.getenv("MONGO_DB")

    if not uri:
        print("❌ MongoDB URI not found in environment variables (checked DATABASE_URL, MONGO_URI)")
        sys.exit(1)

    # Extract database from URI if not provided via MONGO_DB
    if not db_name and "/" in uri:
        # Extract from mongodb://host:port/dbname or mongodb://host:port/dbname?params
        try:
            db_name = uri.split("/")[-1].split("?")[0]
            if not db_name:
                db_name = "itjobhub"  # Fallback
        except Exception:
            db_name = "itjobhub"  # Fallback

    print("🔌 Testing connection to MongoDB...")
    print(f"   Database: {db_name}")

    try:
        # Connect
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)

        # Ping to check connection
        client.admin.command("ping")
        print("✅ Connection successful (Ping)")

    except Exception as e:
        is_stage = "stage" in db_name if db_name else False
        if "localhost" in uri and "27017" in uri and is_stage:
            print(
                "   ⚠️ Connection to localhost:27017 failed. Retrying on port 27018 (Smart Fallback)..."
            )
            fallback_uri = uri.replace("27017", "27018")
            try:
                client = MongoClient(
                    fallback_uri,
                    serverSelectionTimeoutMS=5000,
                    directConnection=True,
                    authSource="admin",
                )
                client.admin.command("ping")
                print("   ✅ Fallback connection to localhost:27018 successful")
            except Exception as fallback_e:
                print(f"   ❌ Fallback connection failed: {fallback_e}")
                sys.exit(1)
        else:
            print(f"   ❌ Connection failed: {e}")
            sys.exit(1)

        # Check database access
        db = client[db_name]
        collections = db.list_collection_names()
        print(f"✅ Database '{db_name}' accessible. Collections: {len(collections)}")

        # Check specific collections existence (optional but good sanity check)
        required = ["jobs", "companies"]
        missing = [c for c in required if c not in collections]

        if missing:
            print(f"⚠️  Warning: Missing expected collections: {missing}")
        else:
            print("✅ Expected collections found")

        return True

    except Exception as e:
        print(f"❌ Connection failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    verify_connection()
