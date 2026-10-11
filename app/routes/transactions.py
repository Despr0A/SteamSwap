"""Read-only records for confirmed purchases."""
from fastapi import APIRouter
from app.state import get_purchases

router = APIRouter()


@router.get("/api/purchases")
def api_purchases():
    fields = ("name", "price", "market_price", "at", "purchase_ratio")
    purchases = [
        {key: purchase.get(key) for key in fields}
        for purchase in get_purchases()
        if purchase.get("payment_confirmed") is True
    ]
    purchases.sort(key=lambda purchase: purchase.get("at") or 0, reverse=True)
    return {"purchases": purchases}
