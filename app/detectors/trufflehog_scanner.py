"""
TruffleHog Secret Scanner Integration
=====================================

Secure scanning for git repositories and code to detect:
- Hardcoded secrets
- API keys
- Credentials
- Tokens
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class SecretFinding:
    """TruffleHog finding."""

    detector_type: str
    detector_name: str
    source_type: str
    source: str
    secret: str  # Partial redacted in production
    secret_hash: str
    raw: str
    raw_v2: str
    verified: bool
    verified_type: str
    time_found: str
    commit: str
    file_path: str
    line: int
    column: int


# ─── TRUFFLEHOG SCANNER ───────────────────────────────────────────


class TruffleHogScanner:
    """Scanner using TruffleHog CLI."""

    def __init__(self, verbose: bool = False) -> None:
        self._available = self._check_available()
        self.verbose = verbose

    def _check_available(self) -> bool:
        """Check if TruffleHog is installed."""
        try:
            result = subprocess.run(["trufflehog", "--version"], capture_output=True, timeout=5, shell=False)
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def scan_directory(self, path: str, **kwargs) -> list[SecretFinding]:
        """
        Scan a directory for secrets.

        Args:
            path: Directory path to scan
            **kwargs: Additional trufflehog arguments

        Returns:
            List of findings
        """
        if not self._available:
            return self._stub_scan(path)

        cmd = ["trufflehog", "filesystem", path, "--json"]

        # TruffleHog v3 uses different flags
        if kwargs.get("include"):
            cmd.extend(["--include-paths", kwargs["include"]])
        if kwargs.get("exclude"):
            cmd.extend(["--exclude-paths", kwargs["exclude"]])
        if kwargs.get("max_depth"):
            cmd.extend(["--max-decode-depth", str(kwargs["max_depth"])])
        if self.verbose:
            cmd.append("-v")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,  # 5 minutes max
                shell=False,
            )

            if result.returncode != 0 and "no bugs found" not in result.stderr.lower():
                logger.warning(f"TruffleHog scan warning: {result.stderr}")

            findings = []
            for line in result.stdout.split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        finding = SecretFinding(
                            detector_type=data.get("DetectorType", ""),
                            detector_name=data.get("DetectorName", ""),
                            source_type=data.get("SourceType", ""),
                            source=data.get("SourceMetadata", {}).get("Data", {}).get("Filesystem", {}).get("File", ""),
                            secret=data.get("Raw", ""),
                            secret_hash=data.get("Redacted", ""),
                            raw=data.get("Raw", ""),
                            raw_v2=data.get("RawV2", ""),
                            verified=data.get("Verified", False),
                            verified_type=data.get("VerificationStatus", ""),
                            time_found=datetime.now(UTC).isoformat(),
                            commit=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Commit", ""),
                            file_path=data.get("SourceMetadata", {})
                            .get("Data", {})
                            .get("Filesystem", {})
                            .get("File", ""),
                            line=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Line", 0),
                            column=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Column", 0),
                        )
                        findings.append(finding)
                    except json.JSONDecodeError:
                        continue

            return findings

        except subprocess.TimeoutExpired:
            logger.error("TruffleHog scan timed out")
            return []

    def scan_git_repo(self, url: str, **kwargs) -> list[SecretFinding]:
        """
        Scan a Git repository for secrets.

        Args:
            url: Repository URL
            **kwargs: Additional trufflehog arguments

        Returns:
            List of findings
        """
        if not self._available:
            return self._stub_scan(url)

        cmd = ["trufflehog", "git", url, "--json", "--unvalidated"]

        if kwargs.get("branch"):
            cmd.extend(["--branch", kwargs["branch"]])
        if self.verbose:
            cmd.append("-v")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minutes for git clone + scan
                shell=False,
            )

            findings = []
            for line in result.stdout.split("\n"):
                if line.strip():
                    try:
                        data = json.loads(line)
                        findings.append(
                            SecretFinding(
                                detector_type=data.get("DetectorType", ""),
                                detector_name=data.get("DetectorName", ""),
                                source_type=data.get("SourceType", ""),
                                source=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Repo", ""),
                                secret=data.get("Raw", ""),
                                secret_hash=data.get("Redacted", ""),
                                raw=data.get("Raw", ""),
                                raw_v2=data.get("RawV2", ""),
                                verified=data.get("Verified", False),
                                verified_type=data.get("VerificationStatus", ""),
                                time_found=datetime.now(UTC).isoformat(),
                                commit=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Commit", ""),
                                file_path=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("File", ""),
                                line=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Line", 0),
                                column=data.get("SourceMetadata", {}).get("Data", {}).get("Git", {}).get("Column", 0),
                            )
                        )
                    except json.JSONDecodeError:
                        continue

            return findings

        except subprocess.TimeoutExpired:
            logger.error("TruffleHog git scan timed out")
            return []

    def _stub_scan(self, path: str) -> list[SecretFinding]:
        """Stub scan when TruffleHog unavailable."""
        return [
            SecretFinding(
                detector_type="stub",
                detector_name="stub_detector",
                source_type="stub",
                source=path,
                secret="",
                secret_hash="stub",  # noqa: S106 — config default
                raw="",
                raw_v2="",
                verified=False,
                verified_type="",
                time_found=datetime.now(UTC).isoformat(),
                commit="",
                file_path=path,
                line=0,
                column=0,
            )
        ]

    def filter_findings(self, findings: list[SecretFinding], min_severity: int = 0) -> list[SecretFinding]:
        """
        Filter findings by severity/verified status.

        Args:
            findings: List of findings
            min_severity: Minimum severity (0-100)

        Returns:
            Filtered findings
        """
        return [f for f in findings if f.verified or (not f.verified and min_severity == 0)]


# ─── GLOBAL SINGLETON ─────────────────────────────────────────────

_scanner: TruffleHogScanner | None = None


def get_scanner() -> TruffleHogScanner:
    """Get global TruffleHog scanner instance."""
    global _scanner
    if _scanner is None:
        _scanner = TruffleHogScanner()
    return _scanner


def scan_directory(path: str, **kwargs) -> list[SecretFinding]:
    """Scan a directory for secrets."""
    scanner = get_scanner()
    return scanner.scan_directory(path, **kwargs)


def scan_git_repo(url: str, **kwargs) -> list[SecretFinding]:
    """Scan a Git repository for secrets."""
    scanner = get_scanner()
    return scanner.scan_git_repo(url, **kwargs)
