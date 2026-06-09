"""config package: unified entry point for loading YAML configuration.

设计原则：
1. 配置文件变化后自动失效缓存，避免“改了配置但未生效”。
2. 配置读取失败时 fail-closed，不能悄悄沿用旧配置。
3. 模块级缓存使用线程锁保护，避免并发读写竞态。
4. 返回配置副本，避免调用方误改缓存对象。
"""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent

_ALLOWED_SUFFIXES = {".yaml", ".yml"}

_cache_lock = threading.RLock()


@dataclass(frozen=True)
class _FileSignature:
    """用于判断配置文件是否发生变化。"""

    mtime_ns: int
    size: int


@dataclass(frozen=True)
class _CacheEntry:
    """缓存条目。"""

    signature: _FileSignature
    data: dict[str, Any]


_cache: dict[str, _CacheEntry] = {}


class ConfigLoadError(Exception):
    """Raised when configuration file fails to load. Hard failure, non-degradable."""
    pass


def _resolve_config_path(filename: str) -> Path:
    """Resolve config path and block path traversal."""

    if not filename:
        raise ConfigLoadError("Configuration filename is empty")

    path = Path(filename)

    if path.is_absolute():
        raise ConfigLoadError(f"Absolute config path is not allowed: {filename}")

    if path.suffix not in _ALLOWED_SUFFIXES:
        raise ConfigLoadError(
            f"Unsupported configuration suffix: {filename}. "
            f"Allowed suffixes: {sorted(_ALLOWED_SUFFIXES)}"
        )

    filepath = (CONFIG_DIR / path).resolve()

    try:
        filepath.relative_to(CONFIG_DIR)
    except ValueError as exc:
        raise ConfigLoadError(f"Config path traversal is not allowed: {filename}") from exc

    return filepath


def _get_file_signature(filepath: Path) -> _FileSignature:
    """Return file signature for cache invalidation."""

    try:
        stat_result = filepath.stat()
    except FileNotFoundError as exc:
        raise ConfigLoadError(f"Configuration file not found: {filepath}") from exc
    except PermissionError as exc:
        raise ConfigLoadError(f"Permission denied for configuration file: {filepath}") from exc
    except OSError as exc:
        raise ConfigLoadError(f"Cannot stat configuration file: {filepath}\n{exc}") from exc

    return _FileSignature(
        mtime_ns=stat_result.st_mtime_ns,
        size=stat_result.st_size,
    )


def _read_yaml_file(filepath: Path) -> dict[str, Any]:
    """Read and parse a YAML file.

    使用 utf-8-sig 是为了兼容带 BOM 的 UTF-8 文件。
    不尝试自动 fallback 到 GBK，避免编码不一致导致配置内容被误读。
    """

    try:
        with open(filepath, "r", encoding="utf-8-sig") as f:
            data = yaml.safe_load(f)
    except UnicodeDecodeError as exc:
        raise ConfigLoadError(
            f"Configuration file encoding error: {filepath}. "
            f"Please save it as UTF-8 or UTF-8 with BOM.\n{exc}"
        ) from exc
    except PermissionError as exc:
        raise ConfigLoadError(f"Permission denied for configuration file: {filepath}") from exc
    except FileNotFoundError as exc:
        raise ConfigLoadError(f"Configuration file not found: {filepath}") from exc
    except yaml.YAMLError as exc:
        raise ConfigLoadError(f"Configuration file format error: {filepath}\n{exc}") from exc
    except OSError as exc:
        raise ConfigLoadError(f"Configuration file read error: {filepath}\n{exc}") from exc

    if data is None:
        raise ConfigLoadError(f"Configuration file is empty: {filepath}")

    if not isinstance(data, dict):
        raise ConfigLoadError(f"Configuration root must be a mapping/dict: {filepath}")

    return data


def load_yaml(
    filename: str,
    *,
    use_cache: bool = True,
    force_reload: bool = False,
) -> dict[str, Any]:
    """Load a YAML file from config directory.

    Args:
        filename:
            YAML file name under config directory.
        use_cache:
            Whether to use cache. Even when True, cache is invalidated if file
            mtime or size changes.
        force_reload:
            Force rereading the file.

    Returns:
        A deep copy of the YAML mapping.

    Raises:
        ConfigLoadError:
            Any config loading failure. This is intentionally non-degradable.
    """

    filepath = _resolve_config_path(filename)
    signature = _get_file_signature(filepath)
    cache_key = str(filepath)

    with _cache_lock:
        cached = _cache.get(cache_key)

        if (
            use_cache
            and not force_reload
            and cached is not None
            and cached.signature == signature
        ):
            return copy.deepcopy(cached.data)

        data = _read_yaml_file(filepath)

        if use_cache:
            _cache[cache_key] = _CacheEntry(
                signature=signature,
                data=data,
            )

        return copy.deepcopy(data)


def clear_config_cache(filename: str | None = None) -> None:
    """Clear config cache.

    Args:
        filename:
            If provided, only clear that config file. Otherwise clear all.
    """

    with _cache_lock:
        if filename is None:
            _cache.clear()
            return

        filepath = _resolve_config_path(filename)
        _cache.pop(str(filepath), None)


def reload_yaml(filename: str) -> dict[str, Any]:
    """Force reload one YAML config file."""

    return load_yaml(filename, use_cache=True, force_reload=True)


def get_time_windows() -> dict[str, Any]:
    return load_yaml("time_windows.yaml")


def get_pre_trade_checks() -> dict[str, Any]:
    return load_yaml("pre_trade_checks.yaml")


def get_execution_rules() -> dict[str, Any]:
    return load_yaml("execution_rules.yaml")


def get_broker_capabilities() -> dict[str, Any]:
    return load_yaml("broker_capabilities.yaml")


def get_portfolio_source() -> dict[str, Any]:
    return load_yaml("portfolio_source.yaml")


def get_monitor_rules() -> dict[str, Any]:
    return load_yaml("monitor_rules.yaml")


def get_order_schema() -> dict[str, Any]:
    return load_yaml("order_schema.yaml")


def get_state_machine() -> dict[str, Any]:
    return load_yaml("state_machine.yaml")


def get_reservation_rules() -> dict[str, Any]:
    return load_yaml("reservation_rules.yaml")


def get_degradation_recalibration() -> dict[str, Any]:
    return load_yaml("degradation_recalibration.yaml")


def get_audit_policy() -> dict[str, Any]:
    return load_yaml("audit_policy.yaml")


def get_reconcile_rules() -> dict[str, Any]:
    return load_yaml("reconcile_rules.yaml")


def get_runtime() -> dict[str, Any]:
    return load_yaml("runtime.yaml")


def get_blacklist() -> dict[str, Any]:
    return load_yaml("blacklist.yaml")