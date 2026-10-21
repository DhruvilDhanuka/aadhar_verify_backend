from pymongo import MongoClient
import os
from dotenv import load_dotenv
load_dotenv()


uri = os.environ["MONGO_URI"]
client = MongoClient(uri)
try:
    client.admin.command("ping")
    print("Connected successfully")
    client.close()
except Exception as e:
    raise Exception(
        "The following error occurred: ", e)