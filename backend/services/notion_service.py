"""
Notion API integration service.
"""

from typing import List, Dict, Any
from notion_client import Client
from config import settings


class NotionService:
    """Service for interacting with Notion API."""

    def __init__(self):
        """Initialize Notion client with API key from settings."""
        self.client = Client(auth=settings.notion_api_key)
        self.database_id = settings.notion_database_id

    async def get_database_info(self) -> Dict[str, Any]:
        """
        Get database metadata.

        Returns:
            Dict containing database information
        """
        try:
            response = self.client.databases.retrieve(database_id=self.database_id)
            return {
                "success": True,
                "database_id": response["id"],
                "title": response.get("title", [{}])[0].get("plain_text", "Untitled"),
                "properties": list(response.get("properties", {}).keys()),
                "created_time": response.get("created_time"),
                "last_edited_time": response.get("last_edited_time"),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }

    async def query_database(self, page_size: int = 10) -> Dict[str, Any]:
        """
        Query database and retrieve pages.

        Args:
            page_size: Number of pages to retrieve (default: 10)

        Returns:
            Dict containing query results
        """
        try:
            response = self.client.databases.query(
                database_id=self.database_id,
                page_size=page_size
            )

            pages = []
            for page in response.get("results", []):
                page_data = {
                    "id": page["id"],
                    "created_time": page.get("created_time"),
                    "last_edited_time": page.get("last_edited_time"),
                    "properties": {}
                }

                # Extract property values
                for prop_name, prop_value in page.get("properties", {}).items():
                    page_data["properties"][prop_name] = self._extract_property_value(prop_value)

                pages.append(page_data)

            return {
                "success": True,
                "total_count": len(pages),
                "has_more": response.get("has_more", False),
                "pages": pages
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__
            }

    def _extract_property_value(self, prop: Dict[str, Any]) -> Any:
        """
        Extract value from Notion property object.

        Args:
            prop: Notion property object

        Returns:
            Extracted value
        """
        prop_type = prop.get("type")

        if prop_type == "title":
            return "".join([text.get("plain_text", "") for text in prop.get("title", [])])
        elif prop_type == "rich_text":
            return "".join([text.get("plain_text", "") for text in prop.get("rich_text", [])])
        elif prop_type == "number":
            return prop.get("number")
        elif prop_type == "select":
            return prop.get("select", {}).get("name")
        elif prop_type == "multi_select":
            return [item.get("name") for item in prop.get("multi_select", [])]
        elif prop_type == "date":
            date_obj = prop.get("date", {})
            if date_obj:
                return {
                    "start": date_obj.get("start"),
                    "end": date_obj.get("end")
                }
            return None
        elif prop_type == "checkbox":
            return prop.get("checkbox")
        elif prop_type == "url":
            return prop.get("url")
        elif prop_type == "email":
            return prop.get("email")
        elif prop_type == "phone_number":
            return prop.get("phone_number")
        else:
            return f"Unsupported type: {prop_type}"


def get_notion_service() -> NotionService:
    """
    Get NotionService instance.

    Returns:
        NotionService instance
    """
    return NotionService()
