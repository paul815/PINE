from __future__ import annotations

import os
from pathlib import Path


def _repo_root(root_dir: str | None = None) -> Path:
    return Path(root_dir) if root_dir else Path(__file__).resolve().parents[3]


def onboarding_complete_flag_path(root_dir: str | None = None) -> Path:
    return _repo_root(root_dir) / 'backend' / 'data' / 'onboarding_complete.flag'


def mark_onboarding_complete(root_dir: str | None = None) -> None:
    flag_path = onboarding_complete_flag_path(root_dir)
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text('true\n', encoding='utf-8')


def clear_onboarding_complete(root_dir: str | None = None) -> None:
    flag_path = onboarding_complete_flag_path(root_dir)
    try:
        flag_path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return
