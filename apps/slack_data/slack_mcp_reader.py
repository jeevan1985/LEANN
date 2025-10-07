#!/usr/bin/env python3
"""
Slack MCP Reader for LEANN

This module provides functionality to connect to Slack MCP servers and fetch message data
for indexing in LEANN. It supports various Slack MCP server implementations and provides
flexible message processing options.
"""

import asyncio
import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class SlackMCPReader:
    """
    Reader for Slack data via MCP (Model Context Protocol) servers.

    This class connects to Slack MCP servers to fetch message data and convert it
    into a format suitable for LEANN indexing.
    """

    def __init__(
        self,
        mcp_server_command: str,
        workspace_name: Optional[str] = None,
        concatenate_conversations: bool = True,
        max_messages_per_conversation: int = 100,
    ):
        """
        Initialize the Slack MCP Reader.

        Args:
            mcp_server_command: Command to start the MCP server (e.g., 'slack-mcp-server')
            workspace_name: Optional workspace name to filter messages
            concatenate_conversations: Whether to group messages by channel/thread
            max_messages_per_conversation: Maximum messages to include per conversation
        """
        self.mcp_server_command = mcp_server_command
        self.workspace_name = workspace_name
        self.concatenate_conversations = concatenate_conversations
        self.max_messages_per_conversation = max_messages_per_conversation
        self.mcp_process = None

    async def start_mcp_server(self):
        """Start the MCP server process."""
        try:
            self.mcp_process = await asyncio.create_subprocess_exec(
                *self.mcp_server_command.split(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"Started MCP server: {self.mcp_server_command}")
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            raise

    async def stop_mcp_server(self):
        """Stop the MCP server process."""
        if self.mcp_process:
            self.mcp_process.terminate()
            await self.mcp_process.wait()
            logger.info("Stopped MCP server")

    async def send_mcp_request(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send a request to the MCP server and get response."""
        if not self.mcp_process:
            raise RuntimeError("MCP server not started")

        request_json = json.dumps(request) + "\n"
        self.mcp_process.stdin.write(request_json.encode())
        await self.mcp_process.stdin.drain()

        response_line = await self.mcp_process.stdout.readline()
        if not response_line:
            raise RuntimeError("No response from MCP server")

        return json.loads(response_line.decode().strip())

    async def initialize_mcp_connection(self):
        """Initialize the MCP connection."""
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "leann-slack-reader", "version": "1.0.0"},
            },
        }

        response = await self.send_mcp_request(init_request)
        if "error" in response:
            raise RuntimeError(f"MCP initialization failed: {response['error']}")

        logger.info("MCP connection initialized successfully")

    async def list_available_tools(self) -> list[dict[str, Any]]:
        """List available tools from the MCP server."""
        list_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}

        response = await self.send_mcp_request(list_request)
        if "error" in response:
            raise RuntimeError(f"Failed to list tools: {response['error']}")

        return response.get("result", {}).get("tools", [])

    async def fetch_slack_messages(
        self, channel: Optional[str] = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """
        Fetch Slack messages using MCP tools.

        Args:
            channel: Optional channel name to filter messages
            limit: Maximum number of messages to fetch

        Returns:
            List of message dictionaries
        """
        # This is a generic implementation - specific MCP servers may have different tool names
        # Common tool names might be: 'get_messages', 'list_messages', 'fetch_channel_history'

        tools = await self.list_available_tools()
        message_tool = None

        # Look for a tool that can fetch messages
        for tool in tools:
            tool_name = tool.get("name", "").lower()
            if any(
                keyword in tool_name
                for keyword in ["message", "history", "channel", "conversation"]
            ):
                message_tool = tool
                break

        if not message_tool:
            raise RuntimeError("No message fetching tool found in MCP server")

        # Prepare tool call parameters
        tool_params = {"limit": limit}
        if channel:
            # Try common parameter names for channel specification
            for param_name in ["channel", "channel_id", "channel_name"]:
                tool_params[param_name] = channel
                break

        fetch_request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": message_tool["name"], "arguments": tool_params},
        }

        response = await self.send_mcp_request(fetch_request)
        if "error" in response:
            raise RuntimeError(f"Failed to fetch messages: {response['error']}")

        # Extract messages from response - format may vary by MCP server
        result = response.get("result", {})
        if "content" in result and isinstance(result["content"], list):
            # Some MCP servers return content as a list
            content = result["content"][0] if result["content"] else {}
            if "text" in content:
                try:
                    messages = json.loads(content["text"])
                except json.JSONDecodeError:
                    # If not JSON, treat as plain text
                    messages = [{"text": content["text"], "channel": channel or "unknown"}]
            else:
                messages = result["content"]
        else:
            # Direct message format
            messages = result.get("messages", [result])

        return messages if isinstance(messages, list) else [messages]

    def _format_message(self, message: dict[str, Any]) -> str:
        """Format a single message for indexing."""
        text = message.get("text", "")
        user = message.get("user", message.get("username", "Unknown"))
        channel = message.get("channel", message.get("channel_name", "Unknown"))
        timestamp = message.get("ts", message.get("timestamp", ""))

        # Format timestamp if available
        formatted_time = ""
        if timestamp:
            try:
                import datetime

                if isinstance(timestamp, str) and "." in timestamp:
                    dt = datetime.datetime.fromtimestamp(float(timestamp))
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(timestamp, (int, float)):
                    dt = datetime.datetime.fromtimestamp(timestamp)
                    formatted_time = dt.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    formatted_time = str(timestamp)
            except (ValueError, TypeError):
                formatted_time = str(timestamp)

        # Build formatted message
        parts = []
        if channel:
            parts.append(f"Channel: #{channel}")
        if user:
            parts.append(f"User: {user}")
        if formatted_time:
            parts.append(f"Time: {formatted_time}")
        if text:
            parts.append(f"Message: {text}")

        return "\n".join(parts)

    def _create_concatenated_content(self, messages: list[dict[str, Any]], channel: str) -> str:
        """Create concatenated content from multiple messages in a channel."""
        if not messages:
            return ""

        # Sort messages by timestamp if available
        try:
            messages.sort(key=lambda x: float(x.get("ts", x.get("timestamp", 0))))
        except (ValueError, TypeError):
            pass  # Keep original order if timestamps aren't numeric

        # Limit messages per conversation
        if len(messages) > self.max_messages_per_conversation:
            messages = messages[-self.max_messages_per_conversation :]

        # Create header
        content_parts = [
            f"Slack Channel: #{channel}",
            f"Message Count: {len(messages)}",
            f"Workspace: {self.workspace_name or 'Unknown'}",
            "=" * 50,
            "",
        ]

        # Add messages
        for message in messages:
            formatted_msg = self._format_message(message)
            if formatted_msg.strip():
                content_parts.append(formatted_msg)
                content_parts.append("-" * 30)
                content_parts.append("")

        return "\n".join(content_parts)

    async def read_slack_data(self, channels: Optional[list[str]] = None) -> list[str]:
        """
        Read Slack data and return formatted text chunks.

        Args:
            channels: Optional list of channel names to fetch. If None, fetches from all available channels.

        Returns:
            List of formatted text chunks ready for LEANN indexing
        """
        try:
            await self.start_mcp_server()
            await self.initialize_mcp_connection()

            all_texts = []

            if channels:
                # Fetch specific channels
                for channel in channels:
                    try:
                        messages = await self.fetch_slack_messages(channel=channel, limit=1000)
                        if messages:
                            if self.concatenate_conversations:
                                text_content = self._create_concatenated_content(messages, channel)
                                if text_content.strip():
                                    all_texts.append(text_content)
                            else:
                                # Process individual messages
                                for message in messages:
                                    formatted_msg = self._format_message(message)
                                    if formatted_msg.strip():
                                        all_texts.append(formatted_msg)
                    except Exception as e:
                        logger.warning(f"Failed to fetch messages from channel {channel}: {e}")
                        continue
            else:
                # Fetch from all available channels/conversations
                # This is a simplified approach - real implementation would need to
                # discover available channels first
                try:
                    messages = await self.fetch_slack_messages(limit=1000)
                    if messages:
                        # Group messages by channel if concatenating
                        if self.concatenate_conversations:
                            channel_messages = {}
                            for message in messages:
                                channel = message.get(
                                    "channel", message.get("channel_name", "general")
                                )
                                if channel not in channel_messages:
                                    channel_messages[channel] = []
                                channel_messages[channel].append(message)

                            # Create concatenated content for each channel
                            for channel, msgs in channel_messages.items():
                                text_content = self._create_concatenated_content(msgs, channel)
                                if text_content.strip():
                                    all_texts.append(text_content)
                        else:
                            # Process individual messages
                            for message in messages:
                                formatted_msg = self._format_message(message)
                                if formatted_msg.strip():
                                    all_texts.append(formatted_msg)
                except Exception as e:
                    logger.error(f"Failed to fetch messages: {e}")

            return all_texts

        finally:
            await self.stop_mcp_server()

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start_mcp_server()
        await self.initialize_mcp_connection()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop_mcp_server()
