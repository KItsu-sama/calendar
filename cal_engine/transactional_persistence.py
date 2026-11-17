"""Coordinated multi-file persistence with atomic commits, snapshots, rollback, and recovery.

Related files (``calendar.json``, ``groups_schedules.json``, ``profile.json``,
``relations.json``, ``*.snapshot.json`` and ``cal_changes.jsonl`` are updated
through a single transaction boundary. A multi-file operation either commits
completely or rolls back cleanly, so a crash part-way through can never leave
the group configuration and the calendar data representing different versions
of the timetable.

The store:

* validates staged content *before* any file is written,
* snapshots the current content of every affected file,
* writes every staged file atomically (temp file + :func:`os.replace`),
* records each committed file version (SHA-256 digest) in an append-only
  journal,
* rolls back every affected file to its snapshot when a transaction fails,
* recovers transactions left ``in_progress`` by an interrupted process.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .transactions import canonical_json, utc_timestamp


_PERSISTENCE_DIR = ".cal_txn"
_JOURNAL_NAME = "journal.jsonl"
_SNAPSHOT_INDEX_NAME = "snapshot_index.json"

_STATUS_IN_PROGRESS = "in_progress"
_STATUS_COMMITTED = "committed"
_STATUS_ROLLED_BACK = "rolled_back"
_STATUS_RECOVERED = "recovered"

_VALID_STATUSES = frozenset({
    _STATUS_IN_PROGRESS,
    _STATUS_COMMITTED,
    _STATUS_ROLLED_BACK,
    _STATUS_RECOVERED,
})


class PersistenceError(ValueError):
    """A coordinated persistence operation failed."""


class ValidationError(PersistenceError):
    """Staged content failed validation before commit."""


class TransactionError(PersistenceError):
    """The transaction journal or a transaction record is invalid."""


class RecoveryError(PersistenceError):
    """An interrupted transaction could not be recovered."""


@dataclass(frozen=True)
class FileVersion:
    """A committed version of one file."""

    path: str
    digest: str
    size: int
    committed_at: str


@dataclass
class TransactionRecord:
    transaction_id: str
    timestamp: str
    status: str
    files: dict[str, FileVersion] = field(default_factory=dict)
    snapshot_dir: str = ""
    error: Optional[str] = None
    description: Optional[str] = None

    @property
    def committed(self) -> bool:
        return self.status == _STATUS_COMMITTED

    def to_dict(self) -> dict:
        return {
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "files": {
                path: {
                    "digest": version.digest,
                    "size": version.size,
                    "committed_at": version.committed_at,
                }
                for path, version in self.files.items()
            },
            "snapshot_dir": self.snapshot_dir,
            "error": self.error,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TransactionRecord":
        status = data.get("status")
        if status not in _VALID_STATUSES:
            raise TransactionError(f"Unknown transaction status: {status!r}")
        return cls(
            transaction_id=data["transaction_id"],
            timestamp=data["timestamp"],
            status=status,
            files={
                path: FileVersion(
                    path=path,
                    digest=item["digest"],
                    size=item["size"],
                    committed_at=item["committed_at"],
                )
                for path, item in data.get("files", {}).items()
            },
            snapshot_dir=data.get("snapshot_dir", ""),
            error=data.get("error"),
            description=data.get("description"),
        )


class CoordinatedStore:
    """Transaction boundary across multiple related files.

    Stage file writes with :meth:`stage`, validate them, then :meth:`commit`
    writes every staged file atomically and records each committed version in
    an append-only journal. On failure, :meth:`rollback` restores every
    affected file from its pre-transaction snapshot. :meth:`recover` repairs
    transactions left ``in_progress`` by an interrupted process.

    The store doubles as a context manager: it commits on a clean exit and
    rolls back when an exception propagates.
    """

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        journal_path: str | Path | None = None,
        snapshot_root: str | Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.journal_path = (
            Path(journal_path).resolve()
            if journal_path is not None
            else self.workspace_root / _PERSISTENCE_DIR / _JOURNAL_NAME
        )
        self.snapshot_root = (
            Path(snapshot_root).resolve()
            if snapshot_root is not None
            else self.workspace_root / _PERSISTENCE_DIR
        )
        self._active_id: Optional[str] = None
        self._staged: dict[
            str, tuple[bytes, Optional[object], Optional[Callable[[object], None]]]
        ] = {}
        self._snapshot_index: dict[str, dict] = {}
        self._txn_dir: Optional[Path] = None

    # ------------------------------------------------------------------
    # transaction lifecycle
    # ------------------------------------------------------------------
    def is_active(self) -> bool:
        """Return True while a transaction has been begun and not completed."""
        return self._active_id is not None

    def begin(self, description: Optional[str] = None) -> str:
        """Start a new transaction and return its id.

        The transaction is journaled as ``in_progress`` immediately so an
        interrupted process can be recovered later.
        """
        if self._active_id is not None:
            raise TransactionError(
                f"Transaction '{self._active_id}' is already active; "
                "commit or rollback first."
            )
        transaction_id = f"txn_{uuid.uuid4().hex}"
        self._txn_dir = self.snapshot_root / transaction_id
        self._txn_dir.mkdir(parents=True, exist_ok=True)
        self._active_id = transaction_id
        self._staged = {}
        self._snapshot_index = {}
        record = TransactionRecord(
            transaction_id=transaction_id,
            timestamp=utc_timestamp(),
            status=_STATUS_IN_PROGRESS,
            snapshot_dir=str(self._txn_dir),
            description=description,
        )
        self._append_journal(record)
        return transaction_id

    def commit(self) -> TransactionRecord:
        """Validate, write every staged file atomically, and record the commit.

        Validation runs before any file is touched. If validation or any write
        fails, every affected file is restored from its snapshot, the
        transaction is journaled as ``rolled_back``, and the original error is
        re-raised.
        """
        if self._active_id is None:
            raise TransactionError("No active transaction to commit.")
        transaction_id = self._active_id
        txn_dir = self._txn_dir
        assert txn_dir is not None
        try:
            self._validate_staged()
            # Snapshot the current content of every affected file before writing
            # so an interrupted commit can be rolled back.
            for key in self._staged:
                self._snapshot_file(key)
            # Persist the snapshot index now so recovery can restore files even
            # if the process dies before the commit is journaled.
            (txn_dir / _SNAPSHOT_INDEX_NAME).write_text(
                json.dumps(self._snapshot_index, indent=2) + "\n", encoding="utf-8"
            )
            versions: dict[str, FileVersion] = {}
            now = utc_timestamp()
            for key, (data, _parsed, _validator) in self._staged.items():
                fs_path = self._resolve(key)
                self._atomic_write(fs_path, data)
                versions[key] = FileVersion(
                    path=key,
                    digest=hashlib.sha256(data).hexdigest(),
                    size=len(data),
                    committed_at=now,
                )
            record = TransactionRecord(
                transaction_id=transaction_id,
                timestamp=now,
                status=_STATUS_COMMITTED,
                files=versions,
                snapshot_dir=str(txn_dir),
            )
            self._append_journal(record)
            return record
        except BaseException as error:
            try:
                self._rollback_files()
            except BaseException as restore_error:
                raise RecoveryError(
                    f"Transaction '{transaction_id}' failed and rollback also "
                    f"failed: {restore_error}"
                ) from error
            try:
                rollback_record = TransactionRecord(
                    transaction_id=transaction_id,
                    timestamp=utc_timestamp(),
                    status=_STATUS_ROLLED_BACK,
                    error=str(error),
                    snapshot_dir=str(txn_dir),
                )
                self._append_journal(rollback_record)
            except BaseException:
                # Journal append failure masks the original error; keep it.
                pass
            raise
        finally:
            self._clear_active()
            if self._txn_dir is not None:
                shutil.rmtree(self._txn_dir, ignore_errors=True)
                self._txn_dir = None

    def rollback(self) -> None:
        """Restore every staged file from its snapshot and journal the rollback."""
        if self._active_id is None:
            raise TransactionError("No active transaction to roll back.")
        transaction_id = self._active_id
        txn_dir = self._txn_dir
        assert txn_dir is not None
        try:
            self._rollback_files()
            record = TransactionRecord(
                transaction_id=transaction_id,
                timestamp=utc_timestamp(),
                status=_STATUS_ROLLED_BACK,
                snapshot_dir=str(txn_dir),
            )
            self._append_journal(record)
        finally:
            self._clear_active()
            shutil.rmtree(txn_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # staging / validation
    # ------------------------------------------------------------------
    def stage(
        self,
        path: str | Path,
        content: object,
        validator: Optional[Callable[[object], None]] = None,
        *,
        as_json: bool = True,
    ) -> None:
        """Stage a file for writing in the active transaction.

        ``content`` is serialized (canonical JSON when ``as_json`` is true,
        otherwise raw bytes/str). ``validator``, when given, is called with the
        parsed content at commit time and must raise on invalid input.
        """
        if self._active_id is None:
            raise TransactionError("No active transaction; call begin() before staging.")
        key = self._key(path)
        data, parsed = self._serialize(content, as_json=as_json)
        self._staged[key] = (data, parsed, validator)

    def validate(self) -> None:
        """Run every staged validator immediately (fail-fast)."""
        if self._active_id is None:
            raise TransactionError("No active transaction to validate.")
        self._validate_staged()

    # ------------------------------------------------------------------
    # context manager
    # ------------------------------------------------------------------
    def __enter__(self) -> "CoordinatedStore":
        self.begin()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if not self.is_active():
            return False
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        return False

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------
    def current_version(self, path: str | Path) -> Optional[FileVersion]:
        """Return the latest committed version of ``path``, or ``None``."""
        key = self._key(path)
        latest: Optional[FileVersion] = None
        for record in self._read_journal():
            if record.status in {_STATUS_COMMITTED, _STATUS_RECOVERED} and key in record.files:
                latest = record.files[key]
        return latest

    def file_history(self, path: str | Path) -> list[TransactionRecord]:
        """Return every committed transaction that touched ``path``."""
        key = self._key(path)
        return [
            record
            for record in self._read_journal()
            if record.status in {_STATUS_COMMITTED, _STATUS_RECOVERED} and key in record.files
        ]

    def recover(self) -> list[TransactionRecord]:
        """Repair transactions left ``in_progress`` by an interrupted process.

        For each interrupted transaction, every affected file is restored from
        its pre-transaction snapshot. The transaction is then journaled as
        ``recovered``.         Returns the list of recovered transaction records.
        """
        latest: dict[str, TransactionRecord] = {}
        for record in self._read_journal():
            latest[record.transaction_id] = record

        recovered_list: list[TransactionRecord] = []
        for transaction_id, record in latest.items():
            if record.status != _STATUS_IN_PROGRESS:
                continue
            txn_dir = (
                Path(record.snapshot_dir)
                if record.snapshot_dir
                else self.snapshot_root / transaction_id
            )
            index_path = txn_dir / _SNAPSHOT_INDEX_NAME
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError as error:
                    raise RecoveryError(
                        f"Corrupt snapshot index for '{transaction_id}': {error}"
                    ) from None
                for key, entry in index.items():
                    fs_path = self._resolve(key)
                    snap_path = txn_dir / entry["file"]
                    if not entry.get("existed", False):
                        if fs_path.exists():
                            fs_path.unlink()
                        continue
                    data = snap_path.read_bytes()
                    fs_path.parent.mkdir(parents=True, exist_ok=True)
                    fs_path.write_bytes(data)
            recovered_record = TransactionRecord(
                transaction_id=transaction_id,
                timestamp=utc_timestamp(),
                status=_STATUS_RECOVERED,
                error="Interrupted transaction rolled back by recovery.",
                snapshot_dir=str(txn_dir) if txn_dir.exists() else "",
            )
            self._append_journal(recovered_record)
            recovered_list.append(recovered_record)
        return recovered_list

    # ------------------------------------------------------------------
    # convenience: existing cal_engine types
    # ------------------------------------------------------------------
    def stage_calendar_state(
        self,
        state: object,
        path: str | Path,
        validator: Optional[Callable[[object], None]] = None,
    ) -> None:
        """Stage a :class:`~cal_engine.storage.CalendarState` as version-1 JSON."""
        from .storage import CalendarState, StorageError

        if not isinstance(state, CalendarState):
            raise TypeError("stage_calendar_state expects a CalendarState instance")

        def _validate_calendar(data: object) -> None:
            try:
                CalendarState.from_dict(data)
            except StorageError as error:
                raise ValidationError(str(error)) from None

        self.stage(path, state.to_dict(), validator=validator or _validate_calendar, as_json=True)

    def stage_group_config(
        self,
        config: dict,
        path: str | Path,
        validator: Optional[Callable[[object], None]] = None,
    ) -> None:
        """Stage a group/schedule configuration JSON, validated with ``validate_config``."""
        from .group_schedule import validate_config

        if not isinstance(config, dict):
            raise TypeError("stage_group_config expects a dict")

        def _validate_config(data: object) -> None:
            try:
                validate_config(data)
            except ValueError as error:
                raise ValidationError(str(error)) from None

        self.stage(path, config, validator=validator or _validate_config, as_json=True)

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _validate_staged(self) -> None:
        for key, (_data, parsed, validator) in self._staged.items():
            if validator is None:
                continue
            try:
                validator(parsed)
            except Exception as error:
                raise ValidationError(f"Validation failed for '{key}': {error}") from error

    @staticmethod
    def _serialize(
        content: object, as_json: bool
    ) -> tuple[bytes, Optional[object]]:
        if as_json:
            text = canonical_json(content)
            return text.encode("utf-8"), json.loads(text)
        if isinstance(content, bytes):
            return content, None
        return str(content).encode("utf-8"), None

    def _key(self, path: str | Path) -> str:
        p = Path(path)
        if p.is_absolute():
            try:
                return str(p.resolve().relative_to(self.workspace_root))
            except ValueError:
                return str(p.resolve())
        return str(p)

    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if p.is_absolute():
            return p
        return self.workspace_root / p

    def _snapshot_file(self, key: str) -> None:
        assert self._txn_dir is not None
        fs_path = self._resolve(key)
        existed = fs_path.exists()
        data = fs_path.read_bytes() if existed else None
        original_digest = hashlib.sha256(data).hexdigest() if data is not None else None
        snap_name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".data"
        snap_path = self._txn_dir / snap_name
        if data is not None:
            snap_path.write_bytes(data)
        elif snap_path.exists():
            snap_path.unlink()
        self._snapshot_index[key] = {
            "file": snap_name,
            "existed": existed,
            "original_digest": original_digest,
        }

    def _rollback_files(self) -> None:
        if self._txn_dir is None:
            return
        index_path = self._txn_dir / _SNAPSHOT_INDEX_NAME
        if not index_path.exists():
            return
        self._snapshot_index = json.loads(index_path.read_text(encoding="utf-8"))
        for key, entry in self._snapshot_index.items():
            fs_path = self._resolve(key)
            snap_path = self._txn_dir / entry["file"]
            if not entry.get("existed", False):
                if fs_path.exists():
                    fs_path.unlink()
                continue
            data = snap_path.read_bytes()
            fs_path.parent.mkdir(parents=True, exist_ok=True)
            fs_path.write_bytes(data)

    @staticmethod
    def _atomic_write(fs_path: Path, data: bytes) -> None:
        fs_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(fs_path.parent), prefix=".tmp_", suffix=".txn")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp_name, fs_path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _append_journal(self, record: TransactionRecord) -> None:
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self.journal_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")

    def _read_journal(self) -> list[TransactionRecord]:
        if not self.journal_path.exists():
            return []
        records: list[TransactionRecord] = []
        for number, line in enumerate(
            self.journal_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                records.append(TransactionRecord.from_dict(json.loads(line)))
            except (json.JSONDecodeError, TransactionError, KeyError, ValueError) as error:
                raise TransactionError(f"Invalid journal line {number}: {error}") from None
        return records

    def _clear_active(self) -> None:
        self._active_id = None
        self._staged = {}
        self._snapshot_index = {}
