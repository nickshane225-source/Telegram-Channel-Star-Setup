import asyncio
import csv
import io
from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger
from app.models.sale import SaleModel
from app.repositories.activity import activity_repo
from app.repositories.posts import posts_repo
from app.repositories.purchases import purchases_repo
from app.repositories.sales import sales_repo
from app.services.telegram import telegram_service


class StarsService:
    """Service for synchronizing and reporting Telegram Stars earnings."""

    def __init__(self, repo=sales_repo, pur_repo=purchases_repo, tg=telegram_service):
        self.repo = repo
        self.purchases_repo = pur_repo
        self.tg = tg

    async def sync_stars_transactions(self) -> int:
        """Fetch latest Star transactions from Telegram and store in database."""
        try:
            transactions = await self.tg.get_star_transactions(offset=0, limit=100)
            new_count = 0

            for tx in transactions:
                tx_id = tx.get("id")
                amount = tx.get("amount", 0)
                date_ts = tx.get("date", 0)
                date_dt = datetime.utcfromtimestamp(date_ts) if date_ts else datetime.utcnow()

                source = tx.get("source", {})
                user_info = source.get("user", {})
                user_id = user_info.get("id")
                user_name = user_info.get("username") or user_info.get("first_name")

                # Try to extract post_id or telegram_message_id if stored in payload or invoice
                invoice_payload = (
                    source.get("invoice_payload")
                    or source.get("paid_media_payload")
                    or source.get("payload")
                    or tx.get("payload")
                    or ""
                )
                matched_post_id: Optional[str] = None
                matched_msg_id: Optional[int] = None

                if invoice_payload.startswith("u:"):
                    matched_post_id = invoice_payload[2:].strip()
                elif invoice_payload.startswith("post_"):
                    matched_post_id = invoice_payload.replace("post_", "").strip()
                elif invoice_payload.startswith("msg_"):
                    try:
                        matched_msg_id = int(invoice_payload.replace("msg_", ""))
                    except ValueError:
                        pass

                # If message ID was matched, find the corresponding post
                if matched_msg_id and not matched_post_id:
                    post = await posts_repo.get_by_telegram_message_id(matched_msg_id)
                    if post:
                        matched_post_id = post.id

                sale = SaleModel(
                    transaction_id=tx_id,
                    star_amount=amount,
                    post_id=matched_post_id,
                    telegram_message_id=matched_msg_id,
                    user_id=user_id,
                    user_name=user_name,
                    date=date_dt,
                    raw_data=tx
                )

                inserted = await self.repo.upsert_transaction(sale)
                if inserted:
                    new_count += 1
                    if matched_post_id:
                        await posts_repo.update_stars_earned(matched_post_id, amount)

            if new_count > 0:
                logger.info(f"Synced {new_count} new Star transactions.")
                await activity_repo.log(
                    action="stars_synced",
                    details=f"Synced {new_count} new Star transactions from Telegram",
                    level="info"
                )

            return new_count

        except Exception as exc:
            logger.warning(f"Failed to sync Star transactions: {exc}")
            return 0

    async def get_dashboard_stars_summary(self) -> Dict[str, Any]:
        """Summary metrics for the dashboard combining channel sales and bot purchases."""
        total_sales, today_sales, total_purchases, today_purchases = await asyncio.gather(
            self.repo.get_total_stars(),
            self.repo.get_stars_today(),
            self.purchases_repo.get_total_stars(),
            self.purchases_repo.get_stars_today(),
        )
        return {
            "total_stars": total_sales + total_purchases,
            "today_stars": today_sales + today_purchases,
        }

    async def get_chart_data(self, days: int = 30) -> Dict[str, Any]:
        """Daily breakdown for Chart.js chart combining channel sales and bot purchases."""
        sales_chart, purchases_chart = await asyncio.gather(
            self.repo.get_chart_data(days=days),
            self.purchases_repo.get_chart_data(days=days),
        )

        labels = sales_chart.get("labels", [])
        sales_values = sales_chart.get("values", [0] * len(labels))
        purchases_values = purchases_chart.get("values", [0] * len(labels))

        combined_values = [s + p for s, p in zip(sales_values, purchases_values)]
        return {
            "labels": labels,
            "values": combined_values,
            "total": sum(combined_values),
        }

    async def export_sales_csv(self) -> str:
        """Export all sales transactions as CSV text."""
        records = await self.repo.get_all_sales_for_export()
        output = io.StringIO()
        if not records:
            writer = csv.writer(output)
            writer.writerow(["Transaction ID", "Date (UTC)", "Stars Amount", "Telegram Message ID", "User ID", "User Name", "Post ID"])
            return output.getvalue()

        fieldnames = list(records[0].keys())
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for r in records:
            writer.writerow(r)

        return output.getvalue()


stars_service = StarsService()
