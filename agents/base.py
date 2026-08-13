"""Base agent class for all pipeline agents."""
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from utils import get_logger


class BaseAgent(ABC):
    """
    Abstract base class for all pipeline agents.
    
    Each agent should:
    1. Inherit from this class
    2. Implement the `run()` method
    3. Handle its own error handling and retries
    4. Log progress appropriately
    """
    
    def __init__(self, name: str):
        """
        Initialize the agent.
        
        Args:
            name: Agent name for logging
        """
        self.name = name
        self.logger = get_logger(f"agent.{name}")
    
    @abstractmethod
    async def run(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the agent's main task.
        
        Args:
            context: Input data and state from previous agents
        
        Returns:
            Dictionary containing the agent's output
        
        Raises:
            Exception: If the agent fails to complete its task
        """
        pass
    
    def log_start(self):
        """Log agent start."""
        self.logger.info(f"🚀 Starting {self.name} agent")
    
    def log_complete(self, result_summary: str = ""):
        """Log agent completion."""
        msg = f"✅ {self.name} agent completed"
        if result_summary:
            msg += f": {result_summary}"
        self.logger.info(msg)
    
    def log_error(self, error: Exception):
        """Log agent error."""
        self.logger.error(f"❌ {self.name} agent failed: {str(error)}", exc_info=True)
    
    def validate_context(self, context: Dict[str, Any], required_keys: list) -> None:
        """
        Validate that required keys are present in context.
        
        Args:
            context: Context dictionary to validate
            required_keys: List of required key names
        
        Raises:
            ValueError: If any required key is missing
        """
        missing = [key for key in required_keys if key not in context]
        if missing:
            raise ValueError(
                f"{self.name} agent missing required context keys: {missing}"
            )
