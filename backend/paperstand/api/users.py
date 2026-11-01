"""``/api/users``: the accounts, for an administrator.

Included by :func:`paperstand.main.create_app` behind
:attr:`paperstand.api.auth.Authenticator.admin`, so a signed-in reader gets a
``403`` from every route here. The rules — no taken name, never no
administrator, nobody deleting themselves — are
:mod:`paperstand.accounts`'s, enforced there in single guarded statements;
this module only turns their refusals into status codes.

Every ``409`` here is a plain ``{"detail": ...}``. A client tells the
running-scan ``409`` of ``POST /api/scan`` apart by its ``scan_id`` key, so
none of these may ever carry one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from paperstand import accounts
from paperstand.api.auth import Authenticator, Caller, user_model
from paperstand.api.common import catalogue
from paperstand.db import Database
from paperstand.schemas import User, UserCreate, UserUpdate

UNKNOWN_USER = "unknown user"


def create_router(authenticator: Authenticator, database: Database | None) -> APIRouter:
    """Build the users router."""
    router = APIRouter(prefix="/api/users", tags=["users"])

    @router.get("")
    def list_users() -> list[User]:
        """Every account, by name without regard to case."""
        return [user_model(account) for account in accounts.list_users(catalogue(database))]

    @router.post("", status_code=201, responses={409: {"description": "The name is taken"}})
    def create_user(body: UserCreate) -> User:
        """Create an account; a ``409`` when the name is taken, in any case."""
        try:
            account = accounts.create_user(
                catalogue(database), body.username, body.password, is_admin=body.is_admin
            )
        except accounts.UsernameTaken as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return user_model(account)

    # The caller comes from a default rather than an `Annotated` hint for the
    # reason given in `Authenticator.__init__`; it is the same dependency as
    # the router's, so FastAPI resolves it once.
    @router.patch(
        "/{user_id}",
        responses={404: {"description": UNKNOWN_USER}, 409: {"description": "No admin left"}},
    )
    def update_user(
        user_id: int, body: UserUpdate, caller: Caller = Depends(authenticator.admin)
    ) -> User:
        """Promote, demote, or set a new password — which signs that account out everywhere.

        An administrator resetting their own password this way keeps the token
        they made the request with, as ``PUT /api/auth/password`` would.
        """
        connection = catalogue(database)
        try:
            if body.is_admin is not None:
                accounts.set_admin(connection, user_id, body.is_admin)
            if body.password is not None:
                keep = caller.token_id if user_id == caller.account.id else None
                accounts.set_password(connection, user_id, body.password, keep_token_id=keep)
        except accounts.UnknownUser as error:
            raise HTTPException(status_code=404, detail=UNKNOWN_USER) from error
        except accounts.LastAdmin as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        account = accounts.get_user(connection, user_id)
        if account is None:
            raise HTTPException(status_code=404, detail=UNKNOWN_USER)
        return user_model(account)

    @router.delete(
        "/{user_id}",
        status_code=204,
        response_class=Response,
        responses={404: {"description": UNKNOWN_USER}, 409: {"description": "Not allowed"}},
    )
    def delete_user(user_id: int, caller: Caller = Depends(authenticator.admin)) -> Response:
        """Delete an account and its tokens; never your own, never the last administrator."""
        try:
            accounts.delete_user(catalogue(database), user_id, acting_user_id=caller.account.id)
        except accounts.UnknownUser as error:
            raise HTTPException(status_code=404, detail=UNKNOWN_USER) from error
        except (accounts.SelfDelete, accounts.LastAdmin) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return Response(status_code=204)

    return router
