"""Python SDK for the Jev System One API.

    from jev import Jev, Choice, Score, Noul, Criteria

    class Department(Choice):
        instructions = "Which department should handle this request?"
        billing   = Criteria("invoices, payments, refunds")
        technical = Criteria("bugs, outages, system errors")

    with Jev() as jev:                                # $JEV_API_KEY
        result = jev.ask({"subject": "Charged twice"}, Department)
        result[Department].choice                     # "billing"
"""
from ._answers import Answer, ChoiceAnswer, Model, NoulAnswer, Result, ScoreAnswer, Usage
from ._client import DEFAULT_BASE_URL, DEFAULT_MODEL, AsyncJev, Jev
from ._errors import (
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    JevAPIError,
    JevConnectionError,
    JevError,
    JevResponseError,
    JevTimeoutError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)
from ._questions import Choice, Criteria, Noul, Question, Score, choice, noul, score
from ._version import __version__

__all__ = [
    "Jev", "AsyncJev", "DEFAULT_BASE_URL", "DEFAULT_MODEL",
    "Question", "Choice", "Score", "Noul", "Criteria", "choice", "score", "noul",
    "Answer", "ChoiceAnswer", "ScoreAnswer", "NoulAnswer", "Result", "Usage", "Model",
    "JevError", "JevConnectionError", "JevTimeoutError", "JevResponseError", "JevAPIError",
    "BadRequestError", "AuthenticationError", "PermissionDeniedError", "NotFoundError",
    "UnprocessableEntityError", "RateLimitError", "InternalServerError",
    "__version__",
]
