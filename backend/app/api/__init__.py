"""What the HTTP layer shares: the dependencies routers declare and how responses are drawn.

Routers import from here and never from one another; services import from neither.
"""
