# db.py
import os
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

load_dotenv()

col = MongoClient(os.environ["MONGO_URI"])["aadhar_verify"]["emails"]
col.create_index("email", unique=True)  # safe to run on every startup

def norm(email: str) -> str:
    return email.strip().lower()

# def exists(email: str) -> bool:
#     return col.find_one({"email": norm(email)}, {"_id": 1}) is not None

def exists(email: str) -> bool:
    return col.find_one({"email": norm(email)}, {"_id": 1}) is not None

def add(email: str) -> bool:
    """True if inserted, False if it already existed."""
    try:
        col.insert_one({"email": norm(email)})
        return True
    except DuplicateKeyError:
        return False

add("sumit@gmail.com")
add("dhruvil@gmail.com")
