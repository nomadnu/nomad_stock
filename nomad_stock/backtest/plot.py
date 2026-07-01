"""백테스트 결과 시각화 — 자산곡선 + 낙폭(drawdown).

헤드리스(서버/CLI)에서도 동작하도록 Agg 백엔드로 PNG 저장.
한글 라벨이 깨지지 않도록 시스템에 있는 한글 폰트를 자동 선택한다.
"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")  # 화면 없이 파일로 저장
import matplotlib.pyplot as plt
import pandas as pd


def _setup_font() -> None:
    from matplotlib import font_manager

    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in ["Malgun Gothic", "AppleGothic", "NanumGothic", "DejaVu Sans"]:
        if name in available:
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False  # 음수 부호 깨짐 방지


def plot_equity(
    curves: dict[str, pd.Series],
    title: str,
    save_path: str,
    drawdown_name: str | None = None,
) -> str:
    """여러 자산곡선을 비교 플롯하고 PNG로 저장한다.

    curves : {라벨: 자산곡선 Series(실금액)}. 시작=100으로 정규화해 비교.
    drawdown_name : 아래 낙폭 패널에 그릴 곡선 이름(기본: 첫 곡선).
    반환: 저장된 파일 경로.
    """
    _setup_font()
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 7), height_ratios=[3, 1], sharex=True
    )

    for name, eq in curves.items():
        if eq is None or eq.empty:
            continue
        norm = eq / eq.iloc[0] * 100.0
        ax1.plot(norm.index, norm.values, label=name, linewidth=1.3)
    ax1.set_title(title, fontsize=13)
    ax1.set_ylabel("자산 (시작=100)")
    ax1.legend(loc="upper left", fontsize=9)
    ax1.grid(alpha=0.3)

    dd_name = drawdown_name or next(iter(curves))
    eq = curves[dd_name]
    dd = (eq / eq.cummax() - 1.0) * 100.0
    ax2.fill_between(dd.index, dd.values, 0, color="crimson", alpha=0.3)
    ax2.plot(dd.index, dd.values, color="crimson", linewidth=0.8)
    ax2.set_ylabel(f"낙폭 % ({dd_name})")
    ax2.grid(alpha=0.3)

    fig.tight_layout()
    fig.savefig(save_path, dpi=120)
    plt.close(fig)
    return save_path
