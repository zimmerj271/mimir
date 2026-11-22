"""Local administrator entry point. Run inside the private deployment network."""

import argparse
import asyncio
import getpass
import os
import stat
import sys
import warnings
from pathlib import Path
from uuid import UUID

from sqlalchemy import URL

from mimir_backend.auth import (
    AccountAlreadyExists,
    AuthStorageError,
    InvalidAuthInput,
    PasswordService,
    ProvisioningService,
)
from mimir_backend.auth_postgres import AuthDatabase, PostgresProvisioningRepository


class MaintenanceCommand:
    @staticmethod
    def read_secret(path: Path) -> str:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
                or info.st_uid != os.geteuid()
                or not 32 <= info.st_size <= 4096
            ):
                raise InvalidAuthInput("Unsafe database credential file")
            with os.fdopen(descriptor, closefd=False) as stream:
                value = stream.read(4097).rstrip("\n")
            if len(value) < 32 or any(char in value for char in "\r\n\x00"):
                raise InvalidAuthInput("Invalid database credential file")
            return value
        finally:
            os.close(descriptor)

    def parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            description="Restricted Mimir local provisioning"
        )
        parser.add_argument("--host", default="postgres")
        parser.add_argument("--port", type=int, default=5432)
        parser.add_argument("--database", default="mimir")
        parser.add_argument("--credential-file", type=Path, required=True)
        commands = parser.add_subparsers(dest="operation", required=True)
        create = commands.add_parser("create-user")
        create.add_argument("--username", required=True)
        create.add_argument("--display-name")
        disable = commands.add_parser("disable-user")
        disable.add_argument("--user-id", type=UUID, required=True)
        return parser

    async def run(self, args: argparse.Namespace) -> int:
        database = AuthDatabase(
            URL.create(
                "postgresql+psycopg",
                username="mimir_maintenance",
                password=self.read_secret(args.credential_file),
                host=args.host,
                port=args.port,
                database=args.database,
            )
        )
        try:
            service = ProvisioningService(
                PostgresProvisioningRepository(database), PasswordService()
            )
            if args.operation == "create-user":
                with warnings.catch_warnings():
                    warnings.simplefilter("error", getpass.GetPassWarning)
                    try:
                        password = getpass.getpass("New account password: ")
                        if password != getpass.getpass("Confirm password: "):
                            raise InvalidAuthInput("Passwords do not match")
                    except getpass.GetPassWarning:
                        raise InvalidAuthInput(
                            "A non-echoing terminal is required"
                        ) from None
                user_id = await service.provision(
                    args.username, password, args.display_name
                )
                print(f"Provisioned user {user_id}")
            elif await service.disable(args.user_id):
                print("Account disabled; all sessions revoked")
            else:
                print("Account not found", file=sys.stderr)
                return 1
            return 0
        finally:
            await database.close()

    def main(self) -> int:
        args = self.parser().parse_args()
        try:
            return asyncio.run(self.run(args))
        except (InvalidAuthInput, AccountAlreadyExists, AuthStorageError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except OSError, UnicodeError, EOFError:
            print("Unable to read administrator input or credentials", file=sys.stderr)
            return 1


def main() -> int:
    # Python module entry point is the narrow standalone-function exception.
    return MaintenanceCommand().main()


if __name__ == "__main__":
    sys.exit(main())
