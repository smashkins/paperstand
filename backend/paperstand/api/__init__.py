"""The HTTP API.

One module per group of routes, each exposing a ``create_router`` factory that
is handed the pieces it needs — the settings, the database, the scheduler —
rather than reaching for a global. That is what lets a test build an application
around a throwaway library and a throwaway database.
"""
