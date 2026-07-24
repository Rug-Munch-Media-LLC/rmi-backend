"""CQRS — light command/query separation for the RMI Backend.

CQRS (Command-Query Responsibility Segregation) separates operations that
change state (commands) from operations that read state (queries). This
makes the system easier to reason about, test, and optimise independently.

Principles
──────────
1. **Commands** return result/error, never data.
   - ``CreateWalletLabelCommand`` → ``CommandResult(success=True)``
2. **Queries** return data, never mutate state.
   - ``GetWalletLabelsQuery`` → ``list[WalletLabel]``
3. **Handlers** are single-purpose classes.
   - One class per command or query.
4. **Mediator** dispatches commands/queries to their handlers.
   - No direct handler instantiation in callers.

When to use
───────────
Use CQRS for operations that benefit from explicit read/write separation:
  - Wallet label mutations vs. lookups
  - Alert subscription create/delete vs. list
  - Token scan triggering (command) vs. retrieving results (query)

When NOT to use
───────────────
CQRS is overkill for simple CRUD that maps directly to a single repository
call. The ``BaseService`` pattern is sufficient for those cases.
  - Simple get/list/create/update/delete → use BaseService
  - Complex business logic with multiple side effects → use CQRS
  - Operations that need audit logging, validation, and async propagation → use CQRS

Usage
─────
  Define a command::

      from dataclasses import dataclass
      from app.core.cqrs import Command, CommandResult, CommandHandler

      @dataclass
      class RescanTokenCommand(Command):
          token_address: str
          chain: str
          force: bool = False

      class RescanTokenHandler(CommandHandler[RescanTokenCommand]):
          async def handle(self, cmd: RescanTokenCommand) -> CommandResult:
              ...

  Dispatch::

      from app.core.cqrs import get_mediator

      result = await get_mediator().execute(RescanTokenCommand(token_address="...", chain="solana"))
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Base types ────────────────────────────────────────────────────────────────


@dataclass
class Command:
    """A request to change system state.

    Commands are named with a verb + noun (``RescanToken``, ``CancelAlert``).
    Fields are the parameters needed to execute the command.
    """


@dataclass
class Query:
    """A request to read system state.

    Queries are named with a noun + qualifier (``TokenDetail``, ``AlertList``).
    Fields are filter/selection parameters.
    """


@dataclass
class CommandResult:
    """Returned by every command handler.

    - ``success``: True if the command completed without error.
    - ``result``: Optional data (e.g. created entity ID).
    - ``error``: Error message if ``success`` is False.
    """

    success: bool = True
    result: Any = None
    error: str = ""


# ── Type variables ────────────────────────────────────────────────────────────

C = TypeVar("C", bound=Command)
Q = TypeVar("Q", bound=Query)
R = TypeVar("R")


# ── Handler interfaces ───────────────────────────────────────────────────────


class CommandHandler(ABC, Generic[C]):
    """Handle one command type. MUST be stateless."""

    @abstractmethod
    async def handle(self, command: C) -> CommandResult:
        """Execute the command. Return a CommandResult."""
        ...


class QueryHandler(ABC, Generic[Q, R]):
    """Handle one query type. MUST be stateless."""

    @abstractmethod
    async def handle(self, query: Q) -> R:
        """Execute the query. Return data."""
        ...


# ── Mediator ─────────────────────────────────────────────────────────────────


class Mediator:
    """Dispatches commands and queries to their registered handlers.

    Register handlers at startup::

        mediator = Mediator()
        mediator.register(RescanTokenCommand, RescanTokenHandler())

    Call from anywhere::

        result = await mediator.execute(RescanTokenCommand(token_address="..."))
    """

    def __init__(self) -> None:
        self._command_handlers: dict[type[Command], CommandHandler[Any]] = {}
        self._query_handlers: dict[type[Query], QueryHandler[Any, Any]] = {}

    # ── Registration ─────────────────────────────────────────────────────

    def register(
        self,
        handler: CommandHandler[C] | QueryHandler[Q, R],
    ) -> None:
        """Register a handler inferred from its generic type.

        The handler's type argument is inferred from the class hierarchy.
        """
        # Determine the command/query type from generic bases
        for base in type(handler).__orig_bases__ if hasattr(type(handler), "__orig_bases__") else []:
            args = getattr(base, "__args__", [])
            if args:
                arg = args[0]
                if isinstance(handler, CommandHandler):
                    self._command_handlers[arg] = handler  # type: ignore[assignment]
                    logger.debug("cmd_handler_registered", command=arg.__name__)
                elif isinstance(handler, QueryHandler):
                    self._query_handlers[arg] = handler  # type: ignore[assignment]
                    logger.debug("query_handler_registered", query=arg.__name__)
                return

        # Fallback: manual registration with explicit key
        # This path is hit when the handler class uses ``Generic[C]`` at definition
        # but Python does not preserve ``__orig_bases__`` at runtime.
        logger.warning("cqrs_handler_type_not_detected", handler=type(handler).__name__)

    def register_command(self, command_type: type[C], handler: CommandHandler[C]) -> None:
        """Explicitly register a command handler by type."""
        self._command_handlers[command_type] = handler
        logger.debug("cmd_handler_registered", command=command_type.__name__)

    def register_query(self, query_type: type[Q], handler: QueryHandler[Q, R]) -> None:
        """Explicitly register a query handler by type."""
        self._query_handlers[query_type] = handler
        logger.debug("query_handler_registered", query=query_type.__name__)

    # ── Dispatch ─────────────────────────────────────────────────────────

    async def execute(self, command: Command) -> CommandResult:
        """Execute a command through its registered handler.

        Returns ``CommandResult(success=False, error="...")`` if no handler
        is registered or if the handler raises.
        """
        handler = self._command_handlers.get(type(command))
        if handler is None:
            logger.error("cmd_no_handler", command=type(command).__name__)
            return CommandResult(
                success=False,
                error=f"No handler registered for {type(command).__name__}",
            )
        try:
            logger.debug("cmd_executing", command=type(command).__name__)
            result = await handler.handle(command)
            return result
        except Exception:
            logger.exception("cmd_execution_failed", command=type(command).__name__)
            return CommandResult(
                success=False,
                error=f"Command {type(command).__name__} failed",
            )

    async def fetch(self, query: Query) -> R | None:  # type: ignore[type-arg]
        """Execute a query through its registered handler.

        Returns ``None`` if no handler is registered.
        """
        handler = self._query_handlers.get(type(query))
        if handler is None:
            logger.error("query_no_handler", query=type(query).__name__)
            return None
        try:
            logger.debug("query_executing", query=type(query).__name__)
            return await handler.handle(query)
        except Exception:
            logger.exception("query_execution_failed", query=type(query).__name__)
            return None


# ── Singleton ────────────────────────────────────────────────────────────────

_mediator: Mediator | None = None


def get_mediator() -> Mediator:
    """Get the application-wide Mediator singleton."""
    global _mediator
    if _mediator is None:
        _mediator = Mediator()
        logger.info("cqrs_mediator_initialized")
    return _mediator


def reset_mediator() -> None:
    """Reset the Mediator singleton — used in test teardown."""
    global _mediator
    _mediator = None
