"""Push notifications to phones when an agent session changes (CYLIST-107).

* :mod:`~app.services.push.triggers` notices a change, on the ORM mapper, and
  queues it on the transaction's outbox.
* :mod:`~app.services.push.delivery` sends it once the transaction commits,
  to the devices :mod:`app.services.devices` knows.
* :mod:`~app.services.push.messages` is what a push says.
* :mod:`~app.services.push.fcm` is Firebase Cloud Messaging, or nothing at
  all when no service account is configured.
"""
