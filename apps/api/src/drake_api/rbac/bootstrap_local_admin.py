"""Create the first local administrator.

    uv run python -m drake_api.rbac.bootstrap_local_admin --email admin@example.com

The password is never an argument. It is read from a hidden prompt, or
from stdin when the command is not attached to a terminal, so it does not
reach `ps`, shell history, a CI log, a Kubernetes manifest or this
repository.

The account is an ordinary Drake identity with an ordinary grant: the
Platform Owner role at the organization root, through the same tables every
other grant uses. There is no special case anywhere in the codebase for a
particular email address.

Re-running is safe and deliberately boring: if the credential already
exists the command reports that and changes nothing. It will not quietly
reset a password or re-grant a role that an operator may have revoked on
purpose.
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from drake_api.auth.local import LOCAL_ISSUER, hash_password, normalize_email
from drake_api.rbac.catalog import seed_catalog
from drake_api.settings import get_settings

MIN_PASSWORD_LENGTH = 8


class BootstrapError(RuntimeError):
    """Something that must stop the command rather than be worked around."""


def read_password(confirm: bool = True) -> str:
    """Hidden prompt on a terminal, plain read from a pipe.

    Both paths keep the value out of argv. The piped form exists so an
    operator can drive this from a script without exporting the password
    into the environment first.
    """
    if sys.stdin.isatty():
        password = getpass.getpass("Password: ")
        if confirm and password != getpass.getpass("Confirm password: "):
            raise BootstrapError("passwords did not match")
    else:
        password = sys.stdin.readline().rstrip("\n")
    if not password:
        raise BootstrapError("password must not be empty")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise BootstrapError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


async def bootstrap_local_admin(email: str, password: str, display_name: str) -> str:
    settings = get_settings()
    normalized = normalize_email(email)
    if "@" not in normalized:
        raise BootstrapError("email must contain @")

    # Hashed before the transaction opens, so the plaintext lives for as
    # short a time as possible and never inside a database session.
    password_hash = hash_password(password)

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as connection:
            await seed_catalog(connection)

            existing = (
                await connection.execute(
                    text(
                        "SELECT identity_id FROM local_credentials WHERE email_normalized = :email"
                    ),
                    {"email": normalized},
                )
            ).first()
            if existing is not None:
                # Refuse rather than reset. A silent password change here
                # would be indistinguishable from an attacker with shell
                # access re-running the command.
                return (
                    f"identity={existing[0]} credential=already_exists "
                    "unchanged=true (delete the credential first to replace it)"
                )

            identity_row = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO identities (issuer, subject, display_name, email)
                        VALUES (:issuer, :subject, :display_name, :email)
                        ON CONFLICT (issuer, subject) DO UPDATE
                        SET display_name = EXCLUDED.display_name,
                            email = EXCLUDED.email
                        RETURNING id
                        """
                    ),
                    {
                        "issuer": LOCAL_ISSUER,
                        "subject": normalized,
                        "display_name": display_name,
                        "email": normalized,
                    },
                )
            ).first()
            if identity_row is None:
                raise BootstrapError("could not create the identity")
            identity_id = identity_row[0]

            await connection.execute(
                text(
                    """
                    INSERT INTO local_credentials (identity_id, email_normalized, password_hash)
                    VALUES (:identity_id, :email, :password_hash)
                    """
                ),
                {
                    "identity_id": identity_id,
                    "email": normalized,
                    "password_hash": password_hash,
                },
            )

            # The same grant path every other administrator gets.
            granted = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO grants (identity_id, role_id, scope_id, created_by)
                        SELECT :identity_id, r.id, s.id, :identity_id
                        FROM roles r, scopes s
                        WHERE r.name = 'Platform Owner'
                          AND s.scope_type = 'organization' AND s.external_ref = 'root'
                          AND NOT EXISTS (
                            SELECT 1 FROM grants g
                            WHERE g.identity_id = :identity_id AND g.role_id = r.id
                              AND g.scope_id = s.id AND g.revoked_at IS NULL
                          )
                        RETURNING id
                        """
                    ),
                    {"identity_id": identity_id},
                )
            ).first()

            # Audited without the password and without the hash.
            await connection.execute(
                text(
                    """
                    INSERT INTO audit_events
                        (actor_type, actor_id, action, result, target_type, target_id,
                         correlation_id, metadata, schema_version)
                    VALUES
                        ('system', 'bootstrap', 'auth.local.bootstrap_admin', 'success',
                         'identity', :identity_id, 'bootstrap',
                         jsonb_build_object('email', CAST(:email AS text), 'method', 'local'), 1)
                    """
                ),
                {"identity_id": str(identity_id), "email": normalized},
            )

            return (
                f"identity={identity_id} credential=created "
                f"platform_owner_grant={'created' if granted is not None else 'already_present'}"
            )
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the first local administrator")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", default="Drake Administrator")
    parser.add_argument(
        "--no-confirm",
        action="store_true",
        help="skip the confirmation prompt (ignored when reading from a pipe)",
    )
    args = parser.parse_args()

    try:
        password = read_password(confirm=not args.no_confirm)
        result = asyncio.run(bootstrap_local_admin(args.email, password, args.display_name))
    except BootstrapError as error:
        # The message never contains the password.
        sys.stderr.write(f"bootstrap failed: {error}\n")
        return 1
    finally:
        password = ""  # drop the plaintext as soon as it is unused

    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
