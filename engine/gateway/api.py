"""HTTP API for health-engine tools.

Exposes all tools from TOOL_REGISTRY as GET/POST endpoints at /api/{tool_name}.
Auth via ?token=<secret> query parameter (no headers needed — compatible with
OpenClaw's GET-only web_fetch).

JSON dict/list params are passed as URL-encoded JSON strings and auto-parsed.
"""

import inspect
import json
import logging

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse

from mcp_server.tools import TOOL_REGISTRY

logger = logging.getLogger("health-engine.api")

# Params that accept complex types (dicts/lists) — auto-parse from JSON strings
_COMPLEX_PARAMS = {"habits", "results", "supplements", "goals", "conditions"}


def _coerce_params(tool_name: str, params: dict) -> dict:
    """Coerce query string values to match tool function signatures."""
    func = TOOL_REGISTRY.get(tool_name)
    if not func:
        return params

    sig = inspect.signature(func)
    coerced = {}
    for key, value in params.items():
        if key not in sig.parameters:
            continue
        param = sig.parameters[key]
        annotation = param.annotation

        # Parse JSON strings for complex types
        if key in _COMPLEX_PARAMS or (isinstance(value, str) and value.startswith(("{", "["))):
            try:
                coerced[key] = json.loads(value)
                continue
            except (json.JSONDecodeError, TypeError):
                pass

        # Coerce numeric types
        origin = getattr(annotation, "__origin__", None)
        base = annotation if origin is None else None

        if base is int:
            try:
                coerced[key] = int(value)
                continue
            except (ValueError, TypeError):
                pass
        elif base is float:
            try:
                coerced[key] = float(value)
                continue
            except (ValueError, TypeError):
                pass
        elif base is bool:
            coerced[key] = value.lower() in ("true", "1", "yes")
            continue

        # Check union types (e.g. float | None)
        if origin is not None:
            args = getattr(annotation, "__args__", ())
            for arg in args:
                if arg is type(None):
                    continue
                if arg is int:
                    try:
                        coerced[key] = int(value)
                        break
                    except (ValueError, TypeError):
                        pass
                elif arg is float:
                    try:
                        coerced[key] = float(value)
                        break
                    except (ValueError, TypeError):
                        pass
                elif arg is bool:
                    coerced[key] = value.lower() in ("true", "1", "yes")
                    break
            else:
                coerced[key] = value
        else:
            coerced[key] = value

    return coerced


async def api_handler(tool_name: str, request: Request, token: str = Query(...)):
    """Generic handler for /api/{tool_name}."""
    config = request.app.state.config

    if not config.api_token:
        raise HTTPException(500, "API token not configured on server")
    if token != config.api_token:
        raise HTTPException(403, "Invalid token")
    if tool_name not in TOOL_REGISTRY:
        raise HTTPException(404, f"Unknown tool: {tool_name}")

    # Collect params from query string (GET) or body (POST)
    params = dict(request.query_params)
    params.pop("token", None)

    if request.method == "POST":
        try:
            body = await request.json()
            if isinstance(body, dict):
                params.update(body)
        except Exception:
            pass

    # Coerce types to match function signatures
    params = _coerce_params(tool_name, params)

    try:
        result = TOOL_REGISTRY[tool_name](**params)
        # Serialize with default=str to handle datetime, Path, etc.
        serialized = json.loads(json.dumps(result, default=str))
        return JSONResponse(content=serialized)
    except TypeError as e:
        raise HTTPException(400, f"Parameter error: {e}")
    except Exception as e:
        logger.exception(f"Tool {tool_name} failed")
        raise HTTPException(500, f"Tool error: {e}")


async def api_list_tools(request: Request, token: str = Query(...)):
    """List all available tool names."""
    config = request.app.state.config
    if not config.api_token:
        raise HTTPException(500, "API token not configured on server")
    if token != config.api_token:
        raise HTTPException(403, "Invalid token")

    tools = []
    for name, func in TOOL_REGISTRY.items():
        sig = inspect.signature(func)
        params = [
            {"name": p.name, "default": None if p.default is inspect.Parameter.empty else repr(p.default)}
            for p in sig.parameters.values()
        ]
        tools.append({"name": name, "params": params, "doc": (func.__doc__ or "").split("\n")[0]})
    return JSONResponse(content={"tools": tools})
