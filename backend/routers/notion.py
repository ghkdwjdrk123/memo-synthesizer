"""
Notion API endpoints.
"""

from fastapi import APIRouter, Depends, Query

from services.notion_service import NotionService, get_notion_service
from schemas.notion import NotionDatabaseInfo, NotionQueryResult

router = APIRouter(prefix="/notion", tags=["Notion"])


@router.get(
    "/database/info",
    response_model=NotionDatabaseInfo,
    summary="Get Database Info",
    description="Retrieve metadata about the configured Notion database."
)
async def get_database_info(
    notion_service: NotionService = Depends(get_notion_service)
) -> NotionDatabaseInfo:
    """
    Get Notion database information.

    Returns:
        NotionDatabaseInfo: Database metadata including title, properties, and timestamps
    """
    result = await notion_service.get_database_info()
    return NotionDatabaseInfo(**result)


@router.get(
    "/database/query",
    response_model=NotionQueryResult,
    summary="Query Database",
    description="Query the Notion database and retrieve pages."
)
async def query_database(
    page_size: int = Query(default=10, ge=1, le=100, description="Number of pages to retrieve"),
    notion_service: NotionService = Depends(get_notion_service)
) -> NotionQueryResult:
    """
    Query Notion database and retrieve pages.

    Args:
        page_size: Number of pages to retrieve (1-100)

    Returns:
        NotionQueryResult: Query results with page data
    """
    result = await notion_service.query_database(page_size=page_size)
    return NotionQueryResult(**result)
