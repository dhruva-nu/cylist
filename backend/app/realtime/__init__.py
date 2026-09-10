"""Live connections: who is watching a board, and which agent is on a socket.

Everything here is about connections that outlive a request. The rest of the
application is request-shaped — one session, one transaction, one response —
and the rules that keep it honest do not survive being held open for an hour,
so they are restated here rather than reached for:

* A handler holds a database session for a query and never across an ``await``
  on its socket. The pool is fifteen connections; a socket is not one of them.
* Authentication happens once, at the handshake, through the same
  :func:`app.auth.dependencies.resolve_principal` every request uses.
* Nothing pushed carries board data. See :mod:`app.realtime.hub`.
"""
