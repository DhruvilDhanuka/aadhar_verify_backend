import os
from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse

router = APIRouter()

@router.get("/webhook/whatsapp")
async def verify_webhook(
    mode: str = Query(alias="hub.mode"),
    token: str = Query(alias="hub.verify_token"),
    challenge: str = Query(alias="hub.challenge"),
):
    verify_token = os.getenv("WA_VERIFY_TOKEN")
    if verify_token and mode == "subscribe" and token == verify_token:
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)

@router.post("/webhook/whatsapp")
async def receive_webhook(request: Request):
    data = await request.json()
    print("WA webhook:", data)  # shows up in Render logs
    return {"ok": True}