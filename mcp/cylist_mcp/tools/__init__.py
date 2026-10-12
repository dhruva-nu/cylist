"""The tools, one module per thing on the board they act on.

Each module has a ``register`` function that adds its tools to a server and
binds them to one API client; :func:`cylist_mcp.server.build_server` calls them
in the order the tools are listed to a client.
"""
