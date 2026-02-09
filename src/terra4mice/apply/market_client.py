"""
HTTP client for Execution Market API integration.

Handles task creation, status polling, and result retrieval
for market-mode resource implementation.
"""

from __future__ import annotations

import os
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Union


class MarketAPIError(Exception):
    """Exception raised for Execution Market API errors."""
    
    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass
class MarketTask:
    """Dataclass representing an Execution Market task."""
    
    id: str
    title: str
    description: str
    status: str
    tags: list[str]
    metadata: dict
    created_at: datetime
    worker_id: Optional[str] = None
    result: Optional[dict] = None


class MarketClient:
    """HTTP client for Execution Market API integration."""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.execution.market",
        timeout: float = 10.0,
        dry_run: bool = False,
    ):
        """
        Initialize the Market API client.
        
        Args:
            api_key: API key for authentication. If None, uses EXECUTION_MARKET_API_KEY env var.
            base_url: Base URL for the Execution Market API.
            timeout: Request timeout in seconds.
            dry_run: If True, log operations but don't actually make requests.
        """
        self.api_key = api_key or os.getenv("EXECUTION_MARKET_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.dry_run = dry_run
        
        if not self.api_key and not self.dry_run:
            raise MarketAPIError("API key required. Set EXECUTION_MARKET_API_KEY environment variable or pass api_key parameter.")
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict] = None,
    ) -> dict:
        """
        Make an HTTP request to the Execution Market API.
        
        Args:
            method: HTTP method (GET, POST, DELETE, etc.)
            endpoint: API endpoint path (e.g., "/v1/tasks")
            data: Optional request body data (will be JSON-encoded)
            
        Returns:
            Parsed JSON response as a dict
            
        Raises:
            MarketAPIError: For HTTP errors or API-level errors
        """
        url = f"{self.base_url}{endpoint}"
        
        # Prepare request
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "terra4mice/1.0",
        }
        
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        request_body = None
        if data is not None:
            request_body = json.dumps(data).encode("utf-8")
        
        if self.dry_run:
            print(f"[DRY RUN] {method} {url}")
            if request_body:
                print(f"[DRY RUN] Body: {request_body.decode('utf-8')}")
            
            # Return a mock response for dry run
            if method == "POST" and "/tasks" in endpoint:
                return {
                    "id": "mock-task-id",
                    "title": data.get("title", "Mock Task") if data else "Mock Task",
                    "description": data.get("description", "Mock description") if data else "Mock description",
                    "status": "pending",
                    "tags": data.get("tags", []) if data else [],
                    "metadata": data.get("metadata", {}) if data else {},
                    "created_at": datetime.now().isoformat(),
                }
            elif method == "GET" and "/tasks/" in endpoint:
                task_id = endpoint.split("/")[-1]
                return {
                    "id": task_id,
                    "title": "Mock Task",
                    "description": "Mock description",
                    "status": "pending",
                    "tags": [],
                    "metadata": {},
                    "created_at": datetime.now().isoformat(),
                }
            elif method == "GET" and endpoint.endswith("/tasks"):
                return {"tasks": []}
            elif method == "DELETE":
                return {"success": True}
            else:
                return {}
        
        try:
            req = urllib.request.Request(url, data=request_body, headers=headers, method=method)
            
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                response_body = response.read().decode("utf-8")
                
                if response.status >= 400:
                    raise MarketAPIError(
                        f"HTTP {response.status}: {response.reason}",
                        status_code=response.status,
                        response_body=response_body
                    )
                
                try:
                    return json.loads(response_body)
                except json.JSONDecodeError as e:
                    raise MarketAPIError(f"Invalid JSON response: {e}", response_body=response_body)
                    
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8")
            except:
                pass
            
            raise MarketAPIError(
                f"HTTP {e.code}: {e.reason}",
                status_code=e.code,
                response_body=error_body
            )
            
        except urllib.error.URLError as e:
            raise MarketAPIError(f"Network error: {e.reason}")
            
        except TimeoutError:
            raise MarketAPIError(f"Request timed out after {self.timeout}s")
    
    def create_task(self, task: dict) -> MarketTask:
        """
        Create a new task on the Execution Market.
        
        Args:
            task: Task data dictionary with keys: title, description, type, tags, metadata
            
        Returns:
            MarketTask instance for the created task
            
        Raises:
            MarketAPIError: If the API request fails
        """
        response = self._make_request("POST", "/v1/tasks", task)
        
        return MarketTask(
            id=response["id"],
            title=response["title"],
            description=response["description"],
            status=response["status"],
            tags=response.get("tags", []),
            metadata=response.get("metadata", {}),
            created_at=datetime.fromisoformat(response["created_at"].replace("Z", "+00:00")),
            worker_id=response.get("worker_id"),
            result=response.get("result"),
        )
    
    def get_task(self, task_id: str) -> MarketTask:
        """
        Retrieve a specific task by ID.
        
        Args:
            task_id: The task ID to retrieve
            
        Returns:
            MarketTask instance
            
        Raises:
            MarketAPIError: If the API request fails or task is not found
        """
        response = self._make_request("GET", f"/v1/tasks/{task_id}")
        
        return MarketTask(
            id=response["id"],
            title=response["title"],
            description=response["description"],
            status=response["status"],
            tags=response.get("tags", []),
            metadata=response.get("metadata", {}),
            created_at=datetime.fromisoformat(response["created_at"].replace("Z", "+00:00")),
            worker_id=response.get("worker_id"),
            result=response.get("result"),
        )
    
    def list_tasks(self, status: Optional[str] = None) -> list[MarketTask]:
        """
        List tasks, optionally filtered by status.
        
        Args:
            status: Optional status filter (e.g., "pending", "completed", "failed")
            
        Returns:
            List of MarketTask instances
            
        Raises:
            MarketAPIError: If the API request fails
        """
        endpoint = "/v1/tasks"
        if status:
            endpoint += f"?status={urllib.parse.quote(status)}"
        
        response = self._make_request("GET", endpoint)
        tasks_data = response.get("tasks", [])
        
        tasks = []
        for task_data in tasks_data:
            tasks.append(MarketTask(
                id=task_data["id"],
                title=task_data["title"],
                description=task_data["description"],
                status=task_data["status"],
                tags=task_data.get("tags", []),
                metadata=task_data.get("metadata", {}),
                created_at=datetime.fromisoformat(task_data["created_at"].replace("Z", "+00:00")),
                worker_id=task_data.get("worker_id"),
                result=task_data.get("result"),
            ))
        
        return tasks
    
    def cancel_task(self, task_id: str) -> bool:
        """
        Cancel a task.
        
        Args:
            task_id: The task ID to cancel
            
        Returns:
            True if cancellation was successful
            
        Raises:
            MarketAPIError: If the API request fails
        """
        response = self._make_request("DELETE", f"/v1/tasks/{task_id}")
        return response.get("success", False)