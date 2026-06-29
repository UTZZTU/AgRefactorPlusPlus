#!/usr/bin/env python3
"""
MCP Server for Remote HLS (High-Level Synthesis) Synthesis

This MCP server provides a tool for running Xilinx Vitis HLS synthesis on a remote server.
It allows you to:
- Specify a local folder containing HLS source files (must be flat, no sub-folders)
- Set the top function name for synthesis
- Configure clock frequency (e.g., "200MHz", "100MHz")
- Choose flow target: "vitis" or "vivado"

The server copies source files to a remote HLS server, generates the appropriate TCL script,
runs synthesis, and returns the synthesis report or error messages.

Requirements:
- Remote HLS server running (hls_server.py)
- SSH tunnel configured (hls_tunnel.py) OR direct network access
- HLS_SERVER_URL environment variable set (e.g., http://127.0.0.1:8884)

Usage with an MCP client:
Add to your MCP client config (e.g. .mcp.json):
{
    "mcpServers": {
        "hls-synthesis": {
            "command": "python",
            "args": ["/path/to/scripts/hls_mcp_server.py"],
            "env": {
                "HLS_SERVER_URL": "http://127.0.0.1:8884"
            }
        }
    }
}
"""

import os
import sys
import json
import asyncio
import aiohttp
import base64
from pathlib import Path
from typing import Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Tool,
    TextContent,
    CallToolResult,
)

# Configuration
HLS_SERVER_URL = os.getenv("HLS_SERVER_URL", "http://127.0.0.1:8884")
CSYNTH_TIMEOUT = 300  # 5 minutes default timeout
CSIM_TIMEOUT = 60  # 1 minute default timeout for simulation
DEFAULT_CLOCK = "200MHz"
DEFAULT_PART = "xcu200-fsgd2104-2-e"

# Valid file extensions for HLS source files
VALID_EXTENSIONS = {".cpp", ".c", ".h", ".hpp", ".tcl"}
# Additional extensions for csim (binary data files)
CSIM_VALID_EXTENSIONS = VALID_EXTENSIONS | {".bin", ".dat", ".txt"}
# Binary file extensions (need base64 encoding)
BINARY_EXTENSIONS = {".bin", ".dat"}

server = Server("hls-synthesis")


def make_vitis_tcl(
    top_kernel: str,
    file_list: list[str],
    clock_period: str = DEFAULT_CLOCK,
    flow_target: str = "vitis",
    part: str = DEFAULT_PART,
) -> str:
    """Generate Vitis HLS TCL script for synthesis."""
    tcl_lines = [
        "open_project csynth",
        f"set_top {top_kernel}",
    ]
    for fname in file_list:
        tcl_lines.append(f'add_files "{fname}" -cflags " -D XILINX "')
    tcl_lines += [
        f"open_solution -flow_target {flow_target} solution",
        f"set_part {part}",
        f"create_clock -period {clock_period} -name default",
        "csynth_design",
        "close_project",
        "exit",
    ]
    return "\n".join(tcl_lines)


def validate_source_folder(folder_path: str) -> tuple[bool, str, list[str]]:
    """
    Validate the source folder:
    - Must exist and be a directory
    - Must not contain sub-folders
    - Must contain at least one source file

    Returns: (is_valid, error_message, list_of_files)
    """
    path = Path(folder_path)

    if not path.exists():
        return False, f"Folder does not exist: {folder_path}", []

    if not path.is_dir():
        return False, f"Path is not a directory: {folder_path}", []

    files = []
    for item in path.iterdir():
        if item.is_dir():
            return False, f"Source folder must not contain sub-folders. Found: {item.name}", []
        if item.is_file() and item.suffix.lower() in VALID_EXTENSIONS:
            files.append(item.name)

    if not files:
        return False, f"No valid source files found in {folder_path}. Valid extensions: {VALID_EXTENSIONS}", []

    return True, "", files


def validate_csim_source_folder(folder_path: str) -> tuple[bool, str, list[str]]:
    """
    Validate the source folder for csim (allows nested directories):
    - Must exist and be a directory
    - Can contain sub-folders (for data files)
    - Must contain at least one source file

    Returns: (is_valid, error_message, list_of_relative_paths)
    """
    path = Path(folder_path)

    if not path.exists():
        return False, f"Folder does not exist: {folder_path}", []

    if not path.is_dir():
        return False, f"Path is not a directory: {folder_path}", []

    files = []
    for item in path.rglob("*"):
        if item.is_file() and item.suffix.lower() in CSIM_VALID_EXTENSIONS:
            # Use relative path from folder_path
            rel_path = item.relative_to(path)
            files.append(str(rel_path))

    if not files:
        return False, f"No valid source files found in {folder_path}. Valid extensions: {CSIM_VALID_EXTENSIONS}", []

    return True, "", files


def read_source_files(folder_path: str, file_list: list[str]) -> dict[str, str]:
    """Read all source files from the folder into a dictionary."""
    path = Path(folder_path)
    files_content = {}

    for fname in file_list:
        file_path = path / fname
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            files_content[fname] = f.read()

    return files_content


def read_csim_source_files(folder_path: str, file_list: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """
    Read all source files for csim, handling binary files separately.

    Returns: (text_files, binary_files)
    - text_files: dict of filename -> content (text)
    - binary_files: dict of filename -> content (base64 encoded)
    """
    path = Path(folder_path)
    text_files = {}
    binary_files = {}

    for fname in file_list:
        file_path = path / fname
        suffix = file_path.suffix.lower()

        if suffix in BINARY_EXTENSIONS:
            # Read as binary and base64 encode
            with open(file_path, "rb") as f:
                binary_files[fname] = base64.b64encode(f.read()).decode("ascii")
        else:
            # Read as text
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text_files[fname] = f.read()

    return text_files, binary_files


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="run_hls_synthesis",
            description="""Run Xilinx Vitis HLS synthesis on source files.

This tool performs High-Level Synthesis (HLS) on C/C++ source files to generate RTL code.
It sends the source files to a remote HLS server, runs synthesis, and returns the report.

Parameters:
- source_folder: Path to folder containing all source files (must be flat, no sub-folders)
- top_function: Name of the top-level function to synthesize
- clock_frequency: Target clock frequency (e.g., "200MHz", "100MHz", "10ns"). Default: "200MHz"
- flow_target: "vitis" for Vitis flow or "vivado" for Vivado flow. Default: "vitis"
- timeout: Synthesis timeout in seconds. Default: 300

Returns:
- On success: Synthesis report with timing/resource estimates
- On failure: Error message from synthesis log""",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_folder": {
                        "type": "string",
                        "description": "Absolute path to the folder containing HLS source files. Must be flat (no sub-folders).",
                    },
                    "top_function": {
                        "type": "string",
                        "description": "Name of the top-level function to synthesize.",
                    },
                    "clock_frequency": {
                        "type": "string",
                        "description": "Target clock frequency (e.g., '200MHz', '100MHz', '10ns'). Default: '200MHz'.",
                        "default": "200MHz",
                    },
                    "flow_target": {
                        "type": "string",
                        "enum": ["vitis", "vivado"],
                        "description": "HLS flow target: 'vitis' or 'vivado'. Default: 'vitis'.",
                        "default": "vitis",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Synthesis timeout in seconds. Default: 300.",
                        "default": 300,
                    },
                },
                "required": ["source_folder", "top_function"],
            },
        ),
        Tool(
            name="check_hls_server_status",
            description="""Check the status of the remote HLS server.

Returns information about the server availability and number of available workers.""",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="run_csim",
            description="""Run C/C++ simulation to verify functional correctness of HLS code.

This tool compiles and runs a C/C++ simulation using a custom g++ compile command.
It sends the source files to a remote server, compiles them, runs the executable,
and returns the output.

Parameters:
- source_folder: Path to folder containing source files. Can include subdirectories (e.g., data/ for .bin files).
- compile_command: g++ compilation command with relative file paths. Must produce an executable named 'csim'.
  Example: "g++ -I$XILINX_HLS/include -O2 testbench.cpp kernel.cpp -o csim"
- timeout: Simulation timeout in seconds. Default: 60

Supported file types: .cpp, .c, .h, .hpp, .tcl, .bin, .dat, .txt
Binary files (.bin, .dat) are automatically base64 encoded for transfer.

Returns:
- On success: stdout output from the simulation
- On failure: Error message (compilation error or runtime error)""",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_folder": {
                        "type": "string",
                        "description": "Absolute path to the folder containing source files. Can include subdirectories (e.g., data/ for .bin files).",
                    },
                    "compile_command": {
                        "type": "string",
                        "description": "g++ compile command with relative file paths. Must output to '-o csim'. Example: 'g++ -O2 testbench.cpp kernel.cpp -o csim'",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Simulation timeout in seconds. Default: 60.",
                        "default": 60,
                    },
                },
                "required": ["source_folder", "compile_command"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    """Handle tool calls."""

    if name == "check_hls_server_status":
        return await check_server_status()

    elif name == "run_hls_synthesis":
        return await run_synthesis(
            source_folder=arguments["source_folder"],
            top_function=arguments["top_function"],
            clock_frequency=arguments.get("clock_frequency", DEFAULT_CLOCK),
            flow_target=arguments.get("flow_target", "vitis"),
            timeout=arguments.get("timeout", CSYNTH_TIMEOUT),
        )

    elif name == "run_csim":
        return await run_csim(
            source_folder=arguments["source_folder"],
            compile_command=arguments["compile_command"],
            timeout=arguments.get("timeout", CSIM_TIMEOUT),
        )

    else:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Unknown tool: {name}")],
            isError=True,
        )


async def check_server_status() -> CallToolResult:
    """Check the HLS server status."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{HLS_SERVER_URL}/health", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return CallToolResult(
                        content=[TextContent(
                            type="text",
                            text=f"HLS Server Status: OK\n"
                                 f"Available workers: {data.get('available_workers', 'N/A')}/{data.get('max_workers', 'N/A')}\n"
                                 f"Server URL: {HLS_SERVER_URL}"
                        )]
                    )
                else:
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"HLS Server returned status {resp.status}")],
                        isError=True,
                    )
    except asyncio.TimeoutError:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Connection to HLS server timed out. URL: {HLS_SERVER_URL}")],
            isError=True,
        )
    except aiohttp.ClientError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Failed to connect to HLS server: {e}\nURL: {HLS_SERVER_URL}")],
            isError=True,
        )


async def run_synthesis(
    source_folder: str,
    top_function: str,
    clock_frequency: str,
    flow_target: str,
    timeout: int,
) -> CallToolResult:
    """Run HLS synthesis on the provided source files."""

    # Validate source folder
    is_valid, error_msg, file_list = validate_source_folder(source_folder)
    if not is_valid:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Validation error: {error_msg}")],
            isError=True,
        )

    # Read source files
    try:
        files_content = read_source_files(source_folder, file_list)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error reading source files: {e}")],
            isError=True,
        )

    # Generate TCL script
    tcl_content = make_vitis_tcl(
        top_kernel=top_function,
        file_list=file_list,
        clock_period=clock_frequency,
        flow_target=flow_target,
    )

    # Prepare payload for remote server
    payload = {
        "files": files_content,
        "tcl_script": tcl_content,
        "top_function": top_function,
        "timelimit": timeout,
    }

    # Send request to remote server
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HLS_SERVER_URL}/csynth_folder",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout + 60),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Server error ({resp.status}): {error_text}")],
                        isError=True,
                    )

                data = await resp.json()
                status = data.get("status", "unknown")
                error_msg = data.get("error_msg", "")
                report = data.get("report", "")

                if status == "succeeded":
                    result_text = f"HLS Synthesis SUCCEEDED\n\n"
                    result_text += f"Top Function: {top_function}\n"
                    result_text += f"Clock: {clock_frequency}\n"
                    result_text += f"Flow Target: {flow_target}\n"
                    result_text += f"Source Files: {', '.join(file_list)}\n\n"
                    if report:
                        result_text += f"=== Synthesis Report ===\n{report}"
                    return CallToolResult(content=[TextContent(type="text", text=result_text)])

                elif status == "timeout":
                    result_text = f"HLS Synthesis TIMEOUT\n\n"
                    result_text += f"Synthesis exceeded {timeout} seconds.\n"
                    if error_msg:
                        result_text += f"\nLast log output:\n{error_msg}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=result_text)],
                        isError=True,
                    )

                else:  # csynth_failed or other errors
                    result_text = f"HLS Synthesis FAILED\n\n"
                    result_text += f"Status: {status}\n"
                    if error_msg:
                        result_text += f"\nError log:\n{error_msg}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=result_text)],
                        isError=True,
                    )

    except asyncio.TimeoutError:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Request timed out after {timeout + 60} seconds")],
            isError=True,
        )
    except aiohttp.ClientError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Connection error: {e}\nHLS Server URL: {HLS_SERVER_URL}")],
            isError=True,
        )


async def run_csim(
    source_folder: str,
    compile_command: str,
    timeout: int,
) -> CallToolResult:
    """Run C/C++ simulation with a custom g++ compile command."""

    # Validate source folder (allow nested directories for csim)
    is_valid, error_msg, file_list = validate_csim_source_folder(source_folder)
    if not is_valid:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Validation error: {error_msg}")],
            isError=True,
        )

    # Read source files (separate text and binary)
    try:
        text_files, binary_files = read_csim_source_files(source_folder, file_list)
    except Exception as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Error reading source files: {e}")],
            isError=True,
        )

    # Prepare payload for remote server
    payload = {
        "files": text_files,
        "binary_files": binary_files,  # base64 encoded
        "compile_command": compile_command,
        "timelimit": timeout,
    }

    # Send request to remote server
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{HLS_SERVER_URL}/csim_folder",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout + 60),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    return CallToolResult(
                        content=[TextContent(type="text", text=f"Server error ({resp.status}): {error_text}")],
                        isError=True,
                    )

                data = await resp.json()
                status = data.get("status", "unknown")
                error_msg = data.get("error_msg", "")
                stdout = data.get("stdout", "")

                if status == "succeeded":
                    result_text = f"C Simulation SUCCEEDED\n\n"
                    result_text += f"Text Files: {', '.join(text_files.keys())}\n"
                    if binary_files:
                        result_text += f"Binary Files: {', '.join(binary_files.keys())}\n"
                    result_text += f"Compile Command: {compile_command}\n\n"
                    if stdout:
                        result_text += f"=== Simulation Output ===\n{stdout}"
                    return CallToolResult(content=[TextContent(type="text", text=result_text)])

                elif status == "compile_timeout":
                    result_text = f"C Simulation FAILED - Compilation Timeout\n\n"
                    result_text += f"Compilation exceeded {timeout} seconds.\n"
                    if error_msg:
                        result_text += f"\nError:\n{error_msg}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=result_text)],
                        isError=True,
                    )

                elif status == "compile_failed":
                    result_text = f"C Simulation FAILED - Compilation Error\n\n"
                    result_text += f"Compile Command: {compile_command}\n\n"
                    if error_msg:
                        result_text += f"Error:\n{error_msg}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=result_text)],
                        isError=True,
                    )

                elif status == "csim_timeout":
                    result_text = f"C Simulation FAILED - Execution Timeout\n\n"
                    result_text += f"Simulation exceeded {timeout} seconds.\n"
                    if stdout:
                        result_text += f"\nPartial output:\n{stdout}"
                    if error_msg:
                        result_text += f"\nError:\n{error_msg}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=result_text)],
                        isError=True,
                    )

                else:  # csim_failed or other errors
                    result_text = f"C Simulation FAILED\n\n"
                    result_text += f"Status: {status}\n"
                    if stdout:
                        result_text += f"\nOutput:\n{stdout}"
                    if error_msg:
                        result_text += f"\nError:\n{error_msg}"
                    return CallToolResult(
                        content=[TextContent(type="text", text=result_text)],
                        isError=True,
                    )

    except asyncio.TimeoutError:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Request timed out after {timeout + 60} seconds")],
            isError=True,
        )
    except aiohttp.ClientError as e:
        return CallToolResult(
            content=[TextContent(type="text", text=f"Connection error: {e}\nHLS Server URL: {HLS_SERVER_URL}")],
            isError=True,
        )


async def main():
    """Main entry point for the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
